"""API con el orquestador multiagente: aprobaciones en el mensaje, cola y decisión, por HTTP.
Quien decide sale del token; el cuerpo solo trae la decisión."""

import os

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import text

from app.agents.aprobaciones import SqlRepositorioAprobaciones
from app.config import Settings, get_settings
from app.deps import build_orquestador, build_servicios
from app.main import app
from app.models.schemas import Usuario
from app.persistencia import tablas as t
from app.persistencia.repositorios import crear_motor
from app.security.identity import get_usuario
from ingestor.sources import LocalFolderSource
from tests.conftest import SAMPLE_DOCS
from tests.fakes import FakeEmbedder, FakeLLM, GuionLLM

PUBLIC = {"X-Rol": "public"}


class Identidad:
    """Usuario del token en los tests (cambia entre peticiones)."""

    def __init__(self) -> None:
        self.actual = Usuario(id="github:ana", groups=["public"])

    def __call__(self) -> Usuario:
        return self.actual


def _cliente(servicios, guion, identidad, tmp_path):  # noqa: ANN001, ANN202
    llm = FakeLLM()
    orquestador = build_orquestador(
        servicios, InMemorySaver(), modelos=(FakeEmbedder(), llm, guion)
    )
    servicios.conversaciones.usar_orquestador(
        orquestador, SqlRepositorioAprobaciones(servicios.motor)
    )
    app.state.servicios = servicios
    settings = Settings(database_url="sqlite://", seleccion_libre_de_rol=True,
                        almacen_local_dir=str(tmp_path))  # fmt: skip
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_usuario] = identidad
    return TestClient(app)


@pytest.fixture
def servicios(retriever, tmp_path):
    s = build_servicios(
        Settings(database_url="sqlite://", seleccion_libre_de_rol=True,
                 almacen_local_dir=str(tmp_path), cache_semantica=False),
        modelos=(FakeEmbedder(), FakeLLM(), GuionLLM([])), retriever=retriever,
    )  # fmt: skip
    s.gestor.sincronizar(LocalFolderSource(SAMPLE_DOCS))
    yield s
    app.dependency_overrides.clear()


def _solicitud_de_acceso() -> GuionLLM:
    """Supervisor → rag_agent → request_document_access (escritura con confirmación)."""
    return GuionLLM([
        [("delegar_rag_agent", {"tarea": "Pedir acceso a Bandas salariales 2026"})],
        [("request_document_access", {"titulo": "Bandas salariales 2026", "motivo": "Datos"})],
    ], final="He enviado tu solicitud.")  # fmt: skip


def _preguntar(client, texto: str) -> dict:  # noqa: ANN001
    conv = client.post("/conversaciones", json={"rol_id": "public"}, headers=PUBLIC).json()["id"]
    r = client.post(f"/conversaciones/{conv}/mensajes", json={"pregunta": texto}, headers=PUBLIC)
    assert r.status_code == 200, r.text
    return {"conv": conv, **r.json()}


def test_flujo_completo_confirmacion_desde_la_api(servicios, tmp_path) -> None:
    identidad = Identidad()
    client = _cliente(servicios, _solicitud_de_acceso(), identidad, tmp_path)
    mensaje = _preguntar(client, "Quiero acceso a Bandas salariales 2026")
    (pausa,) = mensaje["aprobaciones"]
    assert pausa["type"] == "confirm_user" and "sí" in pausa["como_responder"]
    assert servicios.solicitudes.de_solicitante("github:ana") == []

    cola = client.get("/aprobaciones", headers=PUBLIC).json()
    assert [(a["id"], a["propia"]) for a in cola] == [(pausa["aprobacion_id"], True)]

    r = client.post(f"/aprobaciones/{pausa['aprobacion_id']}/decision", json={"aprobar": True},
                    headers=PUBLIC).json()  # fmt: skip
    assert r["estado"] == "aprobada" and r["mensaje"]["aprobaciones"] == []
    assert len(servicios.solicitudes.de_solicitante("github:ana")) == 1
    guardado = client.get(f"/conversaciones/{mensaje['conv']}", headers=PUBLIC).json()
    assert guardado["mensajes"][-1]["aprobaciones"] == []  # el historial también se actualiza
    assert client.get("/aprobaciones", headers=PUBLIC).json() == []


def test_otra_persona_no_puede_confirmar_por_ti(servicios, tmp_path) -> None:
    identidad = Identidad()
    client = _cliente(servicios, _solicitud_de_acceso(), identidad, tmp_path)
    (pausa,) = _preguntar(client, "Quiero acceso")["aprobaciones"]
    identidad.actual = Usuario(id="github:luis", groups=["public"])
    r = client.post(f"/aprobaciones/{pausa['aprobacion_id']}/decision", json={"aprobar": True},
                    headers=PUBLIC)  # fmt: skip
    assert r.status_code == 404  # ajena: igual que inexistente
    assert client.get("/aprobaciones", headers=PUBLIC).json() == []
    assert servicios.solicitudes.de_solicitante("github:ana") == []


