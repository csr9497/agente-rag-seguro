"""Subgrafo genérico de un agente del registro:

    agent → policy_gate → { execute_tool | human_approval | agent (con el motivo) }
                                   └──────────────┴→ sanitize_output → agent

- `agent`: LLM con tool-calling que solo ve SUS herramientas y su tarea (regla 5).
- `policy_gate`: decisión determinista en código (app/agents/policy_gate.py).
- `human_approval`: `interrupt()` con {type, agent, tool, args_preview, risk, expires_at}; se
  reanuda con Command(resume={approved, edited_args?, reason?, approver_id}). En
  confirm_user confirma el propio usuario; en approve_staff, alguien con `approver_role` que
  no sea el solicitante. Editar los argumentos no puede rebajar el nivel de aprobación.
- `sanitize_output`: la salida de la tool vuelve al LLM como dato, nunca como instrucción
  (regla 6): PII enmascarada, etiquetas neutralizadas y envuelta en <dato_herramienta>.
- Presupuestos (iteraciones y tiempo) en el estado (regla 7). Una fila de auditoría por
  llamada a tool con su decisión: allow, deny, approved o rejected.
- Ningún nodo puede modificar `user` (regla 2): `sin_tocar_usuario`.
"""

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Annotated, Any, Literal, Protocol

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from app.agents.policy_gate import DecisionGate, evaluar
from app.agents.registry import AgentSpec, Mode, ToolPolicy, resolve_mode
from app.agents.scopes import UserContext
from app.models.schemas import ToolCall
from app.rag.prompts import neutralizar
from app.retrieval.base import Supervisor
from app.security.deteccion import TIPOS_PII, enmascarar_pii

logger = logging.getLogger("audit")

VIGENCIA_APROBACION = timedelta(hours=24)
MAX_DATO = 4000  # caracteres de salida de una tool que llegan al LLM
_NIVEL: dict[Mode, int] = {"auto": 0, "confirm_user": 1, "approve_staff": 2, "deny": 3}
_ETIQUETA_DATO = re.compile(r"</?\s*dato_herramienta\b[^>]*>", re.IGNORECASE)

Decision = Literal["allow", "deny", "approved", "rejected"]


# ------------------------------------------------------------------------------ auditoría
class RegistroAuditoria(BaseModel):
    trace_id: str
    user_id: str
    agent: str
    tool: str
    args_hash: str
    decision: Decision
    approver_id: str | None = None
    reason: str | None = None
    fecha: str


class Auditoria(Protocol):
    def registrar(self, fila: RegistroAuditoria) -> None: ...


class AuditoriaMemoria:
    def __init__(self) -> None:
        self.filas: list[RegistroAuditoria] = []

    def registrar(self, fila: RegistroAuditoria) -> None:
        self.filas.append(fila)


class AuditoriaSql:
    """Tabla audit_log (solo inserción: trigger en la base, ver persistencia/repositorios.py)."""

    def __init__(self, motor: Any) -> None:
        self._motor = motor

    def registrar(self, fila: RegistroAuditoria) -> None:
        from sqlalchemy import insert

        from app.persistencia import tablas as t

        with self._motor.begin() as c:
            c.execute(insert(t.audit_log).values(**fila.model_dump()))


class AuditoriaLog:
    """Hasta que exista la tabla audit_log (fase 3): una línea JSON por decisión."""

    def registrar(self, fila: RegistroAuditoria) -> None:
        logger.info(fila.model_dump_json())


# ---------------------------------------------------------------------------------- estado
class ContextoTool(BaseModel):
    """Lo que recibe una tool: la identidad autenticada, nunca la que diga el LLM."""

    user: UserContext
    agent: str
    trace_id: str


class RespuestaAprobacion(BaseModel):
    approved: bool
    approver_id: str
    edited_args: dict[str, Any] | None = None
    reason: str | None = None


class TextoPrivado(BaseModel):
    """Texto con datos del usuario. PostgresSaver guarda los valores `str`/`int`/`bool` del
    estado en claro (columna JSONB) y solo cifra el resto: envolverlo lo lleva al almacén
    cifrado. Ningún `str` del estado puede llevar datos del usuario (test estructural)."""

    model_config = ConfigDict(frozen=True)
    valor: str

    def __str__(self) -> str:
        return self.valor


