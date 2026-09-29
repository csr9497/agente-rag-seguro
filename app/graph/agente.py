"""Agente LangGraph:

    authorize → input_guardrail → supervisor ⇄ tools → generate → output_guardrail → audit

authorize e input_guardrail pueden cortar el flujo directamente hacia audit: toda consulta
se audita, también las rechazadas.
"""

import json
import uuid
from collections.abc import Callable
from datetime import date
from typing import Any, Literal

import openai
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langsmith import Client, traceable
from langsmith.run_helpers import get_tracing_context
from pydantic import BaseModel, Field, ValidationError

from app.acciones.modelos import PropuestaAccion
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
from app.security.guardrails import MENSAJE_BLOQUEO, Guardrail, Veredicto
from app.security.versiones import ContextoAgente
from app.tools.base import SIN_ACCESO, Herramienta, ResultadoHerramienta, schema_openai
from app.tools.conversacion import PLANTILLAS

Update = dict[str, Any]

# Herramientas que ya deciden la respuesta: tras ellas no hace falta otro turno del supervisor.
HERRAMIENTAS_TERMINALES = {"conversacion", "pedir_aclaracion", "proponer_accion"}


class ResultadoAgente(BaseModel):
    respuesta: RespuestaConsulta
    pregunta_procesada: str = Field(description="Tras input_guardrail (PII enmascarada)")
    documentos_consultados: list[str]
    fragmentos_descartados: int
    hallazgos: list[Hallazgo]
    traza_id: str
    desde_cache: bool = False
    acciones: list[PropuestaAccion] = Field(default_factory=list)
    consultas: list[str] = Field(default_factory=list, description="Consultas curadas enviadas")


