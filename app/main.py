import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import openai
from azure.core.exceptions import AzureError
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from app.api import acciones, admin, conversaciones, documentos, roles
from app.config import Settings, get_settings
from app.deps import build_servicios
from app.graph import topologia
from app.graph.agente import Agente
from app.models.schemas import ConsultaRequest, RespuestaConsulta, Usuario
from app.retrieval.no_configurado import ProveedorNoConfiguradoError
from app.security.identity import get_usuario
from app.servicios.errores import DatosInvalidosError, NoEncontradoError, PermisoDenegadoError

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    servicios = build_servicios(get_settings())
    informe = servicios.verificar_integridad()  # "validar antes de todo"
    logger.info(
        "Integridad al arrancar: %d revisados, %d problemas",
        informe.revisados,
        len(informe.problemas),
    )
    app.state.servicios = servicios
    app.state.agente = servicios.agente
    app.state.gestor = servicios.gestor
    yield


app = FastAPI(title="Asistente RAG", version="0.2.0", lifespan=lifespan)
for router in (
    documentos.router,
    roles.router,
    conversaciones.router,
    acciones.router,
    admin.router,
):
    app.include_router(router)


@app.exception_handler(PermisoDenegadoError)
def _permiso_denegado(_: Request, exc: PermisoDenegadoError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=403)


@app.exception_handler(NoEncontradoError)
def _no_encontrado(_: Request, exc: NoEncontradoError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=404)


@app.exception_handler(DatosInvalidosError)
def _datos_invalidos(_: Request, exc: DatosInvalidosError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=422)


def get_agente(request: Request) -> Agente:
    return request.app.state.agente


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _comprobar(fn) -> dict[str, object]:  # noqa: ANN001
    try:
        return {"ok": True, "detalle": fn() or ""}
    except Exception as exc:  # noqa: BLE001 — la sonda informa, no propaga
        return {"ok": False, "detalle": f"{type(exc).__name__}: {str(exc)[:200]}"}


@app.get("/ready")
def ready(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> JSONResponse:
    """Readiness: 503 si no se pueden atender consultas (sin base de datos o sin modelos). El
    índice se informa pero no bloquea: un despliegue nuevo aún no lo tiene hasta la primera
    subida. /health es la sonda de vida (el proceso responde)."""
    servicios = request.app.state.servicios

    def modelos() -> str:
        if not settings.azure_openai_endpoint:
            raise ProveedorNoConfiguradoError("falta AZURE_OPENAI_ENDPOINT")
        return settings.azure_openai_chat_deployment

    checks = {
        "base_de_datos": _comprobar(lambda: f"{len(servicios.repo_roles.listar())} roles"),
        "modelos": _comprobar(modelos),
        "indice": _comprobar(lambda: servicios.retriever.document_hash("__ready__") or "accesible"),
    }
    listo = checks["base_de_datos"]["ok"] and checks["modelos"]["ok"]
    return JSONResponse(
        {"status": "ok" if listo else "no_listo", "checks": checks},
        status_code=200 if listo else 503,
    )


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
    except AzureError as exc:
        logger.exception("Servicio de Azure no disponible")
        raise HTTPException(
            status_code=503,
            detail="Servicio de búsqueda no disponible temporalmente. Vuelve a intentarlo.",
        ) from exc


def _topologia_habilitada(settings: Annotated[Settings, Depends(get_settings)]) -> None:
    if not settings.exponer_topologia:
        raise HTTPException(status_code=404)


@app.get("/grafo", response_class=HTMLResponse, dependencies=[Depends(_topologia_habilitada)])
def grafo(agente: Annotated[Agente, Depends(get_agente)]) -> str:
    return topologia.pagina_html(agente)


@app.get(
    "/grafo.mmd", response_class=PlainTextResponse, dependencies=[Depends(_topologia_habilitada)]
)
def grafo_mermaid(agente: Annotated[Agente, Depends(get_agente)]) -> str:
    return topologia.mermaid(agente)
