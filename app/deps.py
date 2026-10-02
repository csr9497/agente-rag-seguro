"""Composición de dependencias según configuración."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from azure.identity import DefaultAzureCredential
from langsmith import Client as LangSmithClient
from qdrant_client import QdrantClient
from sqlalchemy import Engine

from app.acciones.servicio import ServicioAcciones
from app.agents.aprobaciones import SqlRepositorioAprobaciones
from app.agents.hr import HerramientasHR, SqlRepositorioCasosRRHH, crear_hr_agent
from app.agents.orquestador import Orquestador
from app.agents.rag import HerramientasRag, adaptar_lectura, crear_rag_agent
from app.agents.registry import RegistroAgentes
from app.agents.subgraph import AuditoriaSql
from app.agents.support import HerramientasSoporte, SqlRepositorioTickets, crear_support_agent
from app.cache.semantica import CacheMemoria, CacheRedis, CacheSemantica, alcance_de_permisos
from app.config import Settings
from app.datos.catalogo import CONSULTAS, permisos_por_consulta
from app.graph.agente import Agente
from app.modelos.openai_compat import EmbedderOpenAI, LLMOpenAI, SupervisorOpenAI, build_client
from app.observabilidad import configurar_trazas
from app.persistencia.almacen import AlmacenBlob, AlmacenDocumentos, AlmacenLocal
from app.persistencia.repositorios import (
    RepositorioDocumentos,
    RepositorioRoles,
    SqlRepositorioConversaciones,
    SqlRepositorioDepartamentosUsuario,
    SqlRepositorioDocumentos,
    SqlRepositorioRoles,
    SqlRepositorioSolicitudesAcceso,
    crear_motor,
    inicializar,
)
from app.prompts.registro import RegistroPrompts
from app.rag.catalogo import CatalogoRol, construir_catalogo
from app.retrieval.base import LLM, Embedder, Retriever, Supervisor
from app.retrieval.no_configurado import ModelosNoConfigurados
from app.security.acceso import VerificadorRegistro
from app.security.content_safety import SCOPE as SCOPE_CONTENT_SAFETY
from app.security.content_safety import (
    ClientePromptShields,
    ClienteShields,
)
from app.security.guardrails import GuardrailSalida
from app.security.versiones import catalogo_entrada, catalogo_salida, version_por_defecto
from app.servicios.conversaciones import ServicioConversaciones
from app.servicios.integridad import InformeIntegridad, verificar_integridad
from app.servicios.roles import ServicioRoles
from app.tools.acciones import ProponerAccion
from app.tools.aclaracion import PedirAclaracion
from app.tools.conversacion import ResponderConversacion
from app.tools.datos import DataQuery
from app.tools.documentos import BuscarEnDocumento, LeerDocumento, ListarDocumentos
from app.tools.rag_retrieve import RagRetrieve
from ingestor.gestor import GestorDocumentos


def build_retriever(settings: Settings) -> Retriever:
    if settings.vector_store == "azure_search":
        from azure.core.credentials import AzureKeyCredential

        from app.retrieval.azure_search_retriever import AzureSearchRetriever

        credencial = (
            AzureKeyCredential(settings.azure_search_api_key.get_secret_value())
            if settings.azure_search_api_key
            else DefaultAzureCredential()
        )
        return AzureSearchRetriever(
            settings.azure_search_endpoint,
            settings.azure_search_index,
            credencial,
            settings.embedding_dimensions,
        )
    from app.retrieval.qdrant_retriever import QdrantRetriever

    client = (
        QdrantClient(path=settings.qdrant_path)
        if settings.qdrant_path
        else QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None,
        )
    )
    return QdrantRetriever(client, settings.qdrant_collection, settings.embedding_dimensions)


def build_cache(settings: Settings) -> CacheSemantica:
    if settings.cache_backend == "redis":
        import redis

        cliente = redis.Redis.from_url(settings.redis_url.get_secret_value())
        return CacheRedis(cliente, umbral=settings.cache_umbral, ttl_s=settings.cache_ttl_s)
    return CacheMemoria(umbral=settings.cache_umbral)


def build_shields(settings: Settings) -> ClienteShields | None:
    if not settings.content_safety_endpoint:
        return None
    if settings.content_safety_api_key:
        return ClientePromptShields(
            settings.content_safety_endpoint,
            api_key=settings.content_safety_api_key.get_secret_value(),
        )
    credencial = DefaultAzureCredential()
    return ClientePromptShields(
        settings.content_safety_endpoint,
        obtener_token=lambda: credencial.get_token(SCOPE_CONTENT_SAFETY).token,
    )


def build_almacen(settings: Settings) -> AlmacenDocumentos:
    if settings.almacen_documentos == "blob":
        return AlmacenBlob(settings.azure_storage_account_url, settings.azure_storage_container)
    return AlmacenLocal(Path(settings.almacen_local_dir))


def build_modelos(settings: Settings) -> tuple[Embedder, LLM, Supervisor]:
    """Embeddings, LLM de generación y supervisor del proveedor configurado (Azure OpenAI u
    OpenAI/compatible). Sin configuración, la app arranca igualmente y las consultas
    responden 503 indicando qué falta."""
    if faltan := settings.modelos_faltantes():
        sin_modelos = ModelosNoConfigurados(faltan, settings.modelos_proveedor)
        return sin_modelos, sin_modelos, sin_modelos
    client = build_client(settings)
    proveedor = settings.modelos_proveedor
    return (
        EmbedderOpenAI(
            client, settings.modelo_embeddings, proveedor, settings.embedding_dimensions
        ),
        LLMOpenAI(client, settings.modelo_chat, proveedor),
        SupervisorOpenAI(client, settings.modelo_chat, proveedor),
    )


def build_embedder(settings: Settings) -> Embedder:
    return build_modelos(settings)[0]


@dataclass(frozen=True)
class Servicios:
    agente: Agente
    gestor: GestorDocumentos
    roles: ServicioRoles
    conversaciones: ServicioConversaciones
    registro: RepositorioDocumentos
    repo_roles: RepositorioRoles
    retriever: Retriever
    acciones: ServicioAcciones
    departamentos: SqlRepositorioDepartamentosUsuario
    solicitudes: SqlRepositorioSolicitudesAcceso
    embedder: Embedder
    settings: Settings
    motor: Engine

    def verificar_integridad(self) -> InformeIntegridad:
        return verificar_integridad(self.retriever, self.registro, self.repo_roles)


def build_servicios(
    settings: Settings,
    *,
    modelos: tuple[Embedder, LLM, Supervisor] | None = None,
    retriever: Retriever | None = None,
    shields: ClienteShields | None = None,
) -> Servicios:
    """Composición completa. Agente y gestor comparten embedder, retriever (Qdrant embebido
    solo admite un cliente por proceso) y registro. `modelos`/`retriever` permiten tests."""
    embedder, llm, supervisor = modelos or build_modelos(settings)
    retriever = retriever or build_retriever(settings)
    shields = shields or build_shields(settings)
    motor = crear_motor(settings.database_url)
    inicializar(motor)
    repo_roles = SqlRepositorioRoles(motor)
    registro = SqlRepositorioDocumentos(motor)
    departamentos = SqlRepositorioDepartamentosUsuario(motor)
    departamentos.iniciales(settings.departamentos_iniciales)
    cache, alcance = None, None
    if settings.cache_semantica:
        version = f"{settings.modelos_proveedor}:{settings.modelo_chat}:{settings.app_version}"
        cache = build_cache(settings)

        def alcance(roles: list[str]) -> str:
            return alcance_de_permisos(registro, roles, version)

    catalogo = catalogo_de(registro, repo_roles)

    agente = _agente(
        settings, embedder, llm, supervisor, retriever, registro,
        cache=cache, alcance_cache=alcance, motor=motor, shields=shields, catalogo=catalogo,
    )  # fmt: skip
    roles = ServicioRoles(repo_roles, settings.seleccion_libre_de_rol)
    roles.asignaciones_iniciales(settings.asignaciones_iniciales)
    return Servicios(
        agente=agente,
        gestor=GestorDocumentos(
            embedder,
            retriever,
            registro=registro,
            roles=repo_roles,
            almacen=build_almacen(settings),
            shields=shields,
            shields_fallo=settings.content_safety_fallo,
            departamentos=departamentos.existentes,
        ),
        roles=roles,
        conversaciones=ServicioConversaciones(
            SqlRepositorioConversaciones(motor),
            roles,
            agente,
            settings.max_turnos_historial,
            departamentos_de=departamentos.de,
        ),
        registro=registro,
        repo_roles=repo_roles,
        retriever=retriever,
        acciones=ServicioAcciones(motor),
        departamentos=departamentos,
        solicitudes=SqlRepositorioSolicitudesAcceso(motor),
        embedder=embedder,
        settings=settings,
        motor=motor,
    )


def catalogo_de(
    registro: RepositorioDocumentos, repo_roles: RepositorioRoles
) -> Callable[[list[str]], CatalogoRol]:
    def catalogo(grupos: list[str]) -> CatalogoRol:
        """Lo que el usuario puede consultar, desde la fuente de verdad de los permisos."""
        return construir_catalogo(
            grupos,
            [(d.doc_id, d.titulo, d.roles) for d in registro.listar(estado="activo")],
            {r.id: (r.nombre, r.descripcion) for r in repo_roles.listar(incluir_inactivos=False)},
            [(c.descripcion, list(c.roles)) for c in CONSULTAS.values()],
        )

    return catalogo


def build_orquestador(
    servicios: Servicios,
    checkpointer: Any = None,
    modelos: tuple[Embedder, LLM, Supervisor] | None = None,
) -> Orquestador:
    """Orquestador multiagente con la composición real (fase 5). `checkpointer=None` en
    LangGraph Studio (lo pone el servidor de desarrollo)."""
    settings = servicios.settings
    _, llm, supervisor = modelos or build_modelos(settings)
    shields = build_shields(settings)
    entrada = catalogo_entrada(settings, shields)[version_por_defecto(settings, shields)]
    salida = catalogo_salida(settings)[settings.guardrail_salida]
    version = f"{settings.modelos_proveedor}:{settings.modelo_chat}:{settings.app_version}"
    registro = servicios.registro
    return Orquestador(
        registro=build_registro_agentes(servicios), supervisor=supervisor,
        llm_de_agente=lambda _nombre: supervisor, llm_sintesis=llm,
        guardrail_entrada=entrada, guardrail_salida=salida,
        roles_de=lambda uid: set(servicios.repo_roles.roles_de_usuario(uid)),
        checkpointer=checkpointer, auditoria=AuditoriaSql(servicios.motor),
        aprobaciones=SqlRepositorioAprobaciones(servicios.motor),
        alcance=lambda user: alcance_de_permisos(registro, list(user.acl), version),
        catalogo=catalogo_de(registro, servicios.repo_roles),
    )  # fmt: skip


def build_registro_agentes(servicios: Servicios) -> RegistroAgentes:
    """Agentes de la orquestación multiagente (ver app/agents/). Agregar uno es registrarlo
    aquí: el grafo principal no cambia."""
    registro = RegistroAgentes()
    rag = HerramientasRag(
        servicios.embedder, servicios.retriever, servicios.registro,
        VerificadorRegistro(servicios.registro, permisos_por_consulta()),
        servicios.solicitudes,
        top_k=servicios.settings.retrieval_top_k, min_score=servicios.settings.min_score,
    )  # fmt: skip
    verificador = VerificadorRegistro(servicios.registro, permisos_por_consulta())
    top_k = servicios.settings.retrieval_top_k
    lectura = {
        h.nombre: adaptar_lectura(h, verificador, servicios.registro, top_k)
        for h in (
            DataQuery(servicios.motor),
            ListarDocumentos(servicios.retriever, servicios.registro),
            LeerDocumento(servicios.retriever),
            BuscarEnDocumento(servicios.embedder, servicios.retriever),
        )
    }
    registro.register(crear_rag_agent(rag, lectura))
    # Casos de RR.HH. solo con RLS (PostgreSQL): sin ella, el agente no existe (fallo cerrado).
    if servicios.motor.dialect.name == "postgresql":
        casos = SqlRepositorioCasosRRHH(servicios.motor)
        registro.register(crear_hr_agent(HerramientasHR(casos, rag, servicios.registro)))
        tickets = SqlRepositorioTickets(servicios.motor)
        registro.register(crear_support_agent(HerramientasSoporte(tickets, rag)))
    return registro


def build_prompts(settings: Settings) -> RegistroPrompts:
    """Prompts del repositorio o de LangSmith (PROMPTS_ORIGEN; en Studio, por ejecución)."""
    clave = settings.langsmith_api_key.get_secret_value() if settings.langsmith_api_key else ""
    cliente = LangSmithClient(api_key=clave) if clave else None
    return RegistroPrompts(settings, cliente)


def build_agente(settings: Settings) -> Agente:
    """Agente sin registro (LangGraph Studio y /consultar): verificación solo por ACL."""
    embedder, llm, supervisor = build_modelos(settings)
    retriever = build_retriever(settings)

    def catalogo(grupos: list[str]) -> CatalogoRol:
        """Sin registro: los documentos del índice visibles para esos grupos."""
        documentos = [(d.doc_id, d.doc_id, d.acl_groups) for d in retriever.list_documents(grupos)]
        return construir_catalogo(grupos, documentos, {}, [])

    return _agente(
        settings, embedder, llm, supervisor, retriever,
        shields=build_shields(settings), catalogo=catalogo,
    )  # fmt: skip


def _agente(
    settings: Settings,
    embedder: Embedder,
    llm: LLM,
    supervisor: Supervisor,
    retriever: Retriever,
    registro: RepositorioDocumentos | None = None,
    cache: CacheSemantica | None = None,
    alcance_cache: Callable[[list[str]], str] | None = None,
    motor: Engine | None = None,
    shields: ClienteShields | None = None,
    catalogo: Callable[[list[str]], CatalogoRol] | None = None,
) -> Agente:
    versiones_entrada = catalogo_entrada(settings, shields)
    versiones_salida = catalogo_salida(settings)
    version_entrada = version_por_defecto(settings, shields)
    prompts = build_prompts(settings)
    for guardrail in versiones_salida.values():
        if isinstance(guardrail, GuardrailSalida):  # también vigila versiones de LangSmith
            prompts.al_cargar(guardrail.proteger)
    return Agente(
        trazas=configurar_trazas(settings),
        settings=settings,
        supervisor=supervisor,
        llm=llm,
        herramientas=[
            RagRetrieve(embedder, retriever, settings.min_score),
            ResponderConversacion(),
            PedirAclaracion(),
            ListarDocumentos(retriever, registro),
            BuscarEnDocumento(embedder, retriever),
            LeerDocumento(retriever),
            *(
                [DataQuery(motor), ProponerAccion(ServicioAcciones(motor))]
                if motor is not None
                else []
            ),
        ],
        guardrail_entrada=versiones_entrada[version_entrada],
        guardrail_salida=versiones_salida[settings.guardrail_salida],
        versiones_entrada=versiones_entrada,
        versiones_salida=versiones_salida,
        version_entrada=version_entrada,
        version_salida=settings.guardrail_salida,
        top_k=settings.retrieval_top_k,
        max_iteraciones=settings.max_iteraciones,
        max_contexto=settings.max_fragmentos_contexto,
        verificador=VerificadorRegistro(registro, permisos_por_consulta()) if registro else None,
        cache=cache,
        embedder_cache=embedder if cache is not None else None,
        alcance_cache=alcance_cache,
        prompts=prompts,
        catalogo=catalogo,
    )
