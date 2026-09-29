"""Autenticación con Entra ID (AUTH_MODO=entra).

Valida el token JWT del encabezado `Authorization: Bearer`: firma RS256 contra las claves
públicas (JWKS) del tenant, emisor, audiencia y caducidad. Los roles salen de la claim
configurada (por defecto `roles`, app roles de Entra), que se corresponden con los ids de rol.

En Azure, la web usa la autenticación integrada de Container Apps (Easy Auth) y nginx reenvía
el token; el backend lo vuelve a validar aquí: no confía en cabeceras sin verificar la firma.
"""

from collections.abc import Callable
from typing import Any

import jwt

from app.config import Settings
from app.models.schemas import Usuario

ALGORITMOS = ["RS256"]  # nunca "none" ni HS256 (clave simétrica)


class TokenInvalidoError(Exception):
    pass


class ValidadorEntra:
    def __init__(
        self,
        tenant_id: str,
        audiencia: str,
        claim_roles: str = "roles",
        resolver_clave: Callable[[str], Any] | None = None,
    ) -> None:
        self._audiencia = audiencia
        self._emisor = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self._claim_roles = claim_roles
        if resolver_clave is None:
            jwks = jwt.PyJWKClient(
                f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys",
                cache_keys=True,
                lifespan=3600,
            )
            resolver_clave = lambda token: jwks.get_signing_key_from_jwt(token).key  # noqa: E731
        self._resolver_clave = resolver_clave

    def validar(self, token: str) -> Usuario:
        try:
            claims = jwt.decode(
                token,
                key=self._resolver_clave(token),
                algorithms=ALGORITMOS,
                audience=self._audiencia,
                issuer=self._emisor,
                options={"require": ["exp", "iat", "iss", "aud"]},
                leeway=30,
            )
        except (jwt.PyJWTError, KeyError, ValueError) as exc:
            raise TokenInvalidoError(str(exc)) from exc
        usuario = claims.get("oid") or claims.get("sub")
        if not usuario:
            raise TokenInvalidoError("El token no identifica al usuario (oid/sub)")
        roles = claims.get(self._claim_roles, [])
        if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
            raise TokenInvalidoError(f"Claim '{self._claim_roles}' mal formada")
        return Usuario(id=str(usuario), groups=roles)


_validadores: dict[tuple[str, str, str], ValidadorEntra] = {}


def validador_para(settings: Settings) -> ValidadorEntra:
    clave = (settings.entra_tenant_id, settings.entra_audiencia, settings.entra_claim_roles)
    if clave not in _validadores:
        _validadores[clave] = ValidadorEntra(*clave)
    return _validadores[clave]
