"""Identidad del usuario.

- stub (local): usuario por defecto; en modo depuración, grupos por cabecera.
- entra: token de Entra ID obligatorio y validado (firma, emisor, audiencia, caducidad).
- easyauth (Azure): principal de Easy Auth reenviado por nginx con el secreto de proxy.
"""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.config import Settings, get_settings
from app.models.schemas import Usuario
from app.security.easyauth import (
    PrincipalInvalidoError,
    secreto_valido,
    usuario_desde_principal,
)
from app.security.entra import TokenInvalidoError, validador_para


def get_usuario(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
    x_usuario_grupos: Annotated[str | None, Header()] = None,
    x_ms_client_principal: Annotated[str | None, Header()] = None,
    x_proxy_secreto: Annotated[str | None, Header()] = None,
) -> Usuario:
    if settings.auth_modo == "entra":
        return _usuario_entra(settings, authorization)
    if settings.auth_modo == "easyauth":
        return _usuario_easyauth(settings, x_ms_client_principal, x_proxy_secreto)
    if settings.identidad_debug and x_usuario_grupos is not None:
        grupos = [g.strip() for g in x_usuario_grupos.split(",") if g.strip()]
        return Usuario(id=f"debug:{settings.default_user}", groups=grupos)
    return Usuario(id=settings.default_user, groups=list(settings.default_groups))


def _usuario_entra(settings: Settings, authorization: str | None) -> Usuario:
    no_autenticado = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Se requiere un token de Entra ID válido",
        headers={"WWW-Authenticate": "Bearer"},
    )
    esquema, _, token = (authorization or "").partition(" ")
    if esquema.lower() != "bearer" or not token.strip():
        raise no_autenticado
    try:
        return validador_para(settings).validar(token.strip())
    except TokenInvalidoError as exc:
        raise no_autenticado from exc


def _usuario_easyauth(settings: Settings, principal: str | None, secreto: str | None) -> Usuario:
    esperado = settings.proxy_secreto.get_secret_value() if settings.proxy_secreto else ""
    no_autenticado = HTTPException(status.HTTP_401_UNAUTHORIZED, "Inicia sesión")
    if not secreto_valido(secreto, esperado) or not principal:
        raise no_autenticado
    try:
        return usuario_desde_principal(principal)
    except PrincipalInvalidoError as exc:
        raise no_autenticado from exc
