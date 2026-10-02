"""Roles y permisos (asignados desde la interfaz por un rol con `administrar_roles`)."""

from fastapi import APIRouter, status

from app.api.dependencias import Actor, ServiciosDep, UsuarioDep
from app.persistencia.modelos import Rol
from app.servicios.roles import AsignacionUsuario, AsignarRoles, RolActualizar, RolCrear

router = APIRouter(prefix="/roles", tags=["roles"])


@router.get("", response_model=list[Rol])
def disponibles(usuario: UsuarioDep, servicios: ServiciosDep) -> list[Rol]:
    """Roles con los que el usuario puede iniciar una conversación."""
    return servicios.roles.disponibles(usuario)


@router.get("/todos", response_model=list[Rol])
def todos(actor: Actor, servicios: ServiciosDep) -> list[Rol]:
    """Todos los roles, también inactivos (requiere administrar_roles)."""
    return servicios.roles.listar_todos(actor)


@router.post("", response_model=Rol, status_code=status.HTTP_201_CREATED)
def crear(datos: RolCrear, actor: Actor, servicios: ServiciosDep) -> Rol:
    return servicios.roles.crear(actor, datos)


@router.patch("/{rol_id}", response_model=Rol)
def actualizar(rol_id: str, cambios: RolActualizar, actor: Actor, servicios: ServiciosDep) -> Rol:
    return servicios.roles.actualizar(actor, rol_id, cambios)


# ------------------------------------------------------------------ roles de las personas
@router.get("/asignaciones", response_model=list[AsignacionUsuario])
def asignaciones(actor: Actor, servicios: ServiciosDep) -> list[AsignacionUsuario]:
    """Roles asignados desde la app a cada persona (requiere administrar_roles)."""
    return servicios.roles.listar_asignaciones(actor)


@router.put("/asignaciones/{usuario_id}", response_model=AsignacionUsuario)
def asignar(
    usuario_id: str, datos: AsignarRoles, actor: Actor, usuario: UsuarioDep, servicios: ServiciosDep
) -> AsignacionUsuario:
    """Sustituye los roles de la persona (lista vacía = sin acceso). Se audita."""
    return servicios.roles.asignar(actor, usuario, usuario_id, datos)
