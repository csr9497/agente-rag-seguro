"""App con la composición real (registro, roles, permisos, guardrails, caché, tools) salvo LLM y
embeddings, que son simulados. Sirve la API en /api y la web en /. Para CI y pruebas locales:

    uv run uvicorn tests.integration.servidor_simulado:app --port 8767
    uv run python -m evals.ejecutar --base-url http://localhost:8767/api
"""

import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from qdrant_client import QdrantClient

from app.config import Settings
from app.deps import build_servicios
from app.main import app as api
from app.models.schemas import DecisionSupervisor, ToolCall
from app.retrieval.qdrant_retriever import QdrantRetriever
from ingestor.sources import LocalFolderSource
from tests.fakes import DIM, FakeEmbedder, FakeLLM

RAIZ = Path(__file__).parents[2]


class SupervisorPorPalabras:
    """Elige la tool por palabras de la pregunta actual (no del historial)."""

    def decidir(self, mensajes, herramientas) -> DecisionSupervisor:  # noqa: ANN001
        if any(m["role"] == "assistant" for m in mensajes):
            return DecisionSupervisor(
                tool_calls=[], mensaje_asistente={"role": "assistant", "content": "LISTO"}
            )
        contenido = next(m["content"] for m in mensajes if m["role"] == "user")
        pregunta = contenido.split("<pregunta>")[-1].lower()
        if "ticket" in pregunta:
            nombre, args = (
                "proponer_accion",
                {
                    "accion": "abrir_ticket",
                    "asunto": "Incidencia de IT",
                    "descripcion": pregunta[:200],
                    "prioridad": "media",
                },
            )
        elif "festivos" in pregunta:
            nombre, args = "data_query", {"consulta": "festivos", "anio": 2026}
        else:
            nombre, args = "rag_retrieve", {"consulta": pregunta[:500]}
        tc = ToolCall(id="c0", nombre=nombre, argumentos=json.dumps(args))
        funcion = {"name": nombre, "arguments": tc.argumentos}
        mensaje = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c0", "type": "function", "function": funcion}],
        }
        return DecisionSupervisor(tool_calls=[tc], mensaje_asistente=mensaje)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = Settings(
        # Archivo temporal, no memoria: la web lanza peticiones en paralelo y `sqlite://`
        # comparte una sola conexión entre hilos.
        database_url=f"sqlite:///{tempfile.mkdtemp()}/app.db",
        seleccion_libre_de_rol=True,
        gestion_documentos=True,
        trazas_modo="apagado",
        cache_backend="memoria",
        almacen_local_dir=tempfile.mkdtemp(),
    )
    s = build_servicios(
        settings,
        modelos=(FakeEmbedder(), FakeLLM(), SupervisorPorPalabras()),
        retriever=QdrantRetriever(QdrantClient(":memory:"), "documentos", DIM),
    )
    s.gestor.sincronizar(LocalFolderSource(RAIZ / "ingestor" / "sample_docs"))
    api.state.servicios, api.state.agente, api.state.gestor = s, s.agente, s.gestor
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/api", api)
app.mount("/", StaticFiles(directory=RAIZ / "web" / "html", html=True))
