"""Paridad con PostgreSQL (la base prevista en Azure). Se ejecutan si hay TEST_DATABASE_URL:

    docker compose --profile postgres up -d postgres
    TEST_DATABASE_URL=postgresql+psycopg://agente_app:<clave>@localhost:55432/agente \
        uv run pytest tests/test_postgres.py
"""

import os
import uuid

import pytest
from sqlalchemy import text

from app.config import Settings
from app.deps import build_servicios
from app.models.schemas import Usuario
from app.persistencia import tablas as t
from app.persistencia.modelos import DocumentoRegistrado, Feedback, MensajeGuardado, Rol
from app.persistencia.repositorios import (
    SqlRepositorioConversaciones,
    SqlRepositorioDocumentos,
    SqlRepositorioRoles,
    crear_motor,
    inicializar,
)
from tests.conftest import ROLES_SEMILLA
from tests.fakes import FakeEmbedder, FakeLLM, FakeSupervisor

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not URL.startswith("postgresql"), reason="sin TEST_DATABASE_URL de PostgreSQL"
    ),
]


@pytest.fixture
def motor():
    m = crear_motor(URL)
    t.metadata.drop_all(m)  # base de pruebas: se recrea en cada test
    inicializar(m)
    yield m
    t.metadata.drop_all(m)
    m.dispose()


def test_es_postgres(motor) -> None:
    with motor.connect() as c:
        assert "PostgreSQL" in c.execute(text("select version()")).scalar_one()


def test_roles_documentos_y_conversaciones(motor) -> None:
    roles = SqlRepositorioRoles(motor)
    assert {r.id for r in roles.listar()} == ROLES_SEMILLA
    roles.guardar(Rol(id="compras", nombre="Compras", publica_para=["public"]))
    assert roles.obtener("compras").publica_para == ["public"]

    docs = SqlRepositorioDocumentos(motor)
    docs.registrar(
        DocumentoRegistrado(
            doc_id="rrhh/a.md", titulo="A", roles=["public", "rrhh"], doc_hash="h" * 64, chunks=2
        )
    )
    assert [d.doc_id for d in docs.listar("public")] == ["rrhh/a.md"]
    docs.marcar_estado("rrhh/a.md", "bloqueado", "prueba")
    assert docs.obtener("rrhh/a.md").estado == "bloqueado"

    convs = SqlRepositorioConversaciones(motor)
    conv = convs.crear("rrhh")
    m = convs.agregar_mensaje(
        conv.id,
        MensajeGuardado(pregunta="p", respuesta="r", sin_contexto=True, traza_id=str(uuid.uuid4())),
    )
    convs.registrar_feedback(conv.id, m.id, Feedback(valoracion="negativa", comentario="c"))
    [leido] = convs.obtener(conv.id).mensajes
    assert leido.feedback.valoracion == "negativa" and leido.traza_id == m.traza_id


def test_flujo_completo_sobre_postgres(motor, retriever, tmp_path) -> None:
    s = build_servicios(
        Settings(database_url=URL, seleccion_libre_de_rol=True, almacen_local_dir=str(tmp_path)),
        modelos=(FakeEmbedder(), FakeLLM(), FakeSupervisor()),
        retriever=retriever,
    )
    assert (
        s.gestor.indexar("rrhh/v.md", b"Vacaciones: 23 dias.", roles=["public"]).estado
        == "indexado"
    )
    usuario = Usuario(id="u", groups=["public"])
    conv = s.conversaciones.iniciar(usuario, "public")
    msg = s.conversaciones.preguntar(usuario, conv.id, "vacaciones")
    assert msg.documentos_consultados == ["rrhh/v.md"]
    assert s.verificar_integridad().ok
    p = s.acciones.proponer(
        "abrir_ticket", {"asunto": "VPN caída", "descripcion": "x"}, "public", "u"
    )
    assert s.acciones.decidir(p.id, True, "public", "u").estado == "ejecutada"