def _privado(valor: Any) -> Any:
    return TextoPrivado(valor=valor) if isinstance(valor, str) else valor


Privado = Annotated[TextoPrivado, BeforeValidator(_privado)]

# Únicos campos de texto plano del estado: no llevan datos del usuario.
CAMPOS_TEXTO_INOCUOS = frozenset({"trace_id", "expira", "aprobacion_id"})


def resumen(salida: dict[str, Any]) -> str:
    """El resumen del subagente (para el supervisor) como texto."""
    return str(salida.get("summary") or "")


class EstadoSubagente(BaseModel):
    task: Privado
    user: UserContext
    trace_id: str = ""
    mensajes: list[dict[str, Any]] = Field(default_factory=list)
    pendientes: list[ToolCall] = Field(default_factory=list)
    actual: ToolCall | None = None
    gate: DecisionGate | None = None
    expira: str | None = None  # ISO; lo fija policy_gate para que sea estable al reanudar
    aprobacion_id: str | None = None  # fila de approvals de la pausa en curso
    resultado: Any = None
    iteraciones: int = 0
    inicio: float | None = None
    limite_s: float = 120.0
    ids: list[str] = Field(default_factory=list)
    summary: Privado | None = None


def sin_tocar_usuario(nodo: Callable[..., Any]) -> Callable[..., Any]:
    """Regla 2: solo `authorize` escribe el usuario; un nodo que lo intente falla."""

    @wraps(nodo)
    def envuelto(*args: Any, **kwargs: Any) -> Any:
        cambios = nodo(*args, **kwargs)
        if isinstance(cambios, dict) and "user" in cambios:
            raise PermissionError(
                f"El nodo {getattr(nodo, '__name__', nodo)} no puede cambiar user"
            )
        return cambios

    return envuelto


# ---------------------------------------------------------------------------------- grafo
def construir_subgrafo(
    spec: AgentSpec,
    llm: Supervisor,
    auditoria: Auditoria,
    roles_de: Callable[[str], set[str]],
    checkpointer: Any = None,
    ahora: Callable[[], float] = time.time,
    aprobaciones: Any = None,
) -> Any:
    """`roles_de(user_id)`: roles de quien aprueba, desde la fuente de verdad (no del LLM).
    `checkpointer`: necesario para `interrupt` (en producción, Postgres cifrado).
    `aprobaciones`: tabla approvals (app/agents/aprobaciones.py); sin ella, solo el interrupt."""
    subgrafo = _Subgrafo(spec, llm, auditoria, roles_de, ahora, aprobaciones)
    return SubgrafoPrivado(subgrafo.compilar(checkpointer))


class SubgrafoPrivado:
    """El grafo compilado, con la entrada ya protegida: LangGraph guarda la entrada en sus
    canales antes de validarla con el modelo, así que la tarea (texto del usuario) se envuelve
    en TextoPrivado aquí, antes de que el checkpointer la vea. Lo demás se delega."""

    def __init__(self, grafo: Any) -> None:
        self._grafo = grafo

    def invoke(self, entrada: Any, *args: Any, **kwargs: Any) -> Any:
        return self._grafo.invoke(_entrada_privada(entrada), *args, **kwargs)

    def stream(self, entrada: Any, *args: Any, **kwargs: Any) -> Any:
        return self._grafo.stream(_entrada_privada(entrada), *args, **kwargs)

    def __getattr__(self, nombre: str) -> Any:
        return getattr(self._grafo, nombre)


def _entrada_privada(entrada: Any) -> Any:
    if isinstance(entrada, dict) and isinstance(entrada.get("task"), str):
        return {**entrada, "task": TextoPrivado(valor=entrada["task"])}
    return entrada  # Command(resume=…) y demás, sin cambios


