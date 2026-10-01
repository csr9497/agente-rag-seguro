"""Web + API en un solo proceso de tu equipo, contra los recursos de la nube (make cloud-local).

La API va en /api (como detrás de nginx) y la interfaz en /. Fuera de Docker para que
DefaultAzureCredential use tu `az login` (Blob y Content Safety no admiten claves).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.main import app as api
from app.main import lifespan as lifespan_api


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    async with lifespan_api(api):  # composición real; el estado queda en la API montada
        yield


app = FastAPI(lifespan=lifespan)
app.mount("/api", api)
app.mount("/", StaticFiles(directory=Path(__file__).parents[1] / "web" / "html", html=True))
