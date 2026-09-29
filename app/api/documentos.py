"""Gestión de documentos por rol (cabecera X-Rol).

- GET    /documentos             documentos visibles para el rol (registro: roles y estado)
- POST   /documentos             sube e indexa un .md/.txt para los roles elegidos
- GET    /documentos/{doc_id}/contenido  fragmentos del documento (visor de fuentes citadas)
- DELETE /documentos/{doc_id}    elimina un documento visible para el rol

Subir y eliminar requieren `gestionar_documentos`. Los roles del documento deben estar en
`publica_para` del rol que sube, que se añade siempre.
"""

from pathlib import PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.api.dependencias import Actor, ServiciosDep, gestion_habilitada
from app.datos.catalogo import permisos_por_consulta
from app.models.schemas import ResultadoOperacion
from app.persistencia.modelos import DocumentoRegistrado
from app.retrieval.no_configurado import ProveedorNoConfiguradoError
from app.security.acceso import VerificadorRegistro
from app.servicios.errores import NoEncontradoError, PermisoDenegadoError
from app.servicios.roles import ServicioRoles
from ingestor.validacion import MAX_BYTES

router = APIRouter(prefix="/documentos", tags=["documentos"])


@router.get("", response_model=list[DocumentoRegistrado])
def listar(actor: Actor, servicios: ServiciosDep) -> list[DocumentoRegistrado]:
    return servicios.registro.listar(rol_id=actor.id)


@router.post("", response_model=ResultadoOperacion, dependencies=[Depends(gestion_habilitada)])
def subir(
    archivo: UploadFile,
    actor: Actor,
    servicios: ServiciosDep,
    roles: Annotated[list[str], Form(description="Roles que podrán verlo")] = [],  # noqa: B006
    forzar: Annotated[bool, Form()] = False,
) -> ResultadoOperacion:
    destinos = ServicioRoles.roles_de_publicacion(actor, roles)
    # El servidor decide la ruta: <rol que sube>/<nombre base del archivo>.
    nombre = PurePosixPath((archivo.filename or "").replace("\\", "/")).name
    datos = archivo.file.read(MAX_BYTES + 1)  # un byte de más basta para detectar el exceso
    try:
        resultado = servicios.gestor.indexar(
            f"{actor.id}/{nombre}", datos, forzar=forzar, roles=destinos, subido_por=actor.id
        )
    except ProveedorNoConfiguradoError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    if resultado.estado == "rechazado":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, resultado.model_dump())
    if resultado.estado == "duplicado":
        raise HTTPException(status.HTTP_409_CONFLICT, resultado.model_dump())
    return resultado


class Fragmento(BaseModel):
    chunk_id: str
    contenido: str


class ContenidoDocumento(BaseModel):
    doc_id: str
    titulo: str
    fragmentos: list[Fragmento]


@router.get("/{doc_id:path}/contenido", response_model=ContenidoDocumento)
def contenido(doc_id: str, actor: Actor, servicios: ServiciosDep) -> ContenidoDocumento:
    """Mismas barreras que el chat: documento activo y visible para el rol en el registro,
    filtro por rol en el índice y cada fragmento contrastado con el registro. «No existe» y
    «no tienes acceso» responden igual (404)."""
    doc = servicios.registro.obtener(doc_id)
    if doc is None or actor.id not in doc.roles or doc.estado != "activo":
        raise NoEncontradoError("Documento no encontrado")
    verificador = VerificadorRegistro(servicios.registro, permisos_por_consulta())
    fragmentos = [
        Fragmento(chunk_id=c.chunk_id, contenido=c.contenido)
        for c in servicios.retriever.get_document_chunks(doc_id, [actor.id])
        if verificador.motivo_rechazo(c, [actor.id]) is None
    ]
    if not fragmentos:
        raise NoEncontradoError("Documento no encontrado")
    return ContenidoDocumento(doc_id=doc_id, titulo=doc.titulo, fragmentos=fragmentos)


@router.delete(
    "/{doc_id:path}", response_model=ResultadoOperacion, dependencies=[Depends(gestion_habilitada)]
)
def eliminar(doc_id: str, actor: Actor, servicios: ServiciosDep) -> ResultadoOperacion:
    if not actor.puede("gestionar_documentos"):
        raise PermisoDenegadoError(f"El rol '{actor.id}' no puede gestionar documentos")
    doc = servicios.registro.obtener(doc_id)
    if doc is None or actor.id not in doc.roles:
        raise NoEncontradoError("Documento no encontrado")
    return servicios.gestor.eliminar(doc_id)
