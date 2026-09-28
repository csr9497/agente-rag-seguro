import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import openai
from fastapi import Depends, FastAPI, HTTPException, Request

from app.config import get_settings
from app.deps import build_pipeline
from app.models.schemas import ConsultaRequest, RespuestaConsulta, Usuario
from app.rag.pipeline import RAGPipeline
from app.security.audit import registrar_consulta
from app.security.identity import get_usuario

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.pipeline = build_pipeline(get_settings())
    yield


app = FastAPI(title="Asistente RAG", version="0.1.0", lifespan=lifespan)


def get_pipeline(request: Request) -> RAGPipeline:
    return request.app.state.pipeline


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/consultar", response_model=RespuestaConsulta)
def consultar(
    body: ConsultaRequest,
    usuario: Annotated[Usuario, Depends(get_usuario)],
    pipeline: Annotated[RAGPipeline, Depends(get_pipeline)],
) -> RespuestaConsulta:
    try:
        respuesta = pipeline.consultar(body.pregunta, usuario, top_k=body.top_k)
    except openai.APIError as exc:
        logger.exception("Error del proveedor LLM")
        raise HTTPException(status_code=502, detail="Error del proveedor de IA") from exc
    registrar_consulta(usuario, body.pregunta, respuesta)
    return respuesta
