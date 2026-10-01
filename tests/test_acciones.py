"""action_tool: el agente propone, el humano aprueba (human-in-the-loop)."""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.acciones.servicio import ServicioAcciones
from app.config import Settings, get_settings
from app.deps import build_servicios
from app.main import app
from app.models.schemas import Usuario
from app.persistencia.repositorios import crear_motor, inicializar
from app.servicios.errores import DatosInvalidosError, NoEncontradoError, PermisoDenegadoError
from app.tools.acciones import ProponerAccion, ProponerAccionArgs
from tests.fakes import FakeEmbedder, FakeLLM, FakeSupervisor

TICKET = {"asunto": "No funciona la VPN", "descripcion": "Error al conectar", "prioridad": "alta"}
VACACIONES = {"desde": "2026-10-05", "hasta": "2026-10-09"}


@pytest.fixture
def servicio() -> ServicioAcciones:
    motor = crear_motor("sqlite://")
    inicializar(motor)
    return ServicioAcciones(motor)


# ------------------------------------------------------------------ servicio
def test_proponer_no_ejecuta(servicio) -> None:
    p = servicio.proponer("abrir_ticket", TICKET, "public", "ana")
    assert p.estado == "pendiente" and p.resultado is None
    assert servicio.listar("public", "ana")[0].id == p.id


def test_aprobar_ejecuta_una_sola_vez(servicio, caplog) -> None:
    p = servicio.proponer("abrir_ticket", TICKET, "public", "ana")
    with caplog.at_level(logging.INFO, logger="audit"):
        hecha = servicio.decidir(p.id, True, "public", "ana")
    assert hecha.estado == "ejecutada" and hecha.resultado.startswith("SOP-0001")
    assert any('"accion": "accion_decidida"' in r.getMessage() for r in caplog.records)
    with pytest.raises(DatosInvalidosError):
        servicio.decidir(p.id, True, "public", "ana")


def test_rechazar(servicio) -> None:
    p = servicio.proponer("solicitar_vacaciones", VACACIONES, "rrhh", "ana")
    assert servicio.decidir(p.id, False, "rrhh", "ana").estado == "rechazada"


@pytest.mark.parametrize(("rol", "usuario"), [("rrhh", "ana"), ("public", "otro")])
def test_solo_decide_quien_propuso_con_su_rol(servicio, rol, usuario) -> None:
    p = servicio.proponer("abrir_ticket", TICKET, "public", "ana")
    with pytest.raises(NoEncontradoError):
        servicio.decidir(p.id, True, rol, usuario)
    assert servicio.obtener(p.id).estado == "pendiente"


def test_permiso_por_rol(servicio) -> None:
    with pytest.raises(PermisoDenegadoError):
        servicio.proponer("solicitar_vacaciones", VACACIONES, "administrador", "ana")
    assert servicio.proponer("abrir_ticket", TICKET, "administrador", "ana").estado == "pendiente"


@pytest.mark.parametrize(
    ("tipo", "datos"),
    [
        ("solicitar_vacaciones", {"desde": "2026-10-09", "hasta": "2026-10-05"}),
        ("solicitar_vacaciones", {"desde": "2026-10-01", "hasta": "2026-12-01"}),
        ("abrir_ticket", {"asunto": "x", "descripcion": "y"}),
        ("abrir_ticket", {**TICKET, "ejecutar": "rm -rf /"}),
    ],
    ids=["fechas_invertidas", "mas_de_30_dias", "asunto_corto", "campo_extra"],
)
def test_datos_invalidos(servicio, tipo, datos) -> None:
    with pytest.raises(DatosInvalidosError):
        servicio.proponer(tipo, datos, "public", "ana")


def test_tool_devuelve_propuesta_y_nota(servicio) -> None:
    tool = ProponerAccion(servicio)
    r = tool.ejecutar(
        ProponerAccionArgs(accion="abrir_ticket", **TICKET), Usuario(id="ana", groups=["public"]), 4
    )
    [p] = r.acciones
    assert p.estado == "pendiente" and "pendiente de aprobación" in r.nota
    denegada = tool.ejecutar(
        ProponerAccionArgs(accion="solicitar_vacaciones", desde="2026-10-05", hasta="2026-10-06"),
        Usuario(id="ana", groups=["administrador"]), 4,
    )  # fmt: skip
    assert denegada.acciones == [] and "No se pudo preparar" in denegada.nota


