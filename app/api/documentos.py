"""API de gestión de documentos del RAG.

- GET    /documentos             documentos visibles para el usuario (filtrado por ACL)
- POST   /documentos             sube e indexa un .md/.txt en un grupo (editores del grupo)
- DELETE /documentos/{doc_id}    elimina un documento (editores del grupo)
"""

from pathlib import PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, status

from app.config import Settings, get_settings
from app.models.schemas import DocumentoIndexado, ResultadoOperacion, Usuario
from app.retrieval.no_configurado import ProveedorNoConfiguradoError
from app.security.identity import get_usuario
from app.security.permisos import puede_editar
from ingestor.gestor import GestorDocumentos
from ingestor.validacion import MAX_BYTES

router = APIRouter(prefix="/documentos", tags=["documentos"])


def get_gestor(request: Request) -> GestorDocumentos:
    return request.app.state.gestor


def _gestion_habilitada(settings: Annotated[Settings, Depends(get_settings)]) -> Settings:
    if not settings.gestion_documentos:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Gestión de documentos deshabilitada")
    return settings


def _autorizar(usuario: Usuario, grupo: str, settings: Settings) -> None:
    if not puede_editar(usuario, grupo, settings.grupo_editores):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Se requiere pertenecer a '{settings.grupo_editores}' y a '{grupo}'",
        )


@router.get("", response_model=list[DocumentoIndexado])
def listar(
    usuario: Annotated[Usuario, Depends(get_usuario)],
    gestor: Annotated[GestorDocumentos, Depends(get_gestor)],
) -> list[DocumentoIndexado]:
    return gestor.listar(usuario.groups)


@router.post("", response_model=ResultadoOperacion)
def subir(
    grupo: Annotated[str, Form(pattern=r"^[a-z0-9][a-z0-9_\-]{0,63}$")],
    archivo: UploadFile,
    usuario: Annotated[Usuario, Depends(get_usuario)],
    gestor: Annotated[GestorDocumentos, Depends(get_gestor)],
    settings: Annotated[Settings, Depends(_gestion_habilitada)],
    forzar: Annotated[bool, Form()] = False,
) -> ResultadoOperacion:
    _autorizar(usuario, grupo, settings)
    # Solo el nombre base: la ruta la decide el servidor (<grupo>/<archivo>).
    nombre = PurePosixPath((archivo.filename or "").replace("\\", "/")).name
    datos = archivo.file.read(MAX_BYTES + 1)  # un byte de más basta para detectar el exceso
    try:
        resultado = gestor.indexar(f"{grupo}/{nombre}", datos, forzar=forzar)
    except ProveedorNoConfiguradoError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    if resultado.estado == "rechazado":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, resultado.model_dump())
    if resultado.estado == "duplicado":
        raise HTTPException(status.HTTP_409_CONFLICT, resultado.model_dump())
    return resultado


@router.delete("/{doc_id:path}", response_model=ResultadoOperacion)
def eliminar(
    doc_id: str,
    usuario: Annotated[Usuario, Depends(get_usuario)],
    gestor: Annotated[GestorDocumentos, Depends(get_gestor)],
    settings: Annotated[Settings, Depends(_gestion_habilitada)],
) -> ResultadoOperacion:
    _autorizar(usuario, doc_id.split("/", 1)[0], settings)
    resultado = gestor.eliminar(doc_id)
    if resultado.estado == "no_encontrado":
        raise HTTPException(status.HTTP_404_NOT_FOUND, resultado.model_dump())
    return resultado
