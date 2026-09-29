"""Composición de dependencias según configuración."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from azure.identity import DefaultAzureCredential
from qdrant_client import QdrantClient
from sqlalchemy import Engine

from app.acciones.servicio import ServicioAcciones
from app.cache.semantica import CacheMemoria, CacheRedis, CacheSemantica, alcance_de_permisos
from app.config import Settings
from app.datos.catalogo import permisos_por_consulta
from app.graph.agente import Agente
from app.graph.prompts import SUPERVISOR_PROMPT
from app.observabilidad import configurar_trazas
from app.persistencia.almacen import AlmacenBlob, AlmacenDocumentos, AlmacenLocal
from app.persistencia.repositorios import (
    RepositorioDocumentos,
    RepositorioRoles,
    SqlRepositorioConversaciones,
    SqlRepositorioDocumentos,
    SqlRepositorioRoles,
    crear_motor,
    inicializar,
)
from app.rag.prompts import SYSTEM_PROMPT
from app.retrieval.azure_openai import (
    AzureOpenAIEmbedder,
    AzureOpenAILLM,
    AzureOpenAISupervisor,
    build_client,
)
from app.retrieval.base import LLM, Embedder, Retriever, Supervisor
from app.retrieval.no_configurado import ModelosNoConfigurados
from app.security.acceso import VerificadorRegistro
from app.security.guardrails import GuardrailEntrada, GuardrailSalida
from app.servicios.conversaciones import ServicioConversaciones
from app.servicios.integridad import InformeIntegridad, verificar_integridad
from app.servicios.roles import ServicioRoles
from app.tools.acciones import ProponerAccion
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


def build_almacen(settings: Settings) -> AlmacenDocumentos:
    if settings.almacen_documentos == "blob":
        return AlmacenBlob(settings.azure_storage_account_url, settings.azure_storage_container)
    return AlmacenLocal(Path(settings.almacen_local_dir))


def build_modelos(settings: Settings) -> tuple[Embedder, LLM, Supervisor]:
    """Embeddings, LLM de generación y supervisor. Sin endpoint, la app arranca igualmente y
    las consultas responden 503 indicando qué falta."""
    if not settings.azure_openai_endpoint:
        faltan = ModelosNoConfigurados(["AZURE_OPENAI_ENDPOINT"])
        return faltan, faltan, faltan
    client = build_client(settings)
    return (
        AzureOpenAIEmbedder(client, settings.azure_openai_embedding_deployment),
        AzureOpenAILLM(client, settings.azure_openai_chat_deployment),
        AzureOpenAISupervisor(client, settings.azure_openai_chat_deployment),
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

    def verificar_integridad(self) -> InformeIntegridad:
        return verificar_integridad(self.retriever, self.registro, self.repo_roles)


def build_servicios(
    settings: Settings,
    *,
    modelos: tuple[Embedder, LLM, Supervisor] | None = None,
    retriever: Retriever | None = None,
) -> Servicios:
    """Composición completa. Agente y gestor comparten embedder, retriever (Qdrant embebido
    solo admite un cliente por proceso) y registro. `modelos`/`retriever` permiten tests."""
    embedder, llm, supervisor = modelos or build_modelos(settings)
    retriever = retriever or build_retriever(settings)
    motor = crear_motor(settings.database_url)
    inicializar(motor)
    repo_roles = SqlRepositorioRoles(motor)
    registro = SqlRepositorioDocumentos(motor)
    cache, alcance = None, None
    if settings.cache_semantica:
        version = f"{settings.azure_openai_chat_deployment}:{settings.app_version}"
        cache = build_cache(settings)

        def alcance(roles: list[str]) -> str:
            return alcance_de_permisos(registro, roles, version)

    agente = _agente(
        settings, embedder, llm, supervisor, retriever, registro,
        cache=cache, alcance_cache=alcance, motor=motor,
    )  # fmt: skip
    roles = ServicioRoles(repo_roles, settings.seleccion_libre_de_rol)
    return Servicios(
        agente=agente,
        gestor=GestorDocumentos(
            embedder,
            retriever,
            registro=registro,
            roles=repo_roles,
            almacen=build_almacen(settings),
        ),
        roles=roles,
        conversaciones=ServicioConversaciones(
            SqlRepositorioConversaciones(motor), roles, agente, settings.max_turnos_historial
        ),
        registro=registro,
        repo_roles=repo_roles,
        retriever=retriever,
        acciones=ServicioAcciones(motor),
    )


def build_agente(settings: Settings) -> Agente:
    """Agente sin registro (LangGraph Studio y /consultar): verificación solo por ACL."""
    embedder, llm, supervisor = build_modelos(settings)
    return _agente(settings, embedder, llm, supervisor, build_retriever(settings))


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
) -> Agente:
    return Agente(
        trazas=configurar_trazas(settings),
        settings=settings,
        supervisor=supervisor,
        llm=llm,
        herramientas=[
            RagRetrieve(embedder, retriever, settings.min_score),
            ListarDocumentos(retriever, registro),
            BuscarEnDocumento(embedder, retriever),
            LeerDocumento(retriever),
            *(
                [DataQuery(motor), ProponerAccion(ServicioAcciones(motor))]
                if motor is not None
                else []
            ),
        ],
        guardrail_entrada=GuardrailEntrada(),
        guardrail_salida=GuardrailSalida([SYSTEM_PROMPT, SUPERVISOR_PROMPT]),
        top_k=settings.retrieval_top_k,
        max_iteraciones=settings.max_iteraciones,
        max_contexto=settings.max_fragmentos_contexto,
        verificador=VerificadorRegistro(registro, permisos_por_consulta()) if registro else None,
        cache=cache,
        embedder_cache=embedder if cache is not None else None,
        alcance_cache=alcance_cache,
    )
