from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from app.retrieval.qdrant_retriever import QdrantRetriever
from ingestor.ingest import ingestar
from ingestor.sources import LocalFolderSource
from tests.fakes import DIM, FakeEmbedder, FakeLLM

SAMPLE_DOCS = Path(__file__).parents[1] / "ingestor" / "sample_docs"


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def retriever() -> QdrantRetriever:
    return QdrantRetriever(QdrantClient(":memory:"), "test", DIM)


@pytest.fixture
def retriever_con_docs(retriever: QdrantRetriever, embedder: FakeEmbedder) -> QdrantRetriever:
    ingestar(LocalFolderSource(SAMPLE_DOCS), embedder, retriever)
    return retriever
