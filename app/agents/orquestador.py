"""Grafo principal multiagente (fase 5):

    authorize → input_guardrail → cache_lookup → supervisor → [agentes en paralelo con Send]
      → sintetizar ⇄ verifier (máx. 3 reintentos) → output_guardrail → cache_store → audit
                         └→ escalate_human (interrupt al administrador) ┘

- Solo `authorize` escribe `user` (UserContext desde los grupos autenticados).
- El supervisor enruta con tool-calling: una tool `delegar_<agente>` por agente del registro
  (su `description`), más `conversacion` y `pedir_aclaracion`. Agregar un agente no cambia
  este grafo.
- Cada agente recibe SOLO su tarea y el usuario (regla 5) y corre su subgrafo (fases 1–4); sus
  `interrupt` suben hasta aquí y se resuelven uno a uno con `decidir()`.
- `verifier` es determinista: cada tarea con resultado, citas solo a documentos recuperados,
  identificadores solo de los creados y sin PII. Si falla, el supervisor reescribe con el
  motivo; a los 3 reintentos, `escalate_human`.
- Caché exacta con clave (consulta normalizada, scope_hash), solo para respuestas que salen
  únicamente de rag_agent y sin aprobaciones (regla 10).
"""

import hashlib
import json
import re
import time
import unicodedata
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Protocol

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt
from pydantic import BaseModel, Field, ValidationError

from app.agents.registry import RegistroAgentes
from app.agents.scopes import UserContext, contexto_de_usuario
from app.agents.subgraph import (
    VIGENCIA_APROBACION,
    Auditoria,
    AuditoriaMemoria,
    Privado,
    TextoPrivado,
    _privado,
    construir_subgrafo,
    resumen,
    sin_tocar_usuario,
)
from app.models.schemas import (
    Aclaracion,
    Cita,
    Hallazgo,
    RespuestaConsulta,
    Turno,
    Usuario,
)
from app.prompts import local
from app.rag.catalogo import CatalogoRol, construir_catalogo
from app.rag.orientacion import responder_sin_informacion
from app.rag.prompts import SIN_CONTEXTO, build_historial, neutralizar
from app.retrieval.base import LLM, Supervisor
from app.security.acl import es_rol
from app.security.audit import registrar_consulta
from app.security.deteccion import TIPOS_PII, enmascarar_pii
from app.security.guardrails import MENSAJE_BLOQUEO, Guardrail
from app.tools.conversacion import ORIENTADAS, PLANTILLAS

PROMPT_ORQUESTADOR = local("orquestador")
PROMPT_SINTESIS = local("sintesis")
MAX_REINTENTOS = 3
ROL_ESCALADO = "administrador"
MENSAJE_ESCALADO = (
    "No he podido darte una respuesta fiable. He pasado tu consulta a una persona del equipo, "
    "que te responderá aquí."
)
_CITA = re.compile(r"\[([a-z0-9][\w\-./]*/[\w\-./]+)\]")
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")


# ---------------------------------------------------------------------------------- modelos
class EnvioAgente(BaseModel):
    """Lo único que recibe un agente: su tarea y el usuario (aislamiento, regla 5)."""

    agente: str
    tarea: Privado
    user: UserContext
    trace_id: str = ""


class ResultadoSub(BaseModel):
    agente: str
    resumen: Privado
    ids: list[str] = Field(default_factory=list)
    fuentes: list[dict[str, Any]] = Field(default_factory=list)


class AprobacionPendiente(BaseModel):
    interrupt_id: str
    aprobacion_id: str | None = None
    type: str
    agent: str
    tool: str
    args_preview: str = ""
    risk: str = ""
    expires_at: str = ""


class ResultadoOrquestacion(BaseModel):
    respuesta: RespuestaConsulta
    aprobaciones: list[AprobacionPendiente] = Field(default_factory=list)
    thread_id: str
    traza_id: str
    pregunta_procesada: str = ""
    documentos_consultados: list[str] = Field(default_factory=list)
    hallazgos: list[Hallazgo] = Field(default_factory=list)
    desde_cache: bool = False
    agentes: list[str] = Field(default_factory=list)


def acumular_o_reiniciar(
    actuales: list[ResultadoSub] | None, nuevos: list[ResultadoSub] | None
) -> list[ResultadoSub]:
    """Reductor de `resultados`: las ramas paralelas (Send) suman sus resultados sin pisarse;
    `None` reinicia la lista (lo emite el supervisor al empezar), así un hilo reutilizado no
    mezcla los resultados de un mensaje anterior."""
    if nuevos is None:
        return []
    return [*(actuales or []), *nuevos]


