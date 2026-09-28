"""Composición de dependencias según configuración."""

from dataclasses import dataclass

from azure.identity import DefaultAzureCredential
from qdrant_client import QdrantClient

from app.config import Settings
from app.graph.agente import Agente
from app.graph.prompts import SUPERVISOR_PROMPT
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
from app.tools.documentos import BuscarEnDocumento, LeerDocumento, ListarDocumentos
from app.tools.rag_retrieve import RagRetrieve
from ingestor.gestor import GestorDocumentos


def build_retriever(settings: Settings) -> Retriever:
    if settings.vector_store == "azure_search":
        from app.retrieval.azure_search_retriever import AzureSearchRetriever

        return AzureSearchRetriever(
            settings.azure_search_endpoint,
            settings.azure_search_index,
            DefaultAzureCredential(),
            settings.embedding_dimensions,
        )
    from app.retrieval.qdrant_retriever import QdrantRetriever

    client = (
        QdrantClient(path=settings.qdrant_path)
        if settings.qdrant_path
        else QdrantClient(url=settings.qdrant_url)
    )
    return QdrantRetriever(client, settings.qdrant_collection, settings.embedding_dimensions)


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
    agente = _agente(settings, embedder, llm, supervisor, retriever, registro)
    roles = ServicioRoles(repo_roles, settings.seleccion_libre_de_rol)
    return Servicios(
        agente=agente,
        gestor=GestorDocumentos(embedder, retriever, registro=registro, roles=repo_roles),
        roles=roles,
        conversaciones=ServicioConversaciones(SqlRepositorioConversaciones(motor), roles, agente),
        registro=registro,
        repo_roles=repo_roles,
        retriever=retriever,
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
) -> Agente:
    return Agente(
        supervisor=supervisor,
        llm=llm,
        herramientas=[
            RagRetrieve(embedder, retriever, settings.min_score),
            ListarDocumentos(retriever, registro),
            BuscarEnDocumento(embedder, retriever),
            LeerDocumento(retriever),
        ],
        guardrail_entrada=GuardrailEntrada(),
        guardrail_salida=GuardrailSalida([SYSTEM_PROMPT, SUPERVISOR_PROMPT]),
        top_k=settings.retrieval_top_k,
        max_iteraciones=settings.max_iteraciones,
        max_contexto=settings.max_fragmentos_contexto,
        verificador=VerificadorRegistro(registro) if registro else None,
    )
