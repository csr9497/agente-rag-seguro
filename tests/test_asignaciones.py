"""Roles asignados a personas desde la app (login con GitHub: el token no trae roles)."""

import logging

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from app.api.dependencias import get_servicios
from app.config import Settings, get_settings
from app.deps import build_orquestador, build_servicios
from app.main import app, get_orquestador
from app.models.schemas import Usuario
from app.security.identity import get_usuario
from app.servicios.errores import DatosInvalidosError, PermisoDenegadoError
from app.servicios.roles import AsignarRoles
from ingestor.sources import LocalFolderSource
from tests.conftest import SAMPLE_DOCS
from tests.fakes import FakeEmbedder, FakeLLM, FakeSupervisor, RagEco

ANA = Usuario(id="github:ana", groups=[])
ADMIN = Usuario(id="github:jefa", groups=[])


def _settings(**kw) -> Settings:
    base = {"database_url": "sqlite://", "seleccion_libre_de_rol": False,
            "asignaciones_iniciales": {"github:jefa": ["administrador", "public"]}}  # fmt: skip
    return Settings(**{**base, **kw})


@pytest.fixture
def servicios(retriever):
    return build_servicios(
        _settings(), modelos=(FakeEmbedder(), FakeLLM(), FakeSupervisor()), retriever=retriever
    )


def test_sin_asignacion_no_hay_roles(servicios) -> None:
    assert servicios.roles.disponibles(ANA) == []
    assert servicios.roles.solo_activos(ANA).groups == []


def test_asignaciones_iniciales_al_arrancar(servicios) -> None:
    assert {r.id for r in servicios.roles.disponibles(ADMIN)} == {"administrador", "public"}
    # Idempotente y solo añade: no quita lo que un administrador cambió después.
    servicios.roles.asignaciones_iniciales({"github:jefa": ["public"]})
    assert servicios.repo_roles.roles_de_usuario("github:jefa") == ["administrador", "public"]


def test_admin_asigna_y_se_audita(servicios, caplog) -> None:
    actor = servicios.roles.actuar_como(ADMIN, "administrador")
    with caplog.at_level(logging.INFO, logger="audit"):
        r = servicios.roles.asignar(actor, ADMIN, "GitHub:Ana", AsignarRoles(roles=["rrhh"]))
    assert r.usuario_id == "github:ana" and r.roles == ["rrhh"]
    assert [x.id for x in servicios.roles.disponibles(ANA)] == ["rrhh"]
    assert servicios.roles.solo_activos(ANA).groups == ["rrhh"]
    assert any("roles_asignados" in x.getMessage() for x in caplog.records)
    servicios.roles.asignar(actor, ADMIN, "github:ana", AsignarRoles(roles=[]))
    assert servicios.roles.disponibles(ANA) == []


def test_solo_un_administrador_asigna(servicios) -> None:
    actor = servicios.roles.actuar_como(ADMIN, "public")
    with pytest.raises(PermisoDenegadoError):
        servicios.roles.asignar(actor, ADMIN, "github:ana", AsignarRoles(roles=["rrhh"]))


@pytest.mark.parametrize(
    ("usuario_id", "roles"),
    [("github:ana", ["inventado"]), ("no valido!", ["public"]), ("github:jefa", ["public"])],
)
def test_asignaciones_invalidas(servicios, usuario_id, roles) -> None:
    """Rol inexistente, identificador mal formado o quitarse a uno mismo la administración."""
    actor = servicios.roles.actuar_como(ADMIN, "administrador")
    with pytest.raises(DatosInvalidosError):
        servicios.roles.asignar(actor, ADMIN, usuario_id, AsignarRoles(roles=roles))


def test_rol_desactivado_deja_de_contar(servicios) -> None:
    actor = servicios.roles.actuar_como(ADMIN, "administrador")
    servicios.roles.asignar(actor, ADMIN, "github:ana", AsignarRoles(roles=["rrhh", "public"]))
    rrhh = servicios.repo_roles.obtener("rrhh")
    servicios.repo_roles.guardar(rrhh.model_copy(update={"activo": False}))
    assert servicios.roles.solo_activos(ANA).groups == ["public"]


# ------------------------------------------------------------------ API
@pytest.fixture
def client(servicios):
    yo = {"usuario": ADMIN}
    servicios.gestor.sincronizar(LocalFolderSource(SAMPLE_DOCS))
    modelos = (FakeEmbedder(), FakeLLM(), RagEco())
    orquestador = build_orquestador(servicios, InMemorySaver(), modelos)
    app.dependency_overrides[get_orquestador] = lambda: orquestador
    app.dependency_overrides[get_servicios] = lambda: servicios
    app.dependency_overrides[get_settings] = lambda: _settings()
    app.dependency_overrides[get_usuario] = lambda: yo["usuario"]
    yield TestClient(app), yo
    app.dependency_overrides.clear()


def test_api_asignaciones(client) -> None:
    c, yo = client
    r = c.put("/roles/asignaciones/github:ana", json={"roles": ["public"]},
              headers={"X-Rol": "administrador"})  # fmt: skip
    assert r.status_code == 200 and r.json() == {"usuario_id": "github:ana", "roles": ["public"]}
    lista = c.get("/roles/asignaciones", headers={"X-Rol": "administrador"}).json()
    assert {"usuario_id": "github:ana", "roles": ["public"]} in lista

    yo["usuario"] = ANA
    assert c.get("/yo").json() == {"id": "github:ana", "roles": ["public"], "login": False}
    assert [r["id"] for r in c.get("/roles").json()] == ["public"]
    # Ana no administra: ni ve ni cambia asignaciones.
    assert c.get("/roles/asignaciones", headers={"X-Rol": "public"}).status_code == 403
    r = c.put("/roles/asignaciones/github:ana", json={"roles": ["administrador"]},
              headers={"X-Rol": "public"})  # fmt: skip
    assert r.status_code == 403


def test_consultar_usa_los_roles_asignados(client) -> None:
    c, yo = client
    yo["usuario"] = ANA
    sin_rol = c.post("/consultar", json={"pregunta": "vacaciones"}).json()
    assert sin_rol["citas"] == []
    yo["usuario"] = ADMIN
    c.put("/roles/asignaciones/github:ana", json={"roles": ["public"]},
          headers={"X-Rol": "administrador"})  # fmt: skip
    yo["usuario"] = ANA
    con_rol = c.post("/consultar", json={"pregunta": "vacaciones"}).json()
    assert con_rol["citas"] and con_rol["citas"][0]["fuente"].startswith("public/")


def test_yo_indica_si_hay_login(client) -> None:
    """La web muestra «Cerrar sesión» según el servidor, no según el host desde el que se abre."""
    c, _ = client
    app.dependency_overrides[get_settings] = lambda: _settings(
        auth_modo="easyauth", proxy_secreto="x" * 40
    )
    assert c.get("/yo").json()["login"] is True