def test_tool_con_varios_roles_no_elige_uno(servicio) -> None:
    """Fuera de una conversación (p. ej. /consultar) el usuario puede traer varios roles."""
    r = ProponerAccion(servicio).ejecutar(
        ProponerAccionArgs(accion="abrir_ticket", **TICKET),
        Usuario(id="ana", groups=["public", "rrhh"]), 4,
    )  # fmt: skip
    assert r.acciones == [] and "No se pudo preparar" in r.nota
    assert servicio.listar("public", "ana") == servicio.listar("rrhh", "ana") == []


# ------------------------------------------------------------------ agente y API
def _cliente(retriever, tmp_path, supervisor):
    base = {
        "database_url": "sqlite://",
        "seleccion_libre_de_rol": True,
        "almacen_local_dir": str(tmp_path),
    }
    s = build_servicios(
        Settings(**base), modelos=(FakeEmbedder(), FakeLLM(), supervisor), retriever=retriever
    )
    app.state.servicios, app.state.agente = s, s.agente
    app.dependency_overrides[get_settings] = lambda: Settings(**base)
    return TestClient(app), s


@pytest.fixture
def limpiar():
    yield
    app.dependency_overrides.clear()


def test_flujo_completo_proponer_y_aprobar(retriever, tmp_path, limpiar) -> None:
    sup = FakeSupervisor([[("proponer_accion", json.dumps({"accion": "abrir_ticket", **TICKET}))]])
    client, s = _cliente(retriever, tmp_path, sup)
    conv = client.post("/conversaciones", json={"rol_id": "public"}).json()["id"]
    msg = client.post(
        f"/conversaciones/{conv}/mensajes", json={"pregunta": "Abre un ticket: la VPN no va"}
    ).json()
    [accion] = msg["acciones"]
    assert accion["estado"] == "pendiente" and "He preparado" in msg["respuesta"]
    assert s.acciones.obtener(accion["id"]).estado == "pendiente"  # nada ejecutado aún

    otro_rol = client.post(
        f"/acciones/{accion['id']}/decision", json={"aprobar": True}, headers={"X-Rol": "rrhh"}
    )
    assert otro_rol.status_code == 404
    r = client.post(
        f"/acciones/{accion['id']}/decision", json={"aprobar": True}, headers={"X-Rol": "public"}
    )
    assert r.status_code == 200 and r.json()["estado"] == "ejecutada"
    assert client.post(f"/acciones/{accion['id']}/decision", json={"aprobar": True},
                       headers={"X-Rol": "public"}).status_code == 422  # fmt: skip
    [guardado] = client.get(f"/conversaciones/{conv}").json()["mensajes"]
    assert guardado["acciones"][0]["id"] == accion["id"]
    assert [a["estado"] for a in client.get("/acciones", headers={"X-Rol": "public"}).json()] == [
        "ejecutada"
    ]


def test_documento_malicioso_no_ejecuta_acciones(retriever, tmp_path, limpiar) -> None:
    """Aunque el supervisor fuese manipulado para proponer, nada se ejecuta sin aprobación."""
    sup = FakeSupervisor([[("proponer_accion", json.dumps({"accion": "abrir_ticket", **TICKET}))]])
    client, s = _cliente(retriever, tmp_path, sup)
    conv = client.post("/conversaciones", json={"rol_id": "public"}).json()["id"]
    client.post(f"/conversaciones/{conv}/mensajes", json={"pregunta": "¿Horario de oficina?"})
    [p] = s.acciones.listar("public", "anonimo")
    assert p.estado == "pendiente" and p.resultado is None


def test_respuestas_con_acciones_no_se_cachean(retriever, tmp_path, limpiar) -> None:
    sup = FakeSupervisor([[("proponer_accion", json.dumps({"accion": "abrir_ticket", **TICKET}))]])
    _, s = _cliente(retriever, tmp_path, sup)
    s.agente.consultar_detallado("Abre un ticket", Usuario(id="ana", groups=["public"]))
    r = s.agente.consultar_detallado("Abre un ticket", Usuario(id="ana", groups=["public"]))
    assert not r.desde_cache and len(s.acciones.listar("public", "ana")) == 2
