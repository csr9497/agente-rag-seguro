"""Agente LangGraph:

    authorize → input_guardrail → supervisor ⇄ tools → generate → output_guardrail → audit

authorize e input_guardrail pueden cortar el flujo directamente hacia audit: toda consulta
se audita, también las rechazadas.
"""

import uuid
from collections.abc import Callable
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langsmith import Client
from pydantic import BaseModel, Field, ValidationError

from app.cache.semantica import CacheSemantica, EntradaCache
from app.config import Settings
from app.graph.prompts import SUPERVISOR_PROMPT
from app.graph.state import EstadoAgente, ResultadoLlamada
from app.models.schemas import ChunkRecuperado, Hallazgo, RespuestaConsulta, Turno, Usuario
from app.observabilidad import traza_consulta, usuario_seudonimo
from app.rag.generacion import generar_respuesta, respuesta_sin_contexto
from app.rag.prompts import build_historial, neutralizar
from app.retrieval.base import LLM, Embedder, Supervisor
from app.security.acceso import PREFIJO_DATOS, VerificadorAcceso, VerificadorPermisivo
from app.security.audit import registrar_consulta
from app.security.guardrails import MENSAJE_BLOQUEO, Guardrail
from app.tools.base import SIN_ACCESO, Herramienta, ResultadoHerramienta, schema_openai

Update = dict[str, Any]


