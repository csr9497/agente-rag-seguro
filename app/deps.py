"""Composición de dependencias según configuración."""

from azure.identity import DefaultAzureCredential
from qdrant_client import QdrantClient

from app.config import Settings
from app.graph.agente import Agente
from app.retrieval.azure_openai import (
    AzureOpenAIEmbedder,
    AzureOpenAILLM,
    AzureOpenAISupervisor,
    build_client,
)
from app.retrieval.base import LLM, Embedder, Retriever, Supervisor
from app.retrieval.no_configurado import ModelosNoConfigurados
from app.security.guardrails import GuardrailPermisivo
from app.tools.rag_retrieve import RagRetrieve


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


def build_agente(settings: Settings) -> Agente:
    embedder, llm, supervisor = build_modelos(settings)
    return Agente(
        supervisor=supervisor,
        llm=llm,
        herramientas=[RagRetrieve(embedder, build_retriever(settings), settings.min_score)],
        guardrail_entrada=GuardrailPermisivo(),
        guardrail_salida=GuardrailPermisivo(),
        top_k=settings.retrieval_top_k,
        max_iteraciones=settings.max_iteraciones,
    )
