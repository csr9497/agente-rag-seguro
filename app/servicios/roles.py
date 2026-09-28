"""Reglas de roles y permisos. Los roles y sus permisos se gestionan desde la UI por un rol
con `administrar_roles`."""

import json
import logging

from pydantic import BaseModel, ConfigDict, Field

from app.models.schemas import Usuario
from app.persistencia.modelos import PATRON_ROL, Permiso, Rol
from app.persistencia.repositorios import RepositorioRoles
from app.servicios.errores import DatosInvalidosError, NoEncontradoError, PermisoDenegadoError

audit = logging.getLogger("audit")


class RolCrear(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=PATRON_ROL)
    nombre: str = Field(min_length=1, max_length=80)
    descripcion: str = Field(default="", max_length=300)
    permisos: list[Permiso] = Field(default_factory=list)
    publica_para: list[str] = Field(default_factory=list)


class RolActualizar(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str | None = Field(default=None, min_length=1, max_length=80)
    descripcion: str | None = Field(default=None, max_length=300)
    activo: bool | None = None
    permisos: list[Permiso] | None = None
    publica_para: list[str] | None = None


class ServicioRoles:
    def __init__(self, repo: RepositorioRoles, seleccion_libre: bool) -> None:
        self._repo = repo
        self._seleccion_libre = seleccion_libre

    # ------------------------------------------------------------ selección de rol
    def disponibles(self, usuario: Usuario) -> list[Rol]:
        """Roles con los que el usuario puede actuar. Con selección libre (solo local), todos
        los activos; si no, los que trae su identidad (Entra ID en Azure)."""
        activos = self._repo.listar(incluir_inactivos=False)
        if self._seleccion_libre:
            return activos
        return [r for r in activos if r.id in usuario.groups]

    def actuar_como(self, usuario: Usuario, rol_id: str | None) -> Rol:
        if not rol_id:
            raise PermisoDenegadoError("Indica el rol con el que actúas (cabecera X-Rol)")
        rol = next((r for r in self.disponibles(usuario) if r.id == rol_id), None)
        if rol is None:
            raise PermisoDenegadoError(f"No puedes actuar con el rol '{rol_id}'")
        return rol

    # ------------------------------------------------------------ administración
    def listar_todos(self, actor: Rol) -> list[Rol]:
        self._exigir_admin(actor)
        return self._repo.listar()

    def crear(self, actor: Rol, datos: RolCrear) -> Rol:
        self._exigir_admin(actor)
        if self._repo.obtener(datos.id):
            raise DatosInvalidosError(f"Ya existe un rol con id '{datos.id}'")
        self._validar_destinos(datos.publica_para, propio=datos.id)
        rol = self._repo.guardar(Rol(**datos.model_dump()))
        self._auditar(actor, "rol_creado", rol.id, datos.model_dump())
        return rol

    def actualizar(self, actor: Rol, rol_id: str, cambios: RolActualizar) -> Rol:
        self._exigir_admin(actor)
        actual = self._repo.obtener(rol_id)
        if actual is None:
            raise NoEncontradoError(f"No existe el rol '{rol_id}'")
        datos = cambios.model_dump(exclude_none=True)
        nuevo = actual.model_copy(update=datos)
        nuevo = Rol.model_validate(nuevo.model_dump())  # re-normaliza listas
        if cambios.publica_para is not None:
            self._validar_destinos(nuevo.publica_para, propio=rol_id)
        if actual.puede("administrar_roles") and not nuevo.puede("administrar_roles"):
            otros = [
                r
                for r in self._repo.listar(incluir_inactivos=False)
                if r.id != rol_id and r.puede("administrar_roles")
            ]
            if not otros:
                raise DatosInvalidosError(
                    "No se puede quitar la administración al último rol administrador"
                )
        guardado = self._repo.guardar(nuevo)
        self._auditar(actor, "rol_actualizado", rol_id, datos)
        return guardado

    # ------------------------------------------------------------ publicación
    @staticmethod
    def roles_de_publicacion(actor: Rol, pedidos: list[str]) -> list[str]:
        """Roles finales de un documento subido por `actor`: los pedidos más el propio, y
        todos dentro de lo que el actor puede publicar."""
        if not actor.puede("gestionar_documentos"):
            raise PermisoDenegadoError(f"El rol '{actor.id}' no puede gestionar documentos")
        destinos = sorted(set(pedidos) | {actor.id})
        permitidos = set(actor.publica_para) | {actor.id}
        if fuera := [r for r in destinos if r not in permitidos]:
            raise PermisoDenegadoError(f"El rol '{actor.id}' no puede publicar para {fuera}")
        return destinos

    # ------------------------------------------------------------ internos
    @staticmethod
    def _exigir_admin(actor: Rol) -> None:
        if not actor.puede("administrar_roles"):
            raise PermisoDenegadoError(f"El rol '{actor.id}' no puede administrar roles")

    def _validar_destinos(self, destinos: list[str], propio: str) -> None:
        existentes = {r.id for r in self._repo.listar()} | {propio}
        if faltan := [d for d in destinos if d not in existentes]:
            raise DatosInvalidosError(f"publica_para contiene roles inexistentes: {faltan}")

    @staticmethod
    def _auditar(actor: Rol, accion: str, rol_id: str, datos: dict) -> None:
        audit.info(json.dumps({"accion": accion, "actor": actor.id, "rol": rol_id, "datos": datos}))