def _bloqueada(mensaje: str | None = None) -> RespuestaConsulta:
    return RespuestaConsulta(respuesta=mensaje or MENSAJE_BLOQUEO, citas=[], sin_contexto=True)


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
        versiones_entrada: dict[str, Guardrail] | None = None,
        versiones_salida: dict[str, Guardrail] | None = None,
        version_entrada: str = "configurado",
        version_salida: str = "configurado",
    ) -> None:
        self._supervisor = supervisor
        self._llm = llm
        self._herramientas = {h.nombre: h for h in herramientas}
        self._schemas = [schema_openai(h) for h in herramientas]
        self._guardrail_entrada = guardrail_entrada
        self._guardrail_salida = guardrail_salida
        # Versiones seleccionables (Studio). Sin catálogo, solo la instancia configurada.
        self._version_entrada, self._version_salida = version_entrada, version_salida
        self._versiones = {
            "entrada": versiones_entrada or {version_entrada: guardrail_entrada},
            "salida": versiones_salida or {version_salida: guardrail_salida},
        }
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
        traza_id = uuid.uuid4()
        inicial = EstadoAgente(
            pregunta=pregunta,
            usuario=usuario,
            top_k=top_k or self._top_k,
            historial=historial or [],
            traza_id=str(traza_id),
            conversacion_id=conversacion_id,
        )
        with traza_consulta(
            self._trazas, self._settings, roles=usuario.groups, conversacion_id=conversacion_id
        ) as config:
            config["metadata"]["usuario"] = usuario_seudonimo(usuario.id)
            config["metadata"]["guardrail_entrada"] = self._version_entrada
            config["metadata"]["guardrail_salida"] = self._version_salida
            config["run_id"] = traza_id
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
            acciones=final.acciones_propuestas,
            consultas=final.consultas,
        )

    # ------------------------------------------------------------------ construcción
    def grafo_con_entrada(
        self, input_schema: type[BaseModel], state_schema: type[EstadoAgente] = EstadoAgente
    ) -> CompiledStateGraph:
        """Mismo grafo con otro esquema de entrada y de estado (Studio: rol como desplegable y
        usuario de prueba por defecto)."""
        return self._construir(input_schema, state_schema)

    def _construir(
        self,
        input_schema: type[BaseModel] | None = None,
        state_schema: type[EstadoAgente] = EstadoAgente,
    ) -> CompiledStateGraph:
        g = StateGraph(state_schema, context_schema=ContextoAgente, input_schema=input_schema)
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

        if state_schema is EstadoAgente:
            g.add_edge(START, "authorize")
        else:
            # Studio: completa lo que el formulario no envía (usuario de prueba, top_k) con los
            # valores por defecto de su esquema de estado, antes de autorizar.
            def entrada_studio(estado: Any) -> Update:
                return {"usuario": estado.usuario, "top_k": estado.top_k}

            entrada_studio.__annotations__["estado"] = state_schema
            g.add_node("entrada_studio", entrada_studio)
            g.add_edge(START, "entrada_studio")
            g.add_edge("entrada_studio", "authorize")
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
        g.add_conditional_edges(
            "access_guardrail", self._tras_access_guardrail, ["supervisor", "generate"]
        )
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

    @staticmethod
    def _tras_access_guardrail(estado: EstadoAgente) -> Literal["supervisor", "generate"]:
        """Si el último turno solo usó herramientas terminales (saludo, aclaración, propuesta
        de acción), el supervisor no tiene nada más que buscar: se ahorra una llamada al LLM."""
        ultimo = next((m for m in reversed(estado.mensajes) if m.get("role") == "assistant"), {})
        nombres = {tc["function"]["name"] for tc in ultimo.get("tool_calls", [])}
        return "generate" if nombres and nombres <= HERRAMIENTAS_TERMINALES else "supervisor"

    # ------------------------------------------------------------------ nodos
    def _authorize(self, estado: EstadoAgente) -> Update:
        # Deny by default: sin grupos no hay nada visible; no se gasta ni una llamada al LLM.
        if not estado.usuario.groups:
            return {"respuesta": respuesta_sin_contexto()}
        return {}

    def _elegir_guardrail(
        self, tipo: Literal["entrada", "salida"], runtime: Runtime[ContextoAgente] | None
    ) -> tuple[str, Guardrail]:
        """Versión pedida en el contexto (Studio) o la configurada."""
        contexto = runtime.context if runtime is not None else None
        pedida = getattr(contexto, f"guardrail_{tipo}", None) if contexto else None
        nombre = pedida or (self._version_entrada if tipo == "entrada" else self._version_salida)
        catalogo = self._versiones[tipo]
        if nombre not in catalogo:
            raise ValueError(
                f"Guardrail de {tipo} '{nombre}' no disponible en este entorno. "
                f"Disponibles: {', '.join(catalogo)}"
            )
        return nombre, catalogo[nombre]

    def _input_guardrail(self, estado: EstadoAgente, runtime: Runtime[ContextoAgente]) -> Update:
        version, guardrail = self._elegir_guardrail("entrada", runtime)
        veredicto = _revisar_con_traza("entrada", version, guardrail, estado.pregunta)
        update: Update = {
            "pregunta": veredicto.texto,
            "hallazgos": [*estado.hallazgos, *veredicto.hallazgos],
            "versiones_guardrails": {**estado.versiones_guardrails, "entrada": version},
        }
        if not veredicto.permitido:
            update["respuesta"] = _bloqueada(veredicto.mensaje)
        return update

    def _supervisor_node(self, estado: EstadoAgente) -> Update:
        if estado.iteraciones >= self._max_iteraciones:
            return {"pendientes": []}
        mensajes = estado.mensajes or [
            # Con la fecha, "este año" o "los próximos festivos" no se resuelven con un año viejo.
            {"role": "system", "content": f"{SUPERVISOR_PROMPT}\nFecha de hoy: {date.today()}."},
            {"role": "user", "content": _pregunta_supervisor(estado)},
        ]
        # Primer turno: obligatorio usar una herramienta (con gpt-4o y tool_choice=auto, a veces
        # respondía "no encuentro" sin haber buscado).
        try:
            decision = self._supervisor.decidir(
                mensajes, self._schemas, obligar_herramienta=estado.iteraciones == 0
            )
        except openai.BadRequestError as exc:
            if not _es_filtro_de_contenido(exc):
                raise
            return {**_bloqueo_por_filtro(estado), "pendientes": []}
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
        consultas = list(estado.consultas)
        for llamada in estado.pendientes:
            try:
                consulta = json.loads(llamada.argumentos).get("consulta")
            except (ValueError, AttributeError):
                consulta = None
            if isinstance(consulta, str) and consulta and consulta not in consultas:
                consultas.append(consulta)
        return {"por_revisar": resultados, "pendientes": [], "consultas": consultas}

    def _access_guardrail(self, estado: EstadoAgente) -> Update:
        """Verifica cada fragmento contra el registro; acumula contexto y compone los mensajes
        de tool para el supervisor solo con lo autorizado."""
        recuperados = list(estado.recuperados)
        vistos = {r.chunk.chunk_id for r in recuperados}
        mensajes = list(estado.mensajes)
        hallazgos = list(estado.hallazgos)
        descartados_total = estado.fragmentos_descartados
        acciones = list(estado.acciones_propuestas)
        conversacion = estado.conversacion
        aclaracion = estado.aclaracion
        for llamada in estado.por_revisar:
            resultado = llamada.resultado
            acciones += [a for a in resultado.acciones if a.rol_id in estado.usuario.groups]
            conversacion = resultado.conversacion or conversacion
            aclaracion = resultado.aclaracion or aclaracion
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
            "acciones_propuestas": acciones,
            "conversacion": conversacion,
            "aclaracion": aclaracion,
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
            or estado.acciones_propuestas
            or estado.conversacion
            or estado.aclaracion
            or _requerir_respuesta(estado).sin_contexto  # un «no encuentro» no se reutiliza
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
        if estado.respuesta is not None:  # ya bloqueada por el filtro de contenido del modelo
            return {}
        acciones = estado.acciones_propuestas
        if estado.aclaracion and not estado.recuperados and not acciones:
            # Consulta imprecisa: se pregunta al usuario en lugar de buscar a ciegas.
            return {
                "respuesta": RespuestaConsulta(
                    respuesta=estado.aclaracion.pregunta,
                    citas=[],
                    sin_contexto=True,
                    aclaracion=estado.aclaracion,
                )
            }
        if estado.conversacion in PLANTILLAS and not estado.recuperados and not acciones:
            # Saludo, agradecimiento, ayuda…: plantilla fija, sin LLM y sin inventar contenido.
            texto = PLANTILLAS[estado.conversacion]
            return {
                "respuesta": RespuestaConsulta(
                    respuesta=texto, citas=[], sin_contexto=True, conversacional=True
                )
            }
        if acciones and not estado.recuperados:
            # Solo acciones: respuesta por plantilla (sin LLM); la UI muestra las tarjetas.
            lista = "; ".join(a.resumen for a in acciones)
            texto = f"He preparado lo siguiente para que lo revises y apruebes: {lista}."
            return {"respuesta": RespuestaConsulta(respuesta=texto, citas=[], sin_contexto=True)}
        try:
            respuesta = generar_respuesta(
                self._llm, estado.pregunta, estado.recuperados, estado.historial
            )
        except openai.BadRequestError as exc:
            if not _es_filtro_de_contenido(exc):
                raise
            return _bloqueo_por_filtro(estado)
        return {"respuesta": respuesta}

    def _output_guardrail(self, estado: EstadoAgente, runtime: Runtime[ContextoAgente]) -> Update:
        respuesta = _requerir_respuesta(estado)
        version, guardrail = self._elegir_guardrail("salida", runtime)
        veredicto = _revisar_con_traza("salida", version, guardrail, respuesta.respuesta)
        return {
            "versiones_guardrails": {**estado.versiones_guardrails, "salida": version},
            "respuesta": (
                respuesta.model_copy(update={"respuesta": veredicto.texto})
                if veredicto.permitido
                else _bloqueada()
            ),
            "hallazgos": [*estado.hallazgos, *veredicto.hallazgos],
        }

    def _audit(self, estado: EstadoAgente) -> Update:
        registrar_consulta(
            estado.usuario,
            estado.pregunta,
            _requerir_respuesta(estado),
            estado.hallazgos,
            traza_id=estado.traza_id,
            conversacion_id=estado.conversacion_id,
            documentos_consultados=_documentos_consultados(estado),
            desde_cache=estado.desde_cache,
            guardrails=estado.versiones_guardrails,
        )
        return {}


