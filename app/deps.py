"""Composición de dependencias según configuración."""

from azure.identity import DefaultAzureCredential
from qdrant_client import QdrantClient

from app.config import Settings
from app.rag.pipeline import RAGPipeline
from app.retrieval.azure_openai import AzureOpenAIEmbedder, AzureOpenAILLM, build_client
from app.retrieval.base import Retriever


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

    return QdrantRetriever(
        QdrantClient(url=settings.qdrant_url),
        settings.qdrant_collection,
        settings.embedding_dimensions,
    )


def build_embedder(settings: Settings) -> AzureOpenAIEmbedder:
    return AzureOpenAIEmbedder(build_client(settings), settings.azure_openai_embedding_deployment)


def build_pipeline(settings: Settings) -> RAGPipeline:
    client = build_client(settings)
    return RAGPipeline(
        embedder=AzureOpenAIEmbedder(client, settings.azure_openai_embedding_deployment),
        retriever=build_retriever(settings),
        llm=AzureOpenAILLM(client, settings.azure_openai_chat_deployment),
        top_k=settings.retrieval_top_k,
        min_score=settings.min_score,
    )