class _Subgrafo:
    def __init__(
        self,
        spec: AgentSpec,
        llm: Supervisor,
        auditoria: Auditoria,
        roles_de: Callable[[str], set[str]],
        ahora: Callable[[], float],
        aprobaciones: Any = None,
    ) -> None:
        self._spec, self._llm, self._auditoria = spec, llm, auditoria
        self._roles_de, self._ahora, self._aprobaciones = roles_de, ahora, aprobaciones
        self._schemas = [_schema(nombre, p) for nombre, p in spec.tools.items()]

    def compilar(self, checkpointer: Any) -> Any:
        g = StateGraph(EstadoSubagente)
        for nombre, nodo in [
            ("agent", self._agent),
            ("policy_gate", self._policy_gate),
            ("human_approval", self._human_approval),
            ("execute_tool", self._execute_tool),
            ("sanitize_output", self._sanitize_output),
        ]:
            g.add_node(nombre, sin_tocar_usuario(nodo))
        g.add_edge(START, "agent")
        g.add_conditional_edges("agent", lambda e: END if e.summary is not None else "policy_gate")
        g.add_conditional_edges("policy_gate", self._tras_gate)
        g.add_conditional_edges(
            "human_approval",
            lambda e: "execute_tool" if e.gate and e.gate.decision == "allow" else _siguiente(e),
        )
        g.add_edge("execute_tool", "sanitize_output")
        g.add_conditional_edges("sanitize_output", _siguiente)
        return g.compile(checkpointer=checkpointer)

    # ------------------------------------------------------------------------------ nodos
    def _agent(self, estado: EstadoSubagente) -> dict[str, Any]:
        inicio = estado.inicio if estado.inicio is not None else self._ahora()
        agotado = (
            estado.iteraciones >= self._spec.max_iterations
            or self._ahora() - inicio > estado.limite_s
        )
        if agotado:
            return {
                "inicio": inicio,
                "summary": _privado(self._resumen(estado, None, agotado=True)),
            }
        mensajes = estado.mensajes or [
            {"role": "system", "content": self._spec.system_prompt},
            {"role": "user", "content": f"<tarea>\n{neutralizar(str(estado.task))}\n</tarea>"},
        ]
        decision = self._llm.decidir(mensajes, self._schemas)
        cambios: dict[str, Any] = {
            "inicio": inicio,
            "mensajes": [*mensajes, decision.mensaje_asistente],
            "pendientes": decision.tool_calls,
            "iteraciones": estado.iteraciones + 1,
        }
        if not decision.tool_calls:
            final = decision.mensaje_asistente.get("content") or ""
            cambios["summary"] = _privado(self._resumen(estado, final))
        return cambios

    def _policy_gate(self, estado: EstadoSubagente, config: RunnableConfig) -> dict[str, Any]:
        llamada, resto = estado.pendientes[0], estado.pendientes[1:]
        # La iteración de esta llamada ya se contó al decidirla el agente.
        gate = evaluar(self._spec, llamada, estado.user, max(estado.iteraciones - 1, 0))
        cambios: dict[str, Any] = {"pendientes": resto, "actual": llamada, "gate": gate}
        if gate.decision == "deny":
            self._auditar(estado, gate.tool, gate.args or llamada.argumentos, "deny", gate.reason)
            cambios["mensajes"] = [
                *estado.mensajes,
                _mensaje_tool(llamada, f"Denegado: {gate.reason}"),
            ]
        elif gate.decision == "allow":
            self._auditar(estado, gate.tool, gate.args, "allow")
        else:
            expira = datetime.fromtimestamp(self._ahora(), UTC) + VIGENCIA_APROBACION
            cambios["expira"] = expira.isoformat()
            if self._aprobaciones is not None:
                aprobacion = self._aprobaciones.crear(
                    thread_id=config["configurable"]["thread_id"], trace_id=estado.trace_id,
                    agent=self._spec.name, tool=gate.tool, user_id=estado.user.id,
                    tipo=gate.mode, approver_role=gate.approver_role,
                    args_preview=_vista(gate.args or {}), risk=_riesgo(gate.mode),
                    expires_at=cambios["expira"],
                )  # fmt: skip
                cambios["aprobacion_id"] = aprobacion.id
        return cambios

    def _tras_gate(self, estado: EstadoSubagente) -> str:
        gate = _requerido(estado.gate)
        if gate.decision == "allow":
            return "execute_tool"
        if gate.decision == "needs_approval":
            return "human_approval"
        return _siguiente(estado)

    def _human_approval(self, estado: EstadoSubagente) -> dict[str, Any]:
        """Bucle de interrupt: una respuesta no válida (aprobador sin permiso, argumentos
        editados inválidos) no resuelve nada; se vuelve a pedir. Sin efectos laterales hasta
        la decisión final, porque LangGraph re-ejecuta el nodo al reanudar."""
        gate, expira = _requerido(estado.gate), _requerido(estado.expira)
        politica = self._spec.tools[gate.tool]
        tipo, args, aviso = gate.mode, gate.args or {}, None
        while True:
            carga = {
                "type": tipo, "agent": self._spec.name, "tool": gate.tool,
                "args_preview": _vista(args), "risk": _riesgo(tipo), "expires_at": expira,
            }  # fmt: skip
            if aviso:
                carga["aviso"] = aviso
            try:
                respuesta = RespuestaAprobacion.model_validate(interrupt(carga))
            except ValidationError:
                aviso = "Respuesta de aprobación no válida."
                continue
            if datetime.fromtimestamp(self._ahora(), UTC) > datetime.fromisoformat(expira):
                return self._rechazar(estado, args, respuesta, "La aprobación está vencida (24 h).")
            if (registrada := self._aprobacion(estado)) and registrada.estado != "pendiente":
                # Vencida por la tarea periódica o ya resuelta: no se reabre.
                motivo = f"La aprobación ya no está pendiente ({registrada.estado})."
                return self._rechazar(estado, args, respuesta, motivo, registrar=False)
            if not self._puede_aprobar(tipo, respuesta.approver_id, estado.user, politica):
                aviso = "Quien responde no puede aprobar esta acción."
                continue
            if not respuesta.approved:
                return self._rechazar(estado, args, respuesta, respuesta.reason or "Sin motivo.")
            if respuesta.edited_args is not None:
                try:
                    editados = politica.args_model.model_validate(respuesta.edited_args)
                except ValidationError:
                    aviso = "Los argumentos editados no son válidos."
                    continue
                modo = resolve_mode(politica, editados, estado.user)
                if modo == "deny":
                    return self._rechazar(
                        estado, args, respuesta, "Los argumentos editados no están permitidos."
                    )
                args = editados.model_dump(mode="json")
                if _NIVEL[modo] > _NIVEL[tipo]:  # los cambios exigen más: se vuelve a pedir
                    tipo, aviso = modo, "Los cambios exigen otra aprobación."
                    if self._aprobaciones is not None and estado.aprobacion_id:
                        self._aprobaciones.escalar(
                            estado.aprobacion_id, tipo, politica.approver_role, _vista(args)
                        )  # idempotente: al reanudar se repite con los mismos valores
                    continue
            if self._aprobaciones is not None and estado.aprobacion_id:
                if not self._aprobaciones.decidir(
                    estado.aprobacion_id, True, respuesta.approver_id, None
                ):
                    motivo = "La aprobación ya no está pendiente."
                    return self._rechazar(estado, args, respuesta, motivo, registrar=False)
            self._auditar(estado, gate.tool, args, "approved", aprobador=respuesta.approver_id)
            return {"gate": gate.model_copy(update={"decision": "allow", "args": args})}

    def _execute_tool(self, estado: EstadoSubagente) -> dict[str, Any]:
        gate = _requerido(estado.gate)
        politica = self._spec.tools[gate.tool]
        contexto = ContextoTool(user=estado.user, agent=self._spec.name, trace_id=estado.trace_id)
        try:
            resultado = politica.fn(politica.args_model.model_validate(gate.args), contexto)
        except Exception as exc:  # noqa: BLE001 — el fallo vuelve al agente como dato
            logger.warning("Tool %s falló: %s", gate.tool, type(exc).__name__)
            resultado = {"error": f"La herramienta falló ({type(exc).__name__})."}
        return {"resultado": resultado}

    def _sanitize_output(self, estado: EstadoSubagente) -> dict[str, Any]:
        actual, gate = _requerido(estado.actual), _requerido(estado.gate)
        resultado = estado.resultado
        ids = list(estado.ids)
        if isinstance(resultado, dict) and resultado.get("id") is not None:
            ids.append(str(resultado["id"]))
        contenido = _como_dato(gate.tool, resultado)
        return {
            "mensajes": [*estado.mensajes, _mensaje_tool(actual, contenido)],
            "resultado": None,
            "ids": ids,
        }

    # --------------------------------------------------------------------------- apoyo
    def _puede_aprobar(
        self, tipo: Mode, aprobador: str, user: UserContext, politica: ToolPolicy
    ) -> bool:
        if tipo == "confirm_user":
            return aprobador == user.id
        return aprobador != user.id and (politica.approver_role or "") in self._roles_de(aprobador)

    def _aprobacion(self, estado: EstadoSubagente) -> Any:
        if self._aprobaciones is None or not estado.aprobacion_id:
            return None
        return self._aprobaciones.obtener(estado.aprobacion_id)

    def _rechazar(
        self,
        estado: EstadoSubagente,
        args: dict,
        respuesta: RespuestaAprobacion,
        motivo: str,
        registrar: bool = True,
    ) -> dict[str, Any]:
        """`registrar=False`: la fila de approvals ya no está pendiente (no se toca)."""
        gate, actual = _requerido(estado.gate), _requerido(estado.actual)
        if registrar and self._aprobaciones is not None and estado.aprobacion_id:
            self._aprobaciones.decidir(estado.aprobacion_id, False, respuesta.approver_id, motivo)
        self._auditar(estado, gate.tool, args, "rejected", motivo, aprobador=respuesta.approver_id)
        return {
            "gate": gate.model_copy(update={"decision": "deny", "reason": motivo}),
            "mensajes": [
                *estado.mensajes,
                _mensaje_tool(actual, f"Acción rechazada: {motivo}"),
            ],
        }

    def _auditar(
        self,
        estado: EstadoSubagente,
        tool: str,
        args: dict | str | None,
        decision: Decision,
        motivo: str | None = None,
        aprobador: str | None = None,
    ) -> None:
        self._auditoria.registrar(
            RegistroAuditoria(
                trace_id=estado.trace_id, user_id=estado.user.id, agent=self._spec.name,
                tool=tool, args_hash=_huella(args), decision=decision,
                approver_id=aprobador, reason=motivo,
                fecha=datetime.fromtimestamp(self._ahora(), UTC).isoformat(),
            )
        )  # fmt: skip

    def _resumen(self, estado: EstadoSubagente, final: str | None, agotado: bool = False) -> str:
        """Lo que recibe el supervisor: sin datos personales (regla 5); con id_only, solo IDs."""
        if self._spec.returns == "id_only" and estado.ids:
            return ", ".join(estado.ids)
        texto = "Se agotó el presupuesto del agente." if agotado else (final or "")
        return enmascarar_pii(texto, TIPOS_PII)[0]


