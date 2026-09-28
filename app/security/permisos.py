"""Política de gestión de documentos (escritura). La lectura la protege el filtro por ACL
del retriever; la escritura exige además un rol explícito.

Para subir o borrar un documento del grupo G, el usuario debe pertenecer:
  - al grupo de editores (configurable, por defecto "editores"), y
  - al propio grupo G (nadie puede publicar en un grupo al que no pertenece).
"""

from app.models.schemas import Usuario


def puede_editar(usuario: Usuario, grupo: str, grupo_editores: str) -> bool:
    return grupo_editores in usuario.groups and grupo in usuario.groups
