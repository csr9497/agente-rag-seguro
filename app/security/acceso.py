"""Verificación de acceso a fragmentos (barrera 4 del diseño).

Contrasta cada fragmento devuelto por las tools (índice vectorial) con el registro de
documentos (fuente de verdad de permisos). Dos fuentes independientes: detecta chunks
huérfanos, ACL desincronizadas, documentos en cuarentena o un índice manipulado.
"""

from typing import Protocol

from app.models.schemas import Chunk
from app.persistencia.modelos import DocumentoRegistrado
from app.persistencia.repositorios import RepositorioDocumentos

DOC_CATALOGO = "catalogo"


class VerificadorAcceso(Protocol):
    def motivo_rechazo(self, chunk: Chunk, roles_usuario: list[str]) -> str | None:
        """None si el fragmento es accesible; si no, el motivo (para auditoría)."""
        ...


class VerificadorPermisivo:
    """Sin registro (tests unitarios del grafo): solo exige ACL compatible con el usuario."""

    def motivo_rechazo(self, chunk: Chunk, roles_usuario: list[str]) -> str | None:
        return None if set(chunk.acl_groups) & set(roles_usuario) else "rol_no_autorizado_indice"


class VerificadorRegistro:
    def __init__(self, registro: RepositorioDocumentos) -> None:
        self._registro = registro

    def motivo_rechazo(self, chunk: Chunk, roles_usuario: list[str]) -> str | None:
        roles = set(roles_usuario)
        if chunk.doc_id == DOC_CATALOGO:
            # Sintético, construido desde el registro con los roles del propio usuario.
            return None if set(chunk.acl_groups) <= roles else "rol_no_autorizado_indice"
        doc: DocumentoRegistrado | None = self._registro.obtener(chunk.doc_id)
        if doc is None:
            return "no_registrado"
        if doc.estado != "activo":
            return f"documento_{doc.estado}"
        if not roles & set(doc.roles):
            return "rol_no_autorizado_registro"
        if not roles & set(chunk.acl_groups):
            return "rol_no_autorizado_indice"
        if set(chunk.acl_groups) != set(doc.roles):
            return "acl_desincronizada"
        if chunk.doc_hash and chunk.doc_hash != doc.doc_hash:
            return "hash_distinto"
        return None
