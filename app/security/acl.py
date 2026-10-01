"""ACL de documentos dentro de `acl_groups` (el mismo campo del índice y del registro):

| Clasificación | Entrada en la ACL | Quién lee |
|---|---|---|
| Público       | `public`          | Todo usuario autenticado con rol |
| Interno       | `dept:<depto>`    | Empleados de ese departamento |
| Confidencial  | `<rol>`           | Quien tenga alguno de esos roles |
| Restringido   | `user:<id>`       | Usuarios con permiso explícito |

El usuario lleva los mismos tokens en sus grupos efectivos (rol + `dept:` + `user:`), así que
el filtro «contiene alguno» del índice (Qdrant `MatchAny`, AI Search `search.in`) aplica la
política dentro de la consulta. Las entradas acaban en filtros (OData en AI Search): solo se
aceptan formatos sin comillas, comas ni espacios.
"""

import re
from collections.abc import Iterable
from typing import Literal

_ROL = r"[a-z0-9][a-z0-9_\-]{0,63}"
_DEPTO = r"[a-z0-9][a-z0-9_\-]{0,39}"
_USUARIO = r"[a-z0-9][a-z0-9_.@:\-]{0,127}"
PATRON_ENTRADA_ACL = rf"^(?:{_ROL}|dept:{_DEPTO}|user:{_USUARIO})$"
_ENTRADA = re.compile(PATRON_ENTRADA_ACL)
_ES_ROL = re.compile(rf"^{_ROL}$")

Clasificacion = Literal["publico", "interno", "confidencial", "restringido"]


def entrada_valida(entrada: str) -> bool:
    return bool(_ENTRADA.match(entrada))


def es_rol(entrada: str) -> bool:
    return bool(_ES_ROL.match(entrada))


def grupos_efectivos(
    usuario_id: str, roles: Iterable[str], departamentos: Iterable[str] = ()
) -> list[str]:
    """Roles + `dept:<d>` + `user:<id>`, sin duplicados y solo lo que valida (lo demás no
    entra en ningún filtro: deny by default)."""
    candidatos = [*roles, *(f"dept:{d}" for d in departamentos), f"user:{usuario_id}"]
    return list(dict.fromkeys(g for g in candidatos if entrada_valida(g)))


def clasificacion(acl: Iterable[str]) -> Clasificacion:
    acl = set(acl)
    if "public" in acl:
        return "publico"
    if any(g.startswith("user:") for g in acl):
        return "restringido"
    if any(es_rol(g) for g in acl):
        return "confidencial"
    return "interno"
