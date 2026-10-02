"""Autenticación con Entra ID: tokens firmados en el test con una clave RSA propia."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.deps import build_servicios
from app.main import app
from app.security import identity
from app.security.entra import TokenInvalidoError, ValidadorEntra
from tests.fakes import FakeEmbedder, FakeLLM, FakeSupervisor

TENANT = "11111111-1111-1111-1111-111111111111"
AUDIENCIA = "api://agente-rag"
CLAVE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTRA_CLAVE = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _token(clave=CLAVE, alg="RS256", **claims) -> str:
    ahora = int(time.time())
    base = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "aud": AUDIENCIA,
        "iat": ahora,
        "exp": ahora + 600,
        "oid": "usuario-123",
        "roles": ["rrhh"],
    }
    return jwt.encode({**base, **claims}, clave, algorithm=alg)


@pytest.fixture
def validador() -> ValidadorEntra:
    return ValidadorEntra(TENANT, AUDIENCIA, resolver_clave=lambda _t: CLAVE.public_key())


def test_token_valido(validador) -> None:
    u = validador.validar(_token())
    assert u.id == "usuario-123" and u.groups == ["rrhh"]


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(lambda: _token(exp=int(time.time()) - 3600), id="caducado"),
        pytest.param(lambda: _token(aud="api://otra-app"), id="audiencia"),
        pytest.param(
            lambda: _token(iss="https://login.microsoftonline.com/otro/v2.0"), id="emisor"
        ),
        pytest.param(lambda: _token(clave=OTRA_CLAVE), id="otra_clave"),
        pytest.param(
            lambda: _token(clave="secreto-simetrico-suficientemente-largo-para-hs256", alg="HS256"),
            id="hs256",
        ),
        pytest.param(
            lambda: jwt.encode({"oid": "x", "roles": ["rrhh"]}, None, algorithm="none"),
            id="alg_none",
        ),
        pytest.param(lambda: _token(oid=None, sub=None), id="sin_usuario"),
        pytest.param(lambda: _token(roles="rrhh"), id="roles_mal_formados"),
        pytest.param(lambda: "no-es-un-jwt", id="basura"),
    ],
)
def test_tokens_invalidos(validador, token) -> None:
    with pytest.raises(TokenInvalidoError):
        validador.validar(token())


# ------------------------------------------------------------------ API en modo entra
@pytest.fixture
def client(retriever, tmp_path, monkeypatch):
    ajustes = Settings(
        database_url="sqlite://",
        auth_modo="entra",
        entra_tenant_id=TENANT,
        entra_audiencia=AUDIENCIA,
        seleccion_libre_de_rol=False,
        identidad_debug=True,
        almacen_local_dir=str(tmp_path),
    )
    # Se parchea la referencia que usa identity: ningún test descarga claves de Microsoft.
    monkeypatch.setattr(
        identity, "validador_para",
        lambda _s: ValidadorEntra(TENANT, AUDIENCIA, resolver_clave=lambda _t: CLAVE.public_key()),
    )  # fmt: skip
    s = build_servicios(
        ajustes, modelos=(FakeEmbedder(), FakeLLM(), FakeSupervisor()), retriever=retriever
    )
    app.state.servicios = s
    app.dependency_overrides[get_settings] = lambda: ajustes
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_sin_token_401(client) -> None:
    r = client.get("/roles")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


def test_token_invalido_401(client) -> None:
    assert (
        client.get(
            "/roles", headers={"Authorization": f"Bearer {_token(clave=OTRA_CLAVE)}"}
        ).status_code
        == 401
    )


def test_roles_disponibles_salen_del_token(client) -> None:
    r = client.get("/roles", headers={"Authorization": f"Bearer {_token(roles=['public'])}"})
    assert [x["id"] for x in r.json()] == ["public"]


def test_no_se_puede_actuar_con_un_rol_que_no_esta_en_el_token(client) -> None:
    auth = {"Authorization": f"Bearer {_token(roles=['public'])}"}
    assert client.post("/conversaciones", json={"rol_id": "rrhh"}, headers=auth).status_code == 403
    assert (
        client.post("/conversaciones", json={"rol_id": "public"}, headers=auth).status_code == 201
    )


def test_en_modo_entra_se_ignora_la_cabecera_de_depuracion(client) -> None:
    auth = {
        "Authorization": f"Bearer {_token(roles=['public'])}",
        "X-Usuario-Grupos": "rrhh,administrador",
    }
    assert [x["id"] for x in client.get("/roles", headers=auth).json()] == ["public"]


# ------------------------------------------------------------------ Easy Auth (Azure)
def _principal(claims: list[tuple[str, str]], role_typ: str = "roles") -> str:
    import base64
    import json

    cuerpo = {"auth_typ": "aad", "role_typ": role_typ,
              "claims": [{"typ": t, "val": v} for t, v in claims]}  # fmt: skip
    return base64.b64encode(json.dumps(cuerpo).encode()).decode()


SECRETO = "s" * 40
OID = "http://schemas.microsoft.com/identity/claims/objectidentifier"


def _usuario_easyauth(principal: str | None, secreto: str | None):
    from app.config import Settings
    from app.security.identity import get_usuario

    s = Settings(auth_modo="easyauth", proxy_secreto=SECRETO)
    return get_usuario(s, x_ms_client_principal=principal, x_proxy_secreto=secreto)


def test_easyauth_lee_oid_y_app_roles() -> None:
    u = _usuario_easyauth(
        _principal([(OID, "oid-1"), ("roles", "rrhh"), ("roles", "public"), ("name", "Ana")]),
        SECRETO,
    )
    assert u.id == "oid-1" and u.groups == ["public", "rrhh"]


def test_easyauth_con_role_typ_largo() -> None:
    largo = "http://schemas.microsoft.com/ws/2008/06/identity/claims/role"
    u = _usuario_easyauth(_principal([("oid", "o"), (largo, "finanzas")], largo), SECRETO)
    assert u.groups == ["finanzas"]


@pytest.mark.parametrize(
    ("principal", "secreto"),
    [
        (_principal([("oid", "o"), ("roles", "administrador")]), None),  # no viene del proxy
        (_principal([("oid", "o"), ("roles", "administrador")]), "otro" * 10),
        (None, SECRETO),  # sin login
        ("no-es-base64!!", SECRETO),
        (_principal([("roles", "administrador")]), SECRETO),  # sin oid
    ],
)
def test_easyauth_rechaza(principal, secreto) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        _usuario_easyauth(principal, secreto)
    assert e.value.status_code == 401


def test_easyauth_github_sin_roles_en_el_token() -> None:
    principal = _principal([("urn:github:login", "Ana-Dev"), ("urn:github:id", "123")])
    import base64
    import json

    cuerpo = json.loads(base64.b64decode(principal))
    cuerpo["auth_typ"] = "github"
    cuerpo["name_typ"] = "urn:github:login"
    u = _usuario_easyauth(base64.b64encode(json.dumps(cuerpo).encode()).decode(), SECRETO)
    assert u.id == "github:ana-dev" and u.groups == []
