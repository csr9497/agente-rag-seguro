"""Scopes por rol. El login (Easy Auth / Entra / stub) no emite scopes: se derivan de los roles
autenticados del usuario, y solo `authorize` construye el contexto (regla 2).

Ningún rol tiene scopes para borrar, reasignar o cerrar registros, ni para leer casos o
tickets ajenos fuera de su cola.
"""

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict

from app.security.acl import grupos_efectivos

# Lo que puede hacer cualquier empleado sobre lo suyo.
_EMPLEADO = frozenset({
    "docs:read", "docs:request",
    "hr_case:create", "hr_case:read_own", "hr_case:update_own",
    "ticket:create", "ticket:read_own", "ticket:update_own",
})  # fmt: skip

ROLE_SCOPES: dict[str, frozenset[str]] = {
    # Roles de contenido actuales (los documentos que ven los decide su ACL, no el scope).
    "public": _EMPLEADO,
    "rrhh": _EMPLEADO,
    "finanzas": _EMPLEADO,
    "administrador": frozenset({"roles:manage"}),
    # Roles de la orquestación multiagente.
    "hr_staff": _EMPLEADO | {"hr_case:read_queue"},
    "hr_specialist": _EMPLEADO | {"hr_case:read_queue", "hr_case:read_confidential"},
    "it_support": _EMPLEADO | {"ticket:read_queue", "ticket:approve_p1"},
    "auditor": frozenset({"audit:read"}),
}


class UserContext(BaseModel):
    """Identidad con la que actúan los agentes. Inmutable: ningún nodo puede cambiarla."""

    model_config = ConfigDict(frozen=True)

    id: str
    roles: tuple[str, ...]
    scopes: frozenset[str]
    # Grupos efectivos para la ACL de documentos (rol + «dept:» + «user:», app/security/acl.py).
    acl: tuple[str, ...] = ()


def contexto_de_usuario(
    user_id: str, roles: Iterable[str], departamentos: Iterable[str] = ()
) -> UserContext:
    """Roles desconocidos no aportan scopes (deny by default)."""
    roles = tuple(roles)
    scopes: frozenset[str] = frozenset().union(*(ROLE_SCOPES.get(r, frozenset()) for r in roles))
    acl = tuple(grupos_efectivos(user_id, roles, departamentos))
    return UserContext(id=user_id, roles=roles, scopes=scopes, acl=acl)