class EstadoOrquestador(BaseModel):
    pregunta: Privado
    usuario: Usuario  # identidad autenticada de entrada: solo la lee authorize
    user: UserContext | None = None  # la escribe solo authorize
    trace_id: str = ""
    conversacion_id: str | None = None
    historial: list[Turno] = Field(default_factory=list)
    hallazgos: list[Hallazgo] = Field(default_factory=list)
    tareas: dict[str, Privado] = Field(default_factory=dict)
    resultados: Annotated[list[ResultadoSub], acumular_o_reiniciar] = Field(default_factory=list)
    texto: Privado | None = None  # síntesis en curso
    correcciones: list[str] = Field(default_factory=list)
    reintentos: int = 0  # reescrituras pedidas por el verifier (máx. MAX_REINTENTOS)
    agotado: bool = False  # el verifier falló sin reintentos disponibles → escalate_human
    verificado: bool = False
    respuesta: RespuestaConsulta | None = None
    desde_cache: bool = False


# Campos de una ejecución y su valor inicial (`resultados: None` reinicia su reductor).
REINICIO: dict[str, Any] = {
    "user": None, "hallazgos": [], "tareas": {}, "resultados": None, "texto": None,
    "correcciones": [], "reintentos": 0, "agotado": False, "verificado": False,
    "respuesta": None, "desde_cache": False,
}  # fmt: skip

# Únicos campos de texto plano del estado (PostgresSaver los guarda sin cifrar).
CAMPOS_TEXTO_INOCUOS = frozenset({"trace_id", "conversacion_id"})


class CacheExacta(Protocol):
    def obtener(self, clave: str) -> RespuestaConsulta | None: ...
    def guardar(self, clave: str, respuesta: RespuestaConsulta) -> None: ...


class CacheExactaMemoria:
    def __init__(self) -> None:
        self._datos: dict[str, RespuestaConsulta] = {}

    def obtener(self, clave: str) -> RespuestaConsulta | None:
        return self._datos.get(clave)

    def guardar(self, clave: str, respuesta: RespuestaConsulta) -> None:
        self._datos[clave] = respuesta


def normalizar_consulta(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto.casefold()).encode("ascii", "ignore").decode()
    return " ".join(re.findall(r"[a-z0-9]+", sin_tildes))


