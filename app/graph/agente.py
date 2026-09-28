"""Agente LangGraph:

    authorize → input_guardrail → supervisor ⇄ tools → generate → output_guardrail → audit

authorize e input_guardrail pueden cortar el flujo directamente hacia audit: toda consulta
se audita, también las rechazadas.
"""

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError

from app.graph.prompts import SUPERVISOR_PROMPT
from app.graph.state import EstadoAgente
from app.models.schemas import ChunkRecuperado, RespuestaConsulta, Usuario
from app.rag.generacion import generar_respuesta, respuesta_sin_contexto
from app.retrieval.base import LLM, Supervisor
from app.security.audit import registrar_consulta
from app.security.guardrails import MENSAJE_BLOQUEO, Guardrail
from app.tools.base import Herramienta, schema_openai

Update = dict[str, Any]


def _bloqueada() -> RespuestaConsulta:
    return RespuestaConsulta(respuesta=MENSAJE_BLOQUEO, citas=[], sin_contexto=True)


class Agente:
    def __init__(
        self,
        supervisor: Supervisor,
        llm: LLM,
        herramientas: list[Herramienta],
        guardrail_entrada: Guardrail,
        guardrail_salida: Guardrail,
        top_k: int = 4,
        max_iteraciones: int = 3,
    ) -> None:
        self._supervisor = supervisor
        self._llm = llm
        self._herramientas = {h.nombre: h for h in herramientas}
        self._schemas = [schema_openai(h) for h in herramientas]
        self._guardrail_entrada = guardrail_entrada
        self._guardrail_salida = guardrail_salida
        self._top_k = top_k
        self._max_iteraciones = max_iteraciones
        self.grafo = self._construir()

    def consultar(
        self, pregunta: str, usuario: Usuario, top_k: int | None = None
    ) -> RespuestaConsulta:
        inicial = EstadoAgente(pregunta=pregunta, usuario=usuario, top_k=top_k or self._top_k)
        final = EstadoAgente.model_validate(self.grafo.invoke(inicial))
        return _requerir_respuesta(final)

    # ------------------------------------------------------------------ construcción
    def _construir(self) -> CompiledStateGraph:
        g = StateGraph(EstadoAgente)
        g.add_node("authorize", self._authorize)
        g.add_node("input_guardrail", self._input_guardrail)
        g.add_node("supervisor", self._supervisor_node)
        g.add_node("tools", self._tools)
        g.add_node("generate", self._generate)
        g.add_node("output_guardrail", self._output_guardrail)
        g.add_node("audit", self._audit)

        g.add_edge(START, "authorize")
        g.add_conditional_edges("authorize", self._continuar_o_auditar("input_guardrail"))
        g.add_conditional_edges("input_guardrail", self._continuar_o_auditar("supervisor"))
        g.add_conditional_edges("supervisor", self._tras_supervisor)
        g.add_edge("tools", "supervisor")
        g.add_edge("generate", "output_guardrail")
        g.add_edge("output_guardrail", "audit")
        g.add_edge("audit", END)
        return g.compile()

    @staticmethod
    def _continuar_o_auditar(siguiente: str):
        def ruta(estado: EstadoAgente) -> str:
            return "audit" if estado.respuesta is not None else siguiente

        return ruta

    @staticmethod
    def _tras_supervisor(estado: EstadoAgente) -> Literal["tools", "generate"]:
        return "tools" if estado.pendientes else "generate"

    # ------------------------------------------------------------------ nodos
    def _authorize(self, estado: EstadoAgente) -> Update:
        # Deny by default: sin grupos no hay nada visible; no se gasta ni una llamada al LLM.
        if not estado.usuario.groups:
            return {"respuesta": respuesta_sin_contexto()}
        return {}

    def _input_guardrail(self, estado: EstadoAgente) -> Update:
        if not self._guardrail_entrada.revisar(estado.pregunta).permitido:
            return {"respuesta": _bloqueada()}
        return {}

    def _supervisor_node(self, estado: EstadoAgente) -> Update:
        if estado.iteraciones >= self._max_iteraciones:
            return {"pendientes": []}
        mensajes = estado.mensajes or [
            {"role": "system", "content": SUPERVISOR_PROMPT},
            {"role": "user", "content": estado.pregunta},
        ]
        decision = self._supervisor.decidir(mensajes, self._schemas)
        return {
            "mensajes": [*mensajes, decision.mensaje_asistente],
            "pendientes": decision.tool_calls,
            "iteraciones": estado.iteraciones + 1,
        }

    def _tools(self, estado: EstadoAgente) -> Update:
        recuperados = list(estado.recuperados)
        vistos = {r.chunk.chunk_id for r in recuperados}
        mensajes = list(estado.mensajes)
        for llamada in estado.pendientes:
            nuevos, contenido = self._ejecutar(llamada.nombre, llamada.argumentos, estado)
            inicio = len(recuperados) + 1
            for r in nuevos:
                if r.chunk.chunk_id not in vistos:
                    vistos.add(r.chunk.chunk_id)
                    recuperados.append(r)
            if contenido is None:
                contenido = _resumen(recuperados[inicio - 1 :]) or "Sin resultados nuevos."
            mensajes.append({"role": "tool", "tool_call_id": llamada.id, "content": contenido})
        return {"recuperados": recuperados, "mensajes": mensajes, "pendientes": []}

    def _ejecutar(
        self, nombre: str, argumentos: str, estado: EstadoAgente
    ) -> tuple[list[ChunkRecuperado], str | None]:
        herramienta = self._herramientas.get(nombre)
        if herramienta is None:
            return [], f"Error: la herramienta '{nombre}' no existe."
        try:
            args = herramienta.args_model.model_validate_json(argumentos)
        except ValidationError as exc:
            return [], f"Error: argumentos no válidos ({exc.error_count()} errores)."
        return herramienta.ejecutar(args, estado.usuario, estado.top_k), None

    def _generate(self, estado: EstadoAgente) -> Update:
        return {"respuesta": generar_respuesta(self._llm, estado.pregunta, estado.recuperados)}

    def _output_guardrail(self, estado: EstadoAgente) -> Update:
        if not self._guardrail_salida.revisar(_requerir_respuesta(estado).respuesta).permitido:
            return {"respuesta": _bloqueada()}
        return {}

    def _audit(self, estado: EstadoAgente) -> Update:
        registrar_consulta(estado.usuario, estado.pregunta, _requerir_respuesta(estado))
        return {}


def _requerir_respuesta(estado: EstadoAgente) -> RespuestaConsulta:
    if estado.respuesta is None:
        raise RuntimeError("El grafo llegó a un nodo final sin respuesta fijada")
    return estado.respuesta


def _resumen(recuperados: list[ChunkRecuperado], max_chars: int = 400) -> str:
    return "\n\n".join(f"- ({r.chunk.fuente}) {r.chunk.contenido[:max_chars]}" for r in recuperados)
