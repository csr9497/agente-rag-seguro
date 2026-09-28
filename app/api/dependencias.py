from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from app.config import Settings, get_settings
from app.deps import Servicios
from app.models.schemas import Usuario
from app.persistencia.modelos import Rol
from app.security.identity import get_usuario


def get_servicios(request: Request) -> Servicios:
    return request.app.state.servicios


def rol_actor(
    usuario: Annotated[Usuario, Depends(get_usuario)],
    servicios: Annotated[Servicios, Depends(get_servicios)],
    x_rol: Annotated[str | None, Header(description="Rol con el que se actúa")] = None,
) -> Rol:
    return servicios.roles.actuar_como(usuario, x_rol)


def gestion_habilitada(settings: Annotated[Settings, Depends(get_settings)]) -> None:
    if not settings.gestion_documentos:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Gestión de documentos deshabilitada")


Actor = Annotated[Rol, Depends(rol_actor)]
ServiciosDep = Annotated[Servicios, Depends(get_servicios)]
UsuarioDep = Annotated[Usuario, Depends(get_usuario)]