def _requerido[T](valor: T | None) -> T:
    """Campos que el flujo del grafo garantiza (sin `assert`: desaparecería con python -O)."""
    if valor is None:
        raise RuntimeError("Estado del subgrafo incoherente")
    return valor


def _siguiente(estado: EstadoSubagente) -> str:
    return "policy_gate" if estado.pendientes else "agent"


def _schema(nombre: str, politica: ToolPolicy) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": nombre,
            "description": politica.description,
            "parameters": politica.args_model.model_json_schema(),
        },
    }


def _mensaje_tool(llamada: ToolCall, contenido: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": llamada.id, "content": contenido}


def _como_dato(tool: str, resultado: Any) -> str:
    texto = resultado if isinstance(resultado, str) else json.dumps(resultado, ensure_ascii=False)
    texto = enmascarar_pii(texto, TIPOS_PII)[0]
    texto = neutralizar(_ETIQUETA_DATO.sub(lambda m: m.group(0).replace("<", "&lt;"), texto))
    if len(texto) > MAX_DATO:
        texto = texto[:MAX_DATO] + " …[truncado]"
    return f'<dato_herramienta tool="{tool}">\n{texto}\n</dato_herramienta>'


def _riesgo(modo: str) -> str:
    return "alto" if modo == "approve_staff" else "medio"


def _vista(args: dict) -> str:
    """Vista previa para quien aprueba: sin PII y acotada."""
    texto = json.dumps(args, ensure_ascii=False, sort_keys=True)
    return enmascarar_pii(texto, TIPOS_PII)[0][:300]


def _huella(args: dict | str | None) -> str:
    canonico = args if isinstance(args, str) else json.dumps(args, sort_keys=True, default=str)
    return hashlib.sha256((canonico or "").encode()).hexdigest()
