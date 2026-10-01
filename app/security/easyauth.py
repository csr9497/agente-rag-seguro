"""Identidad desde Easy Auth de Azure Container Apps (AUTH_MODO=easyauth).

La web pública tiene la autenticación integrada de Container Apps (GitHub o Entra ID): nadie
llega a nginx sin iniciar sesión, y el middleware inyecta `X-MS-CLIENT-PRINCIPAL` (JSON en
base64 con el proveedor y las claims) sobrescribiendo cualquier valor que envíe el cliente.
nginx lo reenvía al backend junto con `X-Proxy-Secreto`.

El backend solo tiene ingress interno y además exige el secreto compartido: otra carga del
entorno no puede fabricar una identidad sin conocerlo.

Usuario: con Entra ID, el oid (y sus app roles); con otro proveedor, «<proveedor>:<nombre>»
(p. ej. «github:ana»), sin roles en el token: se los asigna un administrador desde la app.
"""

import base64
import binascii
import hmac
import json
import re

from app.models.schemas import Usuario

CLAIMS_OID = ("http://schemas.microsoft.com/identity/claims/objectidentifier", "oid")
CLAIMS_NOMBRE = (
    "urn:github:login",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
    "name",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier",
)
CLAIMS_ROL = ("roles", "http://schemas.microsoft.com/ws/2008/06/identity/claims/role")


class PrincipalInvalidoError(Exception):
    pass


def secreto_valido(recibido: str | None, esperado: str) -> bool:
    return bool(recibido and esperado) and hmac.compare_digest(recibido.encode(), esperado.encode())


def usuario_desde_principal(cabecera: str) -> Usuario:
    """Usuario y roles del token desde X-MS-CLIENT-PRINCIPAL."""
    try:
        principal = json.loads(base64.b64decode(cabecera, validate=True))
        claims = [(c["typ"], c["val"]) for c in principal["claims"]]
        proveedor = str(principal.get("auth_typ") or "aad").lower()
    except (binascii.Error, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise PrincipalInvalidoError("X-MS-CLIENT-PRINCIPAL mal formado") from exc
    tipos_rol = {*CLAIMS_ROL, principal.get("role_typ")}
    roles = sorted({v for t, v in claims if t in tipos_rol and isinstance(v, str)})
    if proveedor == "aad":
        oid = next((v for t, v in claims if t in CLAIMS_OID), None)
        if not oid:
            raise PrincipalInvalidoError("El principal no identifica al usuario (oid)")
        return Usuario(id=str(oid).lower(), groups=roles)
    tipos_nombre = (principal.get("name_typ"), *CLAIMS_NOMBRE)
    nombre = next((v for tipo in tipos_nombre for t, v in claims if t == tipo and v), None)
    if not isinstance(nombre, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.@\-]{0,120}", nombre
    ):
        raise PrincipalInvalidoError("El principal no identifica al usuario (nombre)")
    if not re.fullmatch(r"[a-z0-9]{1,20}", proveedor):
        raise PrincipalInvalidoError("Proveedor de identidad no válido")
    return Usuario(id=f"{proveedor}:{nombre.lower()}", groups=roles)
