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
from app.models.schemas import DecisionSupervisor
from app.retrieval.qdrant_retriever import QdrantRetriever
from ingestor.sources import LocalFolderSource
from tests.conftest import conectar_orquestador
from tests.fakes import DIM, FakeEmbedder, FakeLLM, GuionLLM, RagEco

RAIZ = Path(__file__).parents[2]


class SupervisorPorPalabras(RagEco):
    """Supervisor y rag_agent simulados. Como supervisor elige por palabras de la pregunta
    actual (no del historial): conversación, aclaración o delegar en rag_agent. Como rag_agent
    busca (o consulta data_query si pregunta por festivos) y cita lo primero que encuentra."""

    def decidir(self, mensajes, herramientas, obligar_herramienta=False) -> DecisionSupervisor:  # noqa: ANN001
        nombres = {h["function"]["name"] for h in herramientas}
        contenido = next(m["content"] for m in mensajes if m["role"] == "user")
        pregunta = contenido.split("<pregunta>")[-1].split("</pregunta>")[0].strip().lower()
        tool = [m["content"] for m in mensajes if m["role"] == "tool"]
        if "delegar_rag_agent" in nombres:
            if pregunta.strip(" ¡!¿?.").startswith(("hola", "gracias", "adiós", "buenos días")):
                return self._llamar("conversacion", {"tipo": "saludo"})
            if any(p in pregunta for p in ("receta", "pizza", "fútbol", "chiste")):
                return self._llamar("conversacion", {"tipo": "fuera_de_ambito"})
            if pregunta.strip().startswith("¿y eso") and "<historial>" not in contenido:
                return self._llamar("pedir_aclaracion", {
                    "pregunta": "¿A qué te refieres? ¿Sobre qué tema necesitas el dato?",
                    "opciones": ["Días de vacaciones al año", "Compensación por teletrabajo"],
                })  # fmt: skip
        elif not tool and "festivos" in contenido.lower():
            return self._llamar("data_query", {"consulta": "festivos", "anio": 2026})
        elif tool:  # respuesta extractiva: el primer fragmento recuperado, con su cita
            self.vistos += [dict(m) for m in mensajes]
            fragmentos = _fragmentos(tool[-1])
            self.turnos, self.llamadas = [], []
            self.final = (
                f"{' '.join(fragmentos[0]['contenido'].split())[:400]} [{fragmentos[0]['doc_id']}]"
                if fragmentos else "No lo encuentro."
            )  # fmt: skip
            return GuionLLM.decidir(self, mensajes, herramientas, obligar_herramienta)
        return super().decidir(mensajes, herramientas, obligar_herramienta)

    def _llamar(self, nombre: str, args: dict) -> DecisionSupervisor:
        guion = GuionLLM([[(nombre, args)]])
        return guion.decidir([], [], False)


def _fragmentos(dato: str) -> list[dict]:
    """Fragmentos del resultado de una tool (JSON dentro de <dato_herramienta>)."""
    try:
        return json.loads(dato[dato.index("{") : dato.rindex("}") + 1]).get("fragmentos", [])
    except ValueError:
        return []


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
    modelos = (FakeEmbedder(), FakeLLM(), SupervisorPorPalabras())
    s = build_servicios(
        settings,
        modelos=modelos,
        retriever=QdrantRetriever(QdrantClient(":memory:"), "documentos", DIM),
    )
    s.gestor.sincronizar(LocalFolderSource(RAIZ / "ingestor" / "sample_docs"))
    api.state.servicios, api.state.gestor = s, s.gestor
    conectar_orquestador(s, modelos)  # como app/main.py, con checkpointer en memoria
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/api", api)
app.mount("/", StaticFiles(directory=RAIZ / "web" / "html", html=True))
