import os
from pathlib import Path

# Los tests nunca envían trazas a LangSmith, aunque .env las active (las variables de
# entorno tienen prioridad sobre .env en pydantic-settings).
os.environ["TRAZAS_MODO"] = "apagado"
os.environ["LANGSMITH_TRACING"] = "false"

import pytest
from qdrant_client import QdrantClient

from app.config import Settings

# Tests herméticos: nunca leen .env (tras un ciclo en Azure contiene endpoints y claves reales).
Settings.model_config["env_file"] = None

from app.retrieval.qdrant_retriever import QdrantRetriever  # noqa: E402
from ingestor.ingest import ingestar  # noqa: E402
from ingestor.sources import LocalFolderSource  # noqa: E402
from tests.fakes import DIM, FakeEmbedder, FakeLLM, FakeSupervisor  # noqa: E402

SAMPLE_DOCS = Path(__file__).parents[1] / "ingestor" / "sample_docs"
ROLES_SEMILLA = {
    "administrador", "rrhh", "finanzas", "public",
    "hr_staff", "hr_specialist", "it_support", "auditor",
}  # fmt: skip


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


def conectar_orquestador(servicios, modelos=None):  # noqa: ANN001, ANN201
    """Como el arranque de la app (app/main.py): orquestador con checkpointer en memoria,
    conectado a las conversaciones y visible para /consultar. Modelos falsos por defecto
    (RagEco: delega en rag_agent y cita lo que encuentra)."""
    from langgraph.checkpoint.memory import InMemorySaver

    from app.agents.aprobaciones import SqlRepositorioAprobaciones
    from app.deps import build_orquestador
    from app.main import app
    from tests.fakes import RagEco

    orquestador = build_orquestador(
        servicios, InMemorySaver(), modelos or (FakeEmbedder(), FakeLLM(), RagEco())
    )
    servicios.conversaciones.usar_orquestador(
        orquestador, SqlRepositorioAprobaciones(servicios.motor)
    )
    app.state.orquestador = orquestador
    return orquestador