class ResultadoAgente(BaseModel):
    respuesta: RespuestaConsulta
    pregunta_procesada: str = Field(description="Tras input_guardrail (PII enmascarada)")
    documentos_consultados: list[str]
    fragmentos_descartados: int
    hallazgos: list[Hallazgo]
    traza_id: str
    desde_cache: bool = False


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
        max_contexto: int = 12,
        verificador: VerificadorAcceso | None = None,
        trazas: Client | None = None,
        settings: Settings | None = None,
        cache: CacheSemantica | None = None,
        embedder_cache: Embedder | None = None,
        alcance_cache: Callable[[list[str]], str] | None = None,
    ) -> None:
        self._supervisor = supervisor
        self._llm = llm
        self._herramientas = {h.nombre: h for h in herramientas}
        self._schemas = [schema_openai(h) for h in herramientas]
        self._guardrail_entrada = guardrail_entrada
        self._guardrail_salida = guardrail_salida
        self._top_k = top_k
        self._max_iteraciones = max_iteraciones
        self._max_contexto = max_contexto
        self._verificador = verificador or VerificadorPermisivo()
        self._trazas = trazas
        # Caché semántica: solo activa si están las tres piezas.
        activa = cache is not None and embedder_cache is not None and alcance_cache is not None
        self._cache = cache if activa else None
        self._embedder_cache = embedder_cache
        self._alcance_cache = alcance_cache
        self._settings = settings or Settings()
        self.grafo = self._construir()

    @property
    def cliente_trazas(self) -> Client | None:
        return self._trazas

    def consultar(
        self, pregunta: str, usuario: Usuario, top_k: int | None = None
    ) -> RespuestaConsulta:
        return self.consultar_detallado(pregunta, usuario, top_k).respuesta

    def consultar_detallado(
        self,
        pregunta: str,
        usuario: Usuario,
        top_k: int | None = None,
        conversacion_id: str | None = None,
        historial: list[Turno] | None = None,
    ) -> ResultadoAgente:
        inicial = EstadoAgente(
            pregunta=pregunta,
            usuario=usuario,
            top_k=top_k or self._top_k,
            historial=historial or [],
        )
        with traza_consulta(
            self._trazas, self._settings, roles=usuario.groups, conversacion_id=conversacion_id
        ) as config:
            config["metadata"]["usuario"] = usuario_seudonimo(usuario.id)
            config["run_id"] = traza_id = uuid.uuid4()
            final = EstadoAgente.model_validate(self.grafo.invoke(inicial, config=config))
        consultados = _documentos_consultados(final)
        return ResultadoAgente(
            respuesta=_requerir_respuesta(final),
            pregunta_procesada=final.pregunta,
            documentos_consultados=consultados,
            fragmentos_descartados=final.fragmentos_descartados,
            hallazgos=final.hallazgos,
            traza_id=str(traza_id),
            desde_cache=final.desde_cache,
        )

    # ------------------------------------------------------------------ construcción
    def _construir(self) -> CompiledStateGraph:
        g = StateGraph(EstadoAgente)
        g.add_node("authorize", self._authorize)
        g.add_node("input_guardrail", self._input_guardrail)
        g.add_node("supervisor", self._supervisor_node)
        g.add_node("tools", self._tools)
        g.add_node("access_guardrail", self._access_guardrail)
        g.add_node("generate", self._generate)
        g.add_node("output_guardrail", self._output_guardrail)
        g.add_node("cache_lookup", self._cache_lookup)
        g.add_node("cache_store", self._cache_store)
        g.add_node("audit", self._audit)

        g.add_edge(START, "authorize")
        # Los destinos explícitos documentan la topología (y la dibujan bien en /grafo).
        g.add_conditional_edges(
            "authorize", self._continuar_o_auditar("input_guardrail"), ["input_guardrail", "audit"]
        )
        g.add_conditional_edges(
            "input_guardrail", self._continuar_o_auditar("cache_lookup"), ["cache_lookup", "audit"]
        )
        g.add_conditional_edges(
            "cache_lookup", self._tras_cache, ["output_guardrail", "supervisor"]
        )
        g.add_conditional_edges("supervisor", self._tras_supervisor, ["tools", "generate"])
        g.add_edge("tools", "access_guardrail")
        g.add_edge("access_guardrail", "supervisor")
        g.add_edge("generate", "output_guardrail")
        g.add_edge("output_guardrail", "cache_store")
        g.add_edge("cache_store", "audit")
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
        veredicto = self._guardrail_entrada.revisar(estado.pregunta)
        update: Update = {
            "pregunta": veredicto.texto,
            "hallazgos": [*estado.hallazgos, *veredicto.hallazgos],
        }
        if not veredicto.permitido:
            update["respuesta"] = _bloqueada()
        return update

    def _supervisor_node(self, estado: EstadoAgente) -> Update:
        if estado.iteraciones >= self._max_iteraciones:
            return {"pendientes": []}
        mensajes = estado.mensajes or [
            {"role": "system", "content": SUPERVISOR_PROMPT},
            {"role": "user", "content": _pregunta_supervisor(estado)},
        ]
        decision = self._supervisor.decidir(mensajes, self._schemas)
        return {
            "mensajes": [*mensajes, decision.mensaje_asistente],
            "pendientes": decision.tool_calls,
            "iteraciones": estado.iteraciones + 1,
        }

    def _tools(self, estado: EstadoAgente) -> Update:
        """Solo ejecuta. El contenido no llega al supervisor hasta pasar access_guardrail."""
        resultados = [
            ResultadoLlamada(
                tool_call_id=llamada.id,
                resultado=self._ejecutar(llamada.nombre, llamada.argumentos, estado),
            )
            for llamada in estado.pendientes
        ]
        return {"por_revisar": resultados, "pendientes": []}

    def _access_guardrail(self, estado: EstadoAgente) -> Update:
        """Verifica cada fragmento contra el registro; acumula contexto y compone los mensajes
        de tool para el supervisor solo con lo autorizado."""
        recuperados = list(estado.recuperados)
        vistos = {r.chunk.chunk_id for r in recuperados}
        mensajes = list(estado.mensajes)
        hallazgos = list(estado.hallazgos)
        descartados_total = estado.fragmentos_descartados
        for llamada in estado.por_revisar:
            resultado = llamada.resultado
            nuevos: list[ChunkRecuperado] = []
            sin_acceso = por_tope = 0
            for r in resultado.chunks:
                if motivo := self._verificador.motivo_rechazo(r.chunk, estado.usuario.groups):
                    sin_acceso += 1
                    hallazgos.append(
                        Hallazgo(
                            tipo="acceso_no_autorizado",
                            detalle=f"{r.chunk.chunk_id}: {motivo}",
                            accion="eliminar",
                        )
                    )
                    continue
                if r.chunk.chunk_id in vistos:
                    continue
                if len(recuperados) >= self._max_contexto:
                    por_tope += 1
                    continue
                vistos.add(r.chunk.chunk_id)
                recuperados.append(r)
                nuevos.append(r)
            descartados_total += sin_acceso
            partes = [resultado.nota] if resultado.nota else []
            if nuevos:
                partes.append(_resumen(nuevos))
            elif resultado.chunks and not (por_tope or sin_acceso):
                partes.append("Sin resultados nuevos.")
            if por_tope:
                partes.append(
                    f"Límite de contexto alcanzado ({self._max_contexto} fragmentos): "
                    f"{por_tope} descartados. Responde con lo que ya tienes."
                )
            if sin_acceso and not nuevos and not resultado.nota:
                # Mismo mensaje que "no existe": no se revela que había algo sin acceso.
                partes.append(SIN_ACCESO)
            contenido = "\n\n".join(partes) or "Sin resultados."
            mensajes.append(
                {"role": "tool", "tool_call_id": llamada.tool_call_id, "content": contenido}
            )
        return {
            "recuperados": recuperados,
            "mensajes": mensajes,
            "por_revisar": [],
            "hallazgos": hallazgos,
            "fragmentos_descartados": descartados_total,
        }

    def _ejecutar(self, nombre: str, argumentos: str, estado: EstadoAgente) -> ResultadoHerramienta:
        herramienta = self._herramientas.get(nombre)
        if herramienta is None:
            return ResultadoHerramienta(nota=f"Error: la herramienta '{nombre}' no existe.")
        try:
            args = herramienta.args_model.model_validate_json(argumentos)
        except ValidationError as exc:
            return ResultadoHerramienta(
                nota=f"Error: argumentos no válidos ({exc.error_count()} errores)."
            )
        return herramienta.ejecutar(args, estado.usuario, estado.top_k)

    @staticmethod
    def _tras_cache(estado: EstadoAgente) -> Literal["output_guardrail", "supervisor"]:
        return "output_guardrail" if estado.desde_cache else "supervisor"

    def _cachear(self, estado: EstadoAgente) -> bool:
        # Con historial la respuesta depende del contexto de la conversación: no se cachea.
        return self._cache is not None and not estado.historial

    def _cache_lookup(self, estado: EstadoAgente) -> Update:
        if not self._cachear(estado):
            return {}
        [vector] = self._embedder_cache.embed([estado.pregunta])
        entrada = self._cache.buscar(vector, self._alcance_cache(estado.usuario.groups))
        if entrada is None:
            return {"vector_pregunta": vector}
        return {
            "vector_pregunta": vector,
            "desde_cache": True,
            "respuesta": entrada.respuesta,
            "documentos_cache": entrada.documentos_consultados,
        }

    def _cache_store(self, estado: EstadoAgente) -> Update:
        bloqueada = any(h.accion == "bloquear" for h in estado.hallazgos)
        # Los datos internos no forman parte de la huella de la caché: no se cachean.
        usa_datos = any(r.chunk.doc_id.startswith(PREFIJO_DATOS) for r in estado.recuperados)
        if (
            not self._cachear(estado)
            or estado.desde_cache
            or bloqueada
            or usa_datos
            or not estado.vector_pregunta
        ):
            return {}
        self._cache.guardar(
            EntradaCache(
                # Alcance recalculado ahora: si algo cambió durante la consulta, no coincidirá.
                alcance=self._alcance_cache(estado.usuario.groups),
                pregunta=estado.pregunta,
                vector=estado.vector_pregunta,
                respuesta=_requerir_respuesta(estado),
                documentos_consultados=_documentos_consultados(estado),
            )
        )
        return {}

    def _generate(self, estado: EstadoAgente) -> Update:
        return {
            "respuesta": generar_respuesta(
                self._llm, estado.pregunta, estado.recuperados, estado.historial
            )
        }

    def _output_guardrail(self, estado: EstadoAgente) -> Update:
        respuesta = _requerir_respuesta(estado)
        veredicto = self._guardrail_salida.revisar(respuesta.respuesta)
        return {
            "respuesta": (
                respuesta.model_copy(update={"respuesta": veredicto.texto})
                if veredicto.permitido
                else _bloqueada()
            ),
            "hallazgos": [*estado.hallazgos, *veredicto.hallazgos],
        }

    def _audit(self, estado: EstadoAgente) -> Update:
        registrar_consulta(
            estado.usuario, estado.pregunta, _requerir_respuesta(estado), estado.hallazgos
        )
        return {}


def _documentos_consultados(estado: EstadoAgente) -> list[str]:
    if estado.desde_cache:
        return list(estado.documentos_cache)
    return list(
        dict.fromkeys(r.chunk.doc_id for r in estado.recuperados if r.chunk.doc_id != "catalogo")
    )


def _pregunta_supervisor(estado: EstadoAgente) -> str:
    """Con historial, el supervisor ve los turnos previos para buscar bien las preguntas de
    seguimiento; sin historial, la pregunta tal cual."""
    if not estado.historial:
        return estado.pregunta
    pregunta = neutralizar(estado.pregunta)
    return f"{build_historial(estado.historial)}<pregunta>\n{pregunta}\n</pregunta>"


def _requerir_respuesta(estado: EstadoAgente) -> RespuestaConsulta:
    if estado.respuesta is None:
        raise RuntimeError("El grafo llegó a un nodo final sin respuesta fijada")
    return estado.respuesta


def _resumen(recuperados: list[ChunkRecuperado], max_chars: int = 400) -> str:
    return "\n\n".join(f"- ({r.chunk.fuente}) {r.chunk.contenido[:max_chars]}" for r in recuperados)