def _revisar_con_traza(tipo: str, version: str, guardrail: Guardrail, texto: str) -> Veredicto:
    """Cada guardrail es un paso propio en LangSmith («guardrail_entrada · v3-politicas»), con
    etiqueta y versión para filtrar. Usa el cliente de la consulta (enmascarado en prod)."""
    contexto = get_tracing_context()
    if not contexto.get("enabled") or not isinstance(contexto.get("client"), Client):
        return guardrail.revisar(texto)  # sin traza activa: la revisión nunca depende de ella
    revisar = traceable(
        name=f"guardrail_{tipo} · {version}",
        run_type="chain",
        tags=["guardrail", f"guardrail_{tipo}:{version}"],
        metadata={"guardrail": tipo, "version": version},
    )(guardrail.revisar)
    return revisar(texto)


def _es_filtro_de_contenido(exc: openai.BadRequestError) -> bool:
    """Azure OpenAI rechaza la petición por su filtro de contenido (p. ej. jailbreak)."""
    return getattr(exc, "code", None) == "content_filter" or "content_filter" in str(exc)


def _bloqueo_por_filtro(estado: EstadoAgente) -> Update:
    """Otra capa de defensa (la del proveedor): se trata como una consulta bloqueada y se
    audita, en lugar de acabar en un error 502."""
    hallazgo = Hallazgo(tipo="inyeccion", detalle="filtro_contenido_azure", accion="bloquear")
    return {"respuesta": _bloqueada(), "hallazgos": [*estado.hallazgos, hallazgo]}


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