# ------------------------------------------------------------------------------ orquestador
class Orquestador:
    def __init__(
        self,
        registro: RegistroAgentes,
        supervisor: Supervisor,
        llm_de_agente: Callable[[str], Supervisor],
        llm_sintesis: LLM,
        guardrail_entrada: Guardrail,
        guardrail_salida: Guardrail,
        roles_de: Callable[[str], set[str]],
        checkpointer: Any,
        auditoria: Auditoria | None = None,
        aprobaciones: Any = None,
        alcance: Callable[[UserContext], str] | None = None,
        cache: CacheExacta | None = None,
        catalogo: Callable[[list[str]], CatalogoRol] | None = None,
        ahora: Callable[[], float] = time.time,
    ) -> None:
        self._registro, self._supervisor, self._llm = registro, supervisor, llm_sintesis
        self._entrada, self._salida = guardrail_entrada, guardrail_salida
        self._roles_de, self._aprobaciones, self._ahora = roles_de, aprobaciones, ahora
        self._alcance = alcance
        self._cache = cache if cache is not None else (CacheExactaMemoria() if alcance else None)
        self._catalogo = catalogo or (lambda grupos: construir_catalogo(grupos, [], {}, []))
        auditoria = auditoria or AuditoriaMemoria()
        # Subgrafos sin checkpointer propio: heredan el del orquestador al ejecutarse dentro
        # de su nodo, y así sus interrupts se reanudan desde aquí.
        self._subgrafos = {
            nombre: construir_subgrafo(
                spec,
                llm_de_agente(nombre),
                auditoria,
                roles_de,
                checkpointer=None,
                ahora=ahora,
                aprobaciones=aprobaciones,
            )  # fmt: skip
            for nombre, spec in registro.items()
        }
        self._schemas = self._herramientas_de_enrutado()
        self.grafo = self._compilar(checkpointer)

    # --------------------------------------------------------------------------- API
    def entrada(
        self,
        pregunta: str,
        usuario: Usuario,
        conversacion_id: str | None = None,
        historial: list[Turno] | None = None,
    ) -> dict[str, Any]:
        return {
            "pregunta": TextoPrivado(valor=pregunta), "usuario": usuario,
            "trace_id": str(uuid.uuid4()), "conversacion_id": conversacion_id,
            "historial": historial or [],
            # Todo lo de una ejecución, reiniciado: si el hilo ya tenía estado (reutilizado),
            # nada de un mensaje anterior (respuesta, resultados, caché…) se mezcla con este.
            **REINICIO,
        }  # fmt: skip

    @staticmethod
    def config(thread_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": thread_id}}

    def consultar(
        self,
        pregunta: str,
        usuario: Usuario,
        conversacion_id: str | None = None,
        historial: list[Turno] | None = None,
        thread_id: str | None = None,
    ) -> ResultadoOrquestacion:
        thread_id = thread_id or str(uuid.uuid4())
        salida = self.grafo.invoke(
            self.entrada(pregunta, usuario, conversacion_id, historial), self.config(thread_id)
        )
        return self._resultado(salida, thread_id)

    def decidir(
        self,
        thread_id: str,
        interrupt_id: str,
        aprobado: bool,
        aprobador: str,
        edited_args: dict | None = None,
        motivo: str | None = None,
        respuesta: str | None = None,
    ) -> ResultadoOrquestacion:
        """Resuelve UNA aprobación pendiente; las demás siguen pendientes. `aprobador` sale
        del token de quien decide (la API nunca lo toma del cuerpo de la petición)."""
        valor = {
            "approved": aprobado, "approver_id": aprobador, "edited_args": edited_args,
            "reason": motivo, "respuesta": respuesta,
        }  # fmt: skip
        salida = self.grafo.invoke(Command(resume={interrupt_id: valor}), self.config(thread_id))
        return self._resultado(salida, thread_id)

    def pendientes(self, thread_id: str) -> list[AprobacionPendiente]:
        estado = self.grafo.get_state(self.config(thread_id), subgraphs=True)
        return [_pendiente(i) for i in _interrupts(estado)]

    # ------------------------------------------------------------------------- grafo
    def _compilar(self, checkpointer: Any) -> Any:
        g = StateGraph(EstadoOrquestador)
        for nombre, nodo in [
            ("authorize", self._authorize),
            ("input_guardrail", self._input_guardrail),
            ("cache_lookup", self._cache_lookup),
            ("supervisor", self._supervisor_node),
            ("ejecutar_agente", self._ejecutar_agente),
            ("sintetizar", self._sintetizar),
            ("verifier", self._verifier),
            ("escalate_human", self._escalate_human),
            ("output_guardrail", self._output_guardrail),
            ("cache_store", self._cache_store),
            ("audit", self._audit),
        ]:
            if nombre == "authorize":
                g.add_node(nombre, nodo)  # el único que escribe user
            else:
                g.add_node(nombre, sin_tocar_usuario(nodo))
        g.add_edge(START, "authorize")
        g.add_conditional_edges("authorize", _si_respondida("input_guardrail"))
        g.add_conditional_edges("input_guardrail", _si_respondida("cache_lookup"))
        g.add_conditional_edges("cache_lookup", _si_respondida("supervisor", "output_guardrail"))
        g.add_conditional_edges(
            "supervisor", self._despachar, ["ejecutar_agente", "output_guardrail"]
        )
        g.add_edge("ejecutar_agente", "sintetizar")
        g.add_edge("sintetizar", "verifier")
        g.add_conditional_edges("verifier", self._tras_verifier)
        g.add_edge("escalate_human", "output_guardrail")
        g.add_edge("output_guardrail", "cache_store")
        g.add_edge("cache_store", "audit")
        g.add_edge("audit", END)
        return g.compile(checkpointer=checkpointer)

    # ------------------------------------------------------------------------- nodos
    def _authorize(self, estado: EstadoOrquestador) -> dict[str, Any]:
        grupos = estado.usuario.groups
        roles = [g for g in grupos if es_rol(g)]
        if not roles:  # deny by default: sin rol no hay contexto ni llamadas a modelos
            return {"respuesta": _sin_contexto(SIN_CONTEXTO)}
        deptos = [g.removeprefix("dept:") for g in grupos if g.startswith("dept:")]
        return {"user": contexto_de_usuario(estado.usuario.id, roles, deptos)}

    def _input_guardrail(self, estado: EstadoOrquestador) -> dict[str, Any]:
        veredicto = self._entrada.revisar(str(estado.pregunta))
        cambios: dict[str, Any] = {
            "pregunta": _privado(veredicto.texto),
            "hallazgos": [*estado.hallazgos, *veredicto.hallazgos],
        }
        if not veredicto.permitido:
            cambios["respuesta"] = _sin_contexto(veredicto.mensaje or MENSAJE_BLOQUEO)
        return cambios

    def _cache_lookup(self, estado: EstadoOrquestador) -> dict[str, Any]:
        if (clave := self._clave_cache(estado)) and self._cache is not None:
            if (guardada := self._cache.obtener(clave)) is not None:
                return {"respuesta": guardada, "desde_cache": True, "verificado": True}
        return {}

    def _supervisor_node(self, estado: EstadoOrquestador) -> dict[str, Any]:
        user = _requerido(estado.user)
        catalogo = neutralizar(self._catalogo(list(user.acl)).como_texto(con_ids=True))
        historial = build_historial(estado.historial)
        pregunta = neutralizar(str(estado.pregunta))
        mensajes = [
            {"role": "system",
             "content": f"{PROMPT_ORQUESTADOR}\n<catalogo>\n{catalogo}\n</catalogo>"},
            {"role": "user", "content": f"{historial}<pregunta>\n{pregunta}\n</pregunta>"},
        ]  # fmt: skip
        decision = self._supervisor.decidir(mensajes, self._schemas, obligar_herramienta=True)
        tareas: dict[str, TextoPrivado] = {}
        for llamada in decision.tool_calls:
            try:
                args = json.loads(llamada.argumentos or "{}")
            except ValueError:
                continue
            nombre = llamada.nombre.removeprefix("delegar_")
            if llamada.nombre.startswith("delegar_") and nombre in self._registro:
                if isinstance(args.get("tarea"), str) and args["tarea"].strip():
                    tareas[nombre] = TextoPrivado(valor=args["tarea"][:2000])
            elif llamada.nombre == "pedir_aclaracion" and not tareas:
                try:
                    aclaracion = Aclaracion.model_validate(args)
                except ValidationError:
                    continue
                return {"respuesta": RespuestaConsulta(
                    respuesta=aclaracion.pregunta, citas=[], sin_contexto=True,
                    aclaracion=aclaracion.model_copy(update={"opciones": aclaracion.opciones[:4]}),
                )}  # fmt: skip
            elif llamada.nombre == "conversacion" and not tareas:
                return {"respuesta": self._conversacion(estado, user, str(args.get("tipo", "")))}
        if not tareas:  # nada que delegar (o solo agentes inexistentes): orientación
            return {"respuesta": self._orientar(estado, user, "sin_resultados")}
        # Estado limpio para esta orquestación (aunque el hilo se reutilizara).
        return {
            "tareas": tareas, "resultados": None, "texto": None, "correcciones": [],
            "reintentos": 0, "agotado": False, "verificado": False,
        }  # fmt: skip

    def _despachar(self, estado: EstadoOrquestador) -> list[Send] | str:
        if estado.respuesta is not None or not estado.tareas:
            return "output_guardrail"
        user = _requerido(estado.user)
        return [
            Send(
                "ejecutar_agente",
                EnvioAgente(agente=nombre, tarea=tarea, user=user, trace_id=estado.trace_id),
            )  # fmt: skip
            for nombre, tarea in estado.tareas.items()
        ]

    def _ejecutar_agente(self, envio: EnvioAgente, config: RunnableConfig) -> dict[str, Any]:
        salida = self._subgrafos[envio.agente].invoke(
            {"task": envio.tarea, "user": envio.user, "trace_id": envio.trace_id}, config
        )
        return {"resultados": [ResultadoSub(
            agente=envio.agente, resumen=TextoPrivado(valor=resumen(salida)),
            ids=list(salida.get("ids") or []), fuentes=list(salida.get("fuentes") or []),
        )]}  # fmt: skip

    def _sintetizar(self, estado: EstadoOrquestador) -> dict[str, Any]:
        if len(estado.resultados) == 1 and not estado.correcciones:
            return {"texto": estado.resultados[0].resumen}  # un agente: su respuesta, sin coste
        bloques = "\n".join(
            f'<resultado agente="{r.agente}">\n{neutralizar(str(r.resumen))}\n</resultado>'
            for r in estado.resultados
        )
        correcciones = "".join(f"- {c}\n" for c in estado.correcciones)
        usuario = f"<pregunta>\n{neutralizar(str(estado.pregunta))}\n</pregunta>\n\n{bloques}" + (
            f"\n\n<correcciones>\n{correcciones}</correcciones>" if correcciones else ""
        )
        salida = self._llm.responder(PROMPT_SINTESIS, usuario)
        return {"texto": TextoPrivado(valor=salida.respuesta.strip())}

    def _verifier(self, estado: EstadoOrquestador) -> dict[str, Any]:
        texto = str(estado.texto or "")
        problemas = []
        if len(estado.resultados) < len(estado.tareas):
            problemas.append("Falta el resultado de alguno de los agentes.")
        fuentes = {f["doc_id"] for r in estado.resultados for f in r.fuentes}
        if inventadas := sorted({d for d in _CITA.findall(texto) if d not in fuentes}):
            problemas.append(
                f"Cita documentos que ningún agente recuperó: {', '.join(inventadas)}."
            )
        ids = {i for r in estado.resultados for i in r.ids}
        if falsos := sorted({i for i in _UUID.findall(texto) if i not in ids}):
            problemas.append(f"Menciona identificadores que no se crearon: {', '.join(falsos)}.")
        if enmascarar_pii(texto, TIPOS_PII)[1]:
            problemas.append("Incluye datos personales.")
        if not texto.strip():
            problemas.append("La respuesta está vacía.")
        if not problemas:
            return {"verificado": True, "respuesta": self._respuesta_final(texto, estado)}
        if estado.reintentos >= MAX_REINTENTOS:  # 1 intento + MAX_REINTENTOS reescrituras
            return {"verificado": False, "agotado": True, "correcciones": problemas}
        return {
            "verificado": False, "correcciones": problemas, "reintentos": estado.reintentos + 1,
        }  # fmt: skip

    @staticmethod
    def _tras_verifier(estado: EstadoOrquestador) -> str:
        if estado.verificado:
            return "output_guardrail"
        return "escalate_human" if estado.agotado else "sintetizar"

    def _escalate_human(self, estado: EstadoOrquestador, config: RunnableConfig) -> dict[str, Any]:
        """Interrupt al administrador. El solicitante no puede resolver su propio escalado."""
        user = _requerido(estado.user)
        expira = datetime.fromtimestamp(self._ahora(), UTC) + VIGENCIA_APROBACION
        aprobacion_id = None
        if self._aprobaciones is not None:
            # Una sola fila aunque el nodo se re-ejecute al reanudar: id determinista por hilo.
            aprobacion_id = self._registrar_escalado(estado, user, config, expira.isoformat())
        aviso = None
        while True:
            carga = {
                "type": "escalate_human", "agent": "supervisor", "tool": "escalate_human",
                "args_preview": "; ".join(estado.correcciones)[:300], "risk": "alto",
                "expires_at": expira.isoformat(), "aprobacion_id": aprobacion_id,
                "approver_role": ROL_ESCALADO,
            }  # fmt: skip
            if aviso:
                carga["aviso"] = aviso
            valor = interrupt(carga)
            aprobador = str(valor.get("approver_id", "")) if isinstance(valor, dict) else ""
            if aprobador == user.id or ROL_ESCALADO not in self._roles_de(aprobador):
                aviso = "Quien responde no puede resolver este escalado."
                continue
            break
        texto = (valor.get("respuesta") or "").strip() if valor.get("approved") else ""
        if self._aprobaciones is not None and aprobacion_id:
            self._aprobaciones.decidir(aprobacion_id, bool(valor.get("approved")), aprobador, texto)
        final = (
            enmascarar_pii(texto, TIPOS_PII)[0]
            if texto
            else ("Una persona del equipo ha revisado tu consulta, pero no puede responderla aquí.")
        )
        return {"respuesta": RespuestaConsulta(respuesta=final, citas=[], sin_contexto=True)}

    def _output_guardrail(self, estado: EstadoOrquestador) -> dict[str, Any]:
        respuesta = estado.respuesta or _sin_contexto(SIN_CONTEXTO)
        veredicto = self._salida.revisar(respuesta.respuesta)
        hallazgos = [*estado.hallazgos, *veredicto.hallazgos]
        if not veredicto.permitido:
            return {"respuesta": _sin_contexto(veredicto.mensaje or MENSAJE_BLOQUEO),
                    "hallazgos": hallazgos, "verificado": False}  # fmt: skip
        return {"respuesta": respuesta.model_copy(update={"respuesta": veredicto.texto}),
                "hallazgos": hallazgos}  # fmt: skip

    def _cache_store(self, estado: EstadoOrquestador) -> dict[str, Any]:
        """Solo respuestas verificadas que salen únicamente de rag_agent (regla 10)."""
        solo_rag = set(estado.tareas) == {"rag_agent"} and not any(r.ids for r in estado.resultados)
        if (
            solo_rag and estado.verificado and not estado.desde_cache and estado.respuesta
            and (clave := self._clave_cache(estado)) and self._cache is not None
        ):  # fmt: skip
            self._cache.guardar(clave, estado.respuesta)
        return {}

    def _audit(self, estado: EstadoOrquestador) -> dict[str, Any]:
        registrar_consulta(
            estado.usuario, str(estado.pregunta), estado.respuesta or _sin_contexto(SIN_CONTEXTO),
            estado.hallazgos, traza_id=estado.trace_id, conversacion_id=estado.conversacion_id,
            documentos_consultados=_documentos(estado), desde_cache=estado.desde_cache,
        )  # fmt: skip
        return {}

    # ------------------------------------------------------------------------- apoyo
    def _herramientas_de_enrutado(self) -> list[dict[str, Any]]:
        def tool(nombre: str, descripcion: str, parametros: dict) -> dict[str, Any]:
            return {"type": "function", "function": {
                "name": nombre, "description": descripcion, "parameters": parametros,
            }}  # fmt: skip

        tarea = {"type": "object", "properties": {"tarea": {"type": "string"}},
                 "required": ["tarea"]}  # fmt: skip
        return [
            *(tool(f"delegar_{n}", s.description, tarea) for n, s in self._registro.items()),
            tool("conversacion", "Solo cortesía, ayuda o temas ajenos a la empresa", {
                "type": "object", "required": ["tipo"],
                "properties": {"tipo": {"type": "string", "enum": sorted(PLANTILLAS)}},
            }),
            tool("pedir_aclaracion", "Pregunta al usuario qué necesita, con hasta 4 opciones", {
                "type": "object", "required": ["pregunta"],
                "properties": {"pregunta": {"type": "string"},
                               "opciones": {"type": "array", "items": {"type": "string"}}},
            }),
        ]  # fmt: skip

    def _conversacion(
        self, estado: EstadoOrquestador, user: UserContext, tipo: str
    ) -> RespuestaConsulta:
        if tipo in ORIENTADAS:
            r = self._orientar(estado, user, tipo, respaldo=PLANTILLAS[tipo])
            return r.model_copy(update={"conversacional": True})
        texto = PLANTILLAS.get(tipo, PLANTILLAS["ayuda"])
        return RespuestaConsulta(respuesta=texto, citas=[], sin_contexto=True, conversacional=True)

    def _orientar(
        self,
        estado: EstadoOrquestador,
        user: UserContext,
        motivo: Any,
        respaldo: str = SIN_CONTEXTO,
    ) -> RespuestaConsulta:
        return responder_sin_informacion(
            self._llm, str(estado.pregunta), self._catalogo(list(user.acl)), motivo,
            respaldo=respaldo,
        )  # fmt: skip

    def _respuesta_final(self, texto: str, estado: EstadoOrquestador) -> RespuestaConsulta:
        """[doc_id] → [n] con el fragmento real, como espera la web (contrato de la API)."""
        fuentes: dict[str, dict] = {}
        for r in estado.resultados:
            for f in r.fuentes:
                fuentes.setdefault(f["doc_id"], f)
        citas: list[Cita] = []
        numeros: dict[str, int] = {}
        for doc_id in _CITA.findall(texto):
            if doc_id in fuentes and doc_id not in numeros:
                numeros[doc_id] = len(numeros) + 1
                f = fuentes[doc_id]
                citas.append(Cita(
                    numero=numeros[doc_id], doc_id=doc_id, chunk_id=f"{doc_id}#0", fuente=doc_id,
                    fragmento=f.get("contenido") or "", score=float(f.get("score") or 0),
                ))  # fmt: skip
        final = _CITA.sub(
            lambda m: f"[{numeros[m.group(1)]}]" if m.group(1) in numeros else "", texto
        )
        return RespuestaConsulta(respuesta=final.strip(), citas=citas, sin_contexto=not citas)

    def _clave_cache(self, estado: EstadoOrquestador) -> str | None:
        if self._alcance is None or estado.user is None:
            return None
        consulta = normalizar_consulta(str(estado.pregunta))
        return hashlib.sha256(f"{consulta}|{self._alcance(estado.user)}".encode()).hexdigest()

    def _registrar_escalado(
        self, estado: EstadoOrquestador, user: UserContext, config: RunnableConfig, expira: str
    ) -> str:
        thread_id = config["configurable"]["thread_id"]
        existente = [
            a for a in self._aprobaciones.pendientes(roles=[ROL_ESCALADO])
            if a.thread_id == thread_id and a.tipo == "escalate_human"
        ]  # fmt: skip
        if existente:
            return existente[0].id
        return self._aprobaciones.crear(
            thread_id=thread_id, trace_id=estado.trace_id, agent="supervisor",
            tool="escalate_human", user_id=user.id, tipo="escalate_human",
            approver_role=ROL_ESCALADO, args_preview="; ".join(estado.correcciones)[:300],
            risk="alto", expires_at=expira,
        ).id  # fmt: skip

    def _resultado(self, salida: dict[str, Any], thread_id: str) -> ResultadoOrquestacion:
        pendientes = [_pendiente(i) for i in salida.get("__interrupt__", [])]
        respuesta = salida.get("respuesta")
        if pendientes:
            respuesta = _respuesta_provisional(pendientes)
        return ResultadoOrquestacion(
            respuesta=respuesta or _sin_contexto(SIN_CONTEXTO), aprobaciones=pendientes,
            thread_id=thread_id, traza_id=str(salida.get("trace_id") or ""),
            pregunta_procesada=str(salida.get("pregunta") or ""),
            documentos_consultados=sorted({
                f["doc_id"] for r in salida.get("resultados", []) for f in r.fuentes
            }),
            hallazgos=salida.get("hallazgos") or [], desde_cache=bool(salida.get("desde_cache")),
            agentes=sorted(salida.get("tareas") or {}),
        )  # fmt: skip


# ----------------------------------------------------------------------------- funciones
def _si_respondida(siguiente: str, si_no: str = "audit") -> Callable[[EstadoOrquestador], str]:
    def ruta(estado: EstadoOrquestador) -> str:
        return si_no if estado.respuesta is not None else siguiente

    return ruta


def _sin_contexto(texto: str) -> RespuestaConsulta:
    return RespuestaConsulta(respuesta=texto, citas=[], sin_contexto=True)


def _documentos(estado: EstadoOrquestador) -> list[str]:
    return sorted({f["doc_id"] for r in estado.resultados for f in r.fuentes})


def _requerido[T](valor: T | None) -> T:
    if valor is None:
        raise RuntimeError("Estado del orquestador incoherente")
    return valor


def _interrupts(estado: Any) -> list[Any]:
    vistos = list(getattr(estado, "interrupts", ()) or ())
    for tarea in getattr(estado, "tasks", ()) or ():
        vistos += list(getattr(tarea, "interrupts", ()) or ())
        if getattr(tarea, "state", None) is not None:
            vistos += _interrupts(tarea.state)
    unicos = {i.id: i for i in vistos}
    return list(unicos.values())


def _pendiente(interrupcion: Any) -> AprobacionPendiente:
    v = interrupcion.value if isinstance(interrupcion.value, dict) else {}
    return AprobacionPendiente(
        interrupt_id=interrupcion.id, aprobacion_id=v.get("aprobacion_id"),
        type=str(v.get("type", "")), agent=str(v.get("agent", "")), tool=str(v.get("tool", "")),
        args_preview=str(v.get("args_preview", "")), risk=str(v.get("risk", "")),
        expires_at=str(v.get("expires_at", "")),
    )  # fmt: skip


def _respuesta_provisional(pendientes: list[AprobacionPendiente]) -> RespuestaConsulta:
    if any(p.type == "escalate_human" for p in pendientes):
        return _sin_contexto(MENSAJE_ESCALADO)
    partes = []
    for p in pendientes:
        quien = "la aprobación de Soporte IT" if p.type == "approve_staff" else "tu confirmación"
        partes.append(f"«{p.tool}» necesita {quien}")
    return _sin_contexto("He preparado lo siguiente y está pendiente: " + "; ".join(partes) + ".")
