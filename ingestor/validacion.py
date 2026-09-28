"""Validación y saneado de documentos antes de indexarlos (defensa contra file injection).

Un documento se RECHAZA (no se indexa) si:
- su ruta no es válida (fuera de una carpeta de grupo, oculta, con '..', grupo mal formado);
- su extensión no está permitida o supera el tamaño máximo;
- no es UTF-8 válido o contiene caracteres de control / binario;
- contiene caracteres invisibles o de control bidireccional (texto oculto al lector humano);
- contiene instrucciones dirigidas al modelo (inyección indirecta de prompt).

Se SANEA (sin rechazar): comentarios HTML (invisibles al renderizar Markdown) y
normalización Unicode NFC. Si un comentario contiene instrucciones, el documento se rechaza.
"""

import re
import unicodedata
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field

from app.security.deteccion import CONTROL, INVISIBLES, detectar_inyeccion

EXTENSIONES_PERMITIDAS = frozenset({".md", ".txt"})
MAX_BYTES = 1_000_000

_GRUPO = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")
_SEGMENTO = re.compile(r"^[\w\-. ]{1,120}$")

_COMENTARIO_HTML = re.compile(r"<!--.*?-->", re.DOTALL)


class DocumentoValidado(BaseModel):
    doc_id: str
    aceptado: bool
    motivos: list[str] = Field(default_factory=list, description="Por qué se rechazó")
    avisos: list[str] = Field(default_factory=list, description="Saneados aplicados")
    acl_groups: list[str] = Field(default_factory=list)
    texto: str | None = Field(default=None, description="Texto saneado (solo si se acepta)")

    @property
    def estado(self) -> Literal["aceptado", "rechazado"]:
        return "aceptado" if self.aceptado else "rechazado"


def validar_ruta(doc_id: str) -> tuple[list[str], list[str]]:
    """Devuelve (motivos de rechazo, acl_groups)."""
    ruta = PurePosixPath(doc_id)
    partes = ruta.parts
    motivos: list[str] = []
    if len(partes) < 2:
        return ["ruta: el documento debe estar dentro de una carpeta de grupo"], []
    if ruta.is_absolute() or any(p in {"..", "."} for p in partes):
        motivos.append("ruta: no se admiten rutas absolutas ni '..'")
    if any(p.startswith(".") for p in partes):
        motivos.append("ruta: no se admiten archivos ni carpetas ocultos")
    if not all(_SEGMENTO.match(p) for p in partes):
        motivos.append("ruta: caracteres no permitidos en el nombre")
    if not _GRUPO.match(partes[0]):
        motivos.append(f"ruta: nombre de grupo no válido {partes[0]!r}")
    if ruta.suffix.lower() not in EXTENSIONES_PERMITIDAS:
        motivos.append(f"formato: extensión no permitida {ruta.suffix!r}")
    return motivos, ([partes[0]] if not motivos else [])


def validar_documento(doc_id: str, datos: bytes, max_bytes: int = MAX_BYTES) -> DocumentoValidado:
    motivos, acl = validar_ruta(doc_id)
    if motivos:
        return DocumentoValidado(doc_id=doc_id, aceptado=False, motivos=motivos)

    if len(datos) > max_bytes:
        return _rechazo(doc_id, f"tamaño: {len(datos)} bytes supera el máximo de {max_bytes}")
    if not datos.strip():
        return _rechazo(doc_id, "contenido: documento vacío")
    try:
        texto = datos.decode("utf-8")
    except UnicodeDecodeError:
        return _rechazo(doc_id, "codificación: no es UTF-8 válido")

    texto = unicodedata.normalize("NFC", texto.removeprefix(chr(0xFEFF)))
    if CONTROL.search(texto):
        return _rechazo(doc_id, "contenido: caracteres de control o binario")
    if INVISIBLES.search(texto):
        return _rechazo(doc_id, "contenido: caracteres invisibles o de control bidireccional")

    avisos: list[str] = []
    comentarios = _COMENTARIO_HTML.findall(texto)
    if comentarios:
        if patrones := detectar_inyeccion("\n".join(comentarios)):
            return _rechazo(doc_id, f"inyección oculta en comentario HTML: {', '.join(patrones)}")
        texto = _COMENTARIO_HTML.sub("", texto)
        avisos.append(f"saneado: {len(comentarios)} comentario(s) HTML eliminados")

    if patrones := detectar_inyeccion(texto):
        return _rechazo(doc_id, f"inyección de prompt: {', '.join(patrones)}")

    return DocumentoValidado(
        doc_id=doc_id, aceptado=True, avisos=avisos, acl_groups=acl, texto=texto
    )


def _rechazo(doc_id: str, motivo: str) -> DocumentoValidado:
    return DocumentoValidado(doc_id=doc_id, aceptado=False, motivos=[motivo])
