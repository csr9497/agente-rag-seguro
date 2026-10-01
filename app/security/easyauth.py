"""Identidad desde Easy Auth de Azure Container Apps (AUTH_MODO=easyauth).

La web pública tiene la autenticación integrada de Container Apps: nadie llega a nginx sin
iniciar sesión con Entra ID, y el middleware inyecta `X-MS-CLIENT-PRINCIPAL` (JSON en base64
con las claims del token, incluidos los app roles) sobrescribiendo cualquier valor que envíe el
cliente. nginx lo reenvía al backend junto con `X-Proxy-Secreto`.

El backend solo tiene ingress interno y además exige el secreto compartido: otra carga del
entorno no puede fabricar una identidad sin conocerlo.
"""

import base64
import binascii
import hmac
import json

from app.models.schemas import Usuario

CLAIMS_OID = ("http://schemas.microsoft.com/identity/claims/objectidentifier", "oid")
CLAIMS_ROL = ("roles", "http://schemas.microsoft.com/ws/2008/06/identity/claims/role")


class PrincipalInvalidoError(Exception):
    pass


def secreto_valido(recibido: str | None, esperado: str) -> bool:
    return bool(recibido and esperado) and hmac.compare_digest(recibido.encode(), esperado.encode())


def usuario_desde_principal(cabecera: str) -> Usuario:
    """Usuario (oid) y roles (app roles de Entra) desde X-MS-CLIENT-PRINCIPAL."""
    try:
        principal = json.loads(base64.b64decode(cabecera, validate=True))
        claims = [(c["typ"], c["val"]) for c in principal["claims"]]
    except (binascii.Error, ValueError, KeyError, TypeError) as exc:
        raise PrincipalInvalidoError("X-MS-CLIENT-PRINCIPAL mal formado") from exc
    tipos_rol = {*CLAIMS_ROL, principal.get("role_typ")}
    oid = next((v for t, v in claims if t in CLAIMS_OID), None)
    if not oid:
        raise PrincipalInvalidoError("El principal no identifica al usuario (oid)")
    roles = sorted({v for t, v in claims if t in tipos_rol and isinstance(v, str)})
    return Usuario(id=str(oid), groups=roles)