def test_el_cuerpo_no_puede_traer_quien_aprueba(servicios, tmp_path) -> None:
    client = _cliente(servicios, _solicitud_de_acceso(), Identidad(), tmp_path)
    (pausa,) = _preguntar(client, "Quiero acceso")["aprobaciones"]
    cuerpo = {"aprobar": True, "approver_id": "github:admin"}
    r = client.post(f"/aprobaciones/{pausa['aprobacion_id']}/decision", json=cuerpo, headers=PUBLIC)
    assert r.status_code == 422


def test_rechazar_no_escribe_y_actualiza_el_mensaje(servicios, tmp_path) -> None:
    client = _cliente(servicios, _solicitud_de_acceso(), Identidad(), tmp_path)
    (pausa,) = _preguntar(client, "Quiero acceso")["aprobaciones"]
    r = client.post(f"/aprobaciones/{pausa['aprobacion_id']}/decision",
                    json={"aprobar": False, "motivo": "ya no"}, headers=PUBLIC).json()  # fmt: skip
    assert r["estado"] == "rechazada" and r["mensaje"]["aprobaciones"] == []
    assert servicios.solicitudes.de_solicitante("github:ana") == []


def test_una_pregunta_normal_pasa_por_el_orquestador(servicios, tmp_path) -> None:
    guion = GuionLLM([[("delegar_rag_agent", {"tarea": "vacaciones"})],
                      [("search_documents", {"consulta": "vacaciones días laborables"})]],
                     final="Son 23 días laborables [public/politica-vacaciones.md].")  # fmt: skip
    client = _cliente(servicios, guion, Identidad(), tmp_path)
    m = _preguntar(client, "¿Cuántos días de vacaciones tengo?")
    assert m["respuesta"] == "Son 23 días laborables [1]." and m["agentes"] == ["rag_agent"]
    assert m["citas"][0]["doc_id"] == "public/politica-vacaciones.md" and not m["sin_contexto"]


# ------------------------------------------------------------ P1: aprobación de soporte (PG)
URL = os.environ.get("TEST_DATABASE_URL", "")


@pytest.mark.postgres
@pytest.mark.skipif(not URL.startswith("postgresql"), reason="sin TEST_DATABASE_URL")
def test_un_p1_lo_aprueba_soporte_desde_su_cola(retriever, tmp_path) -> None:
    m = crear_motor(URL)
    with m.begin() as c:
        c.execute(
            text("DROP TABLE IF EXISTS ticket_comments, tickets, hr_case_notes, hr_cases CASCADE")
        )
    t.metadata.drop_all(m)
    m.dispose()
    servicios = build_servicios(
        Settings(database_url=URL, seleccion_libre_de_rol=True, almacen_local_dir=str(tmp_path),
                 cache_semantica=False),
        modelos=(FakeEmbedder(), FakeLLM(), GuionLLM([])), retriever=retriever,
    )  # fmt: skip
    guion = GuionLLM([
        [("delegar_support_agent", {"tarea": "Caída de la red de la planta"})],
        [("create_ticket", {"category": "red", "priority": "P1", "title": "Red caída planta 3",
                            "steps": "Nadie tiene red desde las 9"})],
    ], final="Ticket creado.")  # fmt: skip
    identidad = Identidad()
    client = _cliente(servicios, guion, identidad, tmp_path)
    try:
        (pausa,) = _preguntar(client, "Se cayó la red de toda la planta")["aprobaciones"]
        assert pausa["type"] == "approve_staff"
        # El solicitante no lo ve en su cola ni puede aprobarlo.
        assert client.get("/aprobaciones", headers=PUBLIC).json() == []
        r = client.post(f"/aprobaciones/{pausa['aprobacion_id']}/decision",
                        json={"aprobar": True}, headers=PUBLIC)  # fmt: skip
        assert r.status_code == 403
        # Soporte (otra persona con it_support) lo ve en su cola y lo aprueba.
        identidad.actual = Usuario(id="github:ti", groups=["public", "it_support"])
        soporte = {"X-Rol": "it_support"}
        cola = client.get("/aprobaciones", headers=soporte).json()
        assert [(a["accion"], a["solicitante"]) for a in cola] == [("create_ticket", "github:ana")]
        r = client.post(f"/aprobaciones/{pausa['aprobacion_id']}/decision",
                        json={"aprobar": True}, headers=soporte).json()  # fmt: skip
        assert r["estado"] == "aprobada" and r["mensaje"] is None  # no ve la conversación ajena
        with servicios.motor.connect() as c:
            assert c.execute(text("select priority, requester_id from tickets")).all() == [
                ("P1", "github:ana")
            ]
    finally:
        t.metadata.drop_all(servicios.motor)
        app.dependency_overrides.clear()
