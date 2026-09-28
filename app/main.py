import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import openai
from fastapi import Depends, FastAPI, HTTPException, Request

from app.config import get_settings
from app.deps import build_agente
from app.graph.agente import Agente
from app.models.schemas import ConsultaRequest, RespuestaConsulta, Usuario
from app.retrieval.no_configurado import ProveedorNoConfiguradoError
from app.security.identity import get_usuario

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.agente = build_agente(get_settings())
    yield


app = FastAPI(title="Asistente RAG", version="0.2.0", lifespan=lifespan)


def get_agente(request: Request) -> Agente:
    return request.app.state.agente


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/consultar", response_model=RespuestaConsulta)
def consultar(
    body: ConsultaRequest,
    usuario: Annotated[Usuario, Depends(get_usuario)],
    agente: Annotated[Agente, Depends(get_agente)],
) -> RespuestaConsulta:
    # La auditoría la hace el propio grafo (nodo audit), también en consultas rechazadas.
    try:
        return agente.consultar(body.pregunta, usuario, top_k=body.top_k)
    except ProveedorNoConfiguradoError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except openai.APIError as exc:
        logger.exception("Error del proveedor LLM")
        raise HTTPException(status_code=502, detail="Error del proveedor de IA") from exc
