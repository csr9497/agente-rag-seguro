import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import openai
from azure.core.exceptions import AzureError
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from app.agents.aprobaciones import Aprobacion, SqlRepositorioAprobaciones
from app.agents.checkpointer import crear_checkpointer
from app.agents.orquestador import Orquestador
from app.api import admin, aprobaciones, conversaciones, documentos, roles
from app.api.dependencias import ServiciosDep
from app.config import Settings, get_settings, validar_seguridad
from app.deps import build_orquestador, build_servicios
from app.graph import topologia
from app.modelos.errores import ModeloError, clasificar
from app.models.schemas import ConsultaRequest, RespuestaConsulta, Usuario
from app.retrieval.no_configurado import ProveedorNoConfiguradoError
from app.security.acl import grupos_efectivos
from app.security.identity import get_usuario
from app.servicios.errores import DatosInvalidosError, NoEncontradoError, PermisoDenegadoError

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    validar_seguridad(settings)  # en prod, sin Entra ID o con modos de depuración no arranca
    servicios = build_servicios(settings)
    informe = servicios.verificar_integridad()  # "validar antes de todo"
    logger.info(
        "Integridad al arrancar: %d revisados, %d problemas",
        informe.revisados,
        len(informe.problemas),
    )
    app.state.servicios = servicios
    app.state.gestor = servicios.gestor
    # Estado de los hilos cifrado en Postgres (falla cerrado sin CHECKPOINT_CLAVE).
    clave = settings.checkpoint_clave.get_secret_value() if settings.checkpoint_clave else None
    checkpointer, cerrar_checkpointer = crear_checkpointer(settings.database_url, clave)
    aprobaciones = SqlRepositorioAprobaciones(servicios.motor, notificar=_aviso_vencida)
    orquestador = build_orquestador(servicios, checkpointer)
    servicios.conversaciones.usar_orquestador(orquestador, aprobaciones)
    app.state.orquestador = orquestador
    tarea_vencimiento = asyncio.create_task(_vencer_periodicamente(aprobaciones))
    yield
    tarea_vencimiento.cancel()
    cerrar_checkpointer()


async def _vencer_periodicamente(aprobaciones: SqlRepositorioAprobaciones) -> None:
    """Cancela las aprobaciones pendientes con más de 24 h (y avisa)."""
    while True:
        try:
            await asyncio.to_thread(aprobaciones.vencer)
        except Exception:  # noqa: BLE001 — la tarea no debe morir por un fallo puntual
            logger.exception("No se pudieron vencer las aprobaciones")
        await asyncio.sleep(600)


def _aviso_vencida(aprobacion: Aprobacion) -> None:
    logging.getLogger("audit").info(json.dumps({
        "accion": "aprobacion_vencida", "aprobacion_id": aprobacion.id,
        "usuario": aprobacion.user_id, "herramienta": aprobacion.tool, "agente": aprobacion.agent,
    }))  # fmt: skip


app = FastAPI(title="Asistente RAG", version="0.2.0", lifespan=lifespan)
for router in (
    documentos.router,
    roles.router,
    conversaciones.router,
    aprobaciones.router,
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


@app.exception_handler(ModeloError)
def _error_modelo(_: Request, exc: ModeloError) -> JSONResponse:
    """Saldo agotado, credenciales, modelo inexistente, capacidad no soportada…: el usuario
    recibe un mensaje claro; quien opera el servicio, el detalle y qué revisar en el log."""
    logger.error("Proveedor de modelos: %s | %s", exc, exc.pista)
    return JSONResponse(
        {"detail": exc.mensaje_usuario, "codigo": exc.tipo}, status_code=exc.estado_http
    )


@app.exception_handler(openai.APIError)
def _error_openai(request: Request, exc: openai.APIError) -> JSONResponse:
    """Errores del SDK que no pasaron por un adaptador (red de seguridad)."""
    settings = get_settings()
    return _error_modelo(request, clasificar(exc, settings.modelos_proveedor, settings.modelo_chat))


def get_orquestador(request: Request) -> Orquestador:
    return request.app.state.orquestador


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
        if faltan := settings.modelos_faltantes():
            raise ProveedorNoConfiguradoError(f"faltan {', '.join(faltan)}")
        return f"{settings.modelos_proveedor}: {settings.modelo_chat}"

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


@app.get("/yo")
def yo(
    usuario: Annotated[Usuario, Depends(get_usuario)],
    servicios: ServiciosDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    """Quién soy, qué roles tengo (para pedir acceso a un administrador) y si hay login (la
    web lo usa para mostrar «Cerrar sesión», sea cual sea el host desde el que se abre)."""
    return {
        "id": usuario.id,
        "roles": servicios.roles.solo_activos(usuario).groups,
        "login": settings.auth_modo == "easyauth",
    }


@app.post("/consultar", response_model=RespuestaConsulta)
def consultar(
    body: ConsultaRequest,
    usuario: Annotated[Usuario, Depends(get_usuario)],
    orquestador: Annotated[Orquestador, Depends(get_orquestador)],
    servicios: ServiciosDep,
) -> RespuestaConsulta:
    """Consulta sin conversación (un hilo nuevo por petición). Si algo requiere aprobación,
    la respuesta lo indica y se resuelve en /aprobaciones."""
    # Solo los roles del usuario que siguen activos en el registro: un rol desactivado desde
    # la interfaz deja de dar acceso también aquí. La auditoría la hace el grafo (nodo audit).
    usuario = servicios.roles.solo_activos(usuario)
    usuario = usuario.model_copy(
        update={
            "groups": grupos_efectivos(
                usuario.id, usuario.groups, servicios.departamentos.de(usuario.id)
            )
        }
    )
    try:
        return orquestador.consultar(body.pregunta, usuario).respuesta
    except ProveedorNoConfiguradoError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
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
def grafo(orquestador: Annotated[Orquestador, Depends(get_orquestador)]) -> str:
    return topologia.pagina_html(orquestador)


@app.get(
    "/grafo.mmd", response_class=PlainTextResponse, dependencies=[Depends(_topologia_habilitada)]
)
def grafo_mermaid(orquestador: Annotated[Orquestador, Depends(get_orquestador)]) -> str:
    return topologia.mermaid(orquestador)
