"""Servidor para regenerar las capturas de docs/ui (sin modelos reales ni Azure).

Composición real de la app (registro, roles, permisos, guardrails, caché, tools) con el
supervisor de la integración y un LLM extractivo: responde con las frases del fragmento más
relevante, citándolo. Ver scripts/capturas/capturas.cjs.

    uv run uvicorn scripts.capturas.servidor:app --port 8790
"""

import re
import tempfile
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from qdrant_client import QdrantClient

from app.config import Settings
from app.deps import build_servicios
from app.main import app as api
from app.models.schemas import RespuestaLLM
from app.retrieval.qdrant_retriever import QdrantRetriever
from ingestor.sources import LocalFolderSource
from tests.fakes import DIM, FakeEmbedder
from tests.integration.servidor_simulado import RAIZ, SupervisorPorPalabras

_FRAGMENTO = re.compile(r'<fragmento n="(\d+)"[^>]*>\n(.*?)\n</fragmento>', re.DOTALL)
_PALABRA = re.compile(r"\w{4,}")
_VACIAS = {"cuáles", "cuántos", "cuántas", "cuál", "tengo", "sobre", "para", "como", "cómo", "qué"}


class LLMExtractivo:
    def responder(self, system: str, user: str) -> RespuestaLLM:
        pregunta = user.split("<pregunta>")[-1]
        clave = {p.lower() for p in _PALABRA.findall(pregunta)} - _VACIAS

        def puntos(texto: str) -> int:
            return len(clave & {p.lower() for p in _PALABRA.findall(texto)})

        fragmentos = [(int(n), t) for n, t in _FRAGMENTO.findall(user)]
        if not fragmentos:
            return RespuestaLLM(respuesta="", citas_usadas=[], encontrado=False)
        n, texto = max(fragmentos, key=lambda f: puntos(f[1]))
        if not puntos(texto):  # como un modelo real: sin datos en el contexto, «no encuentro»
            return RespuestaLLM(respuesta="", citas_usadas=[], encontrado=False)
        frases = [
            f.strip(" -*")
            for linea in texto.splitlines()
            if linea.strip() and not linea.startswith("#")
            for f in re.split(r"(?<=\.)\s+", linea)
        ]
        completas = [f for f in frases if f.endswith(".")] or frases  # sin cortes de fragmento
        mejores = sorted(completas, key=puntos, reverse=True)[:2]
        return RespuestaLLM(respuesta=f"{' '.join(mejores)} [{n}]", citas_usadas=[n],
                            encontrado=True)  # fmt: skip


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = Settings(
        database_url=f"sqlite:///{tempfile.mkdtemp()}/app.db",
        seleccion_libre_de_rol=True,
        gestion_documentos=True,
        trazas_modo="apagado",
        cache_backend="memoria",
        almacen_local_dir=tempfile.mkdtemp(),
    )
    s = build_servicios(
        settings,
        modelos=(FakeEmbedder(), LLMExtractivo(), SupervisorPorPalabras()),
        retriever=QdrantRetriever(QdrantClient(":memory:"), "documentos", DIM),
    )
    s.gestor.sincronizar(LocalFolderSource(RAIZ / "ingestor" / "sample_docs"))
    api.state.servicios, api.state.agente, api.state.gestor = s, s.agente, s.gestor
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/api", api)
app.mount("/", StaticFiles(directory=RAIZ / "web" / "html", html=True))
