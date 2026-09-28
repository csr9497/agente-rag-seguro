"""Identidad del usuario. Fase 1: stub; Fase 3: grupos desde el token de Entra ID."""

from typing import Annotated

from fastapi import Depends, Header

from app.config import Settings, get_settings
from app.models.schemas import Usuario


def get_usuario(
    settings: Annotated[Settings, Depends(get_settings)],
    x_usuario_grupos: Annotated[str | None, Header()] = None,
) -> Usuario:
    if settings.identidad_debug and x_usuario_grupos is not None:
        grupos = [g.strip() for g in x_usuario_grupos.split(",") if g.strip()]
        return Usuario(id=f"debug:{settings.default_user}", groups=grupos)
    return Usuario(id=settings.default_user, groups=list(settings.default_groups))
