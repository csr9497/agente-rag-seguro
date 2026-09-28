import os
from pathlib import Path

# Los tests nunca envían trazas a LangSmith, aunque .env las active (las variables de
# entorno tienen prioridad sobre .env en pydantic-settings).
os.environ["TRAZAS_MODO"] = "apagado"
os.environ["LANGSMITH_TRACING"] = "false"

import pytest
from qdrant_client import QdrantClient

from app.graph.agente import Agente
from app.retrieval.qdrant_retriever import QdrantRetriever
from app.security.guardrails import GuardrailPermisivo
from app.tools.rag_retrieve import RagRetrieve
from ingestor.ingest import ingestar
from ingestor.sources import LocalFolderSource
from tests.fakes import DIM, FakeEmbedder, FakeLLM, FakeSupervisor

SAMPLE_DOCS = Path(__file__).parents[1] / "ingestor" / "sample_docs"


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def supervisor() -> FakeSupervisor:
    return FakeSupervisor()


@pytest.fixture
def retriever() -> QdrantRetriever:
    return QdrantRetriever(QdrantClient(":memory:"), "test", DIM)


@pytest.fixture
def retriever_con_docs(retriever: QdrantRetriever, embedder: FakeEmbedder) -> QdrantRetriever:
    ingestar(LocalFolderSource(SAMPLE_DOCS), embedder, retriever)
    return retriever


@pytest.fixture
def crear_agente(retriever_con_docs, embedder, llm, supervisor):
    """Fábrica de agentes con fakes; los kwargs sustituyen cualquier pieza."""

    def _crear(**kw) -> Agente:
        base = {
            "supervisor": supervisor,
            "llm": llm,
            "herramientas": [RagRetrieve(embedder, retriever_con_docs, kw.pop("min_score", None))],
            "guardrail_entrada": GuardrailPermisivo(),
            "guardrail_salida": GuardrailPermisivo(),
        }
        return Agente(**{**base, **kw})

    return _crear


@pytest.fixture
def agente(crear_agente) -> Agente:
    return crear_agente()
