"""hr_agent de punta a punta con su subgrafo, la bandeja con RLS y la confirmación del usuario.
Requiere PostgreSQL (RLS); sin él, el agente no se registra (también se prueba)."""

import os

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from sqlalchemy import text

from app.agents.hr import SqlRepositorioCasosRRHH, sensibilidad
from app.agents.scopes import contexto_de_usuario
from app.agents.subgraph import AuditoriaSql, construir_subgrafo, resumen
from app.config import Settings
from app.deps import build_registro_agentes, build_servicios
from app.persistencia import tablas as t
from app.persistencia.repositorios import crear_motor
from ingestor.sources import LocalFolderSource
from tests.conftest import SAMPLE_DOCS
from tests.fakes import FakeEmbedder, FakeLLM, FakeSupervisor, GuionLLM

URL = os.environ.get("TEST_DATABASE_URL", "")
solo_pg = [
    pytest.mark.postgres,
    pytest.mark.skipif(not URL.startswith("postgresql"), reason="sin TEST_DATABASE_URL"),
]
ANA = contexto_de_usuario("github:ana", ["public"])


def test_la_sensibilidad_por_categoria() -> None:
    assert {c: sensibilidad(c) for c in ("acoso", "salud", "discriminacion", "disciplina")} == {
        c: "confidential" for c in ("acoso", "salud", "discriminacion", "disciplina")
    }
    assert sensibilidad("horas_extra") == sensibilidad("nomina") == "normal"


def test_sin_postgres_no_hay_hr_agent(retriever, tmp_path) -> None:
    """Fallo cerrado: sin RLS no se registra el agente que guarda casos."""
    s = build_servicios(
        Settings(database_url="sqlite://", almacen_local_dir=str(tmp_path)),
        modelos=(FakeEmbedder(), FakeLLM(), FakeSupervisor()), retriever=retriever,
    )  # fmt: skip
    assert "hr_agent" not in build_registro_agentes(s)


@pytest.fixture
def servicios(retriever, tmp_path):
    if not URL.startswith("postgresql"):
        pytest.skip("sin TEST_DATABASE_URL")
    m = crear_motor(URL)
    with m.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS hr_case_notes, hr_cases CASCADE"))
    t.metadata.drop_all(m)
    m.dispose()
    s = build_servicios(
        Settings(database_url=URL, almacen_local_dir=str(tmp_path), cache_semantica=False),
        modelos=(FakeEmbedder(), FakeLLM(), FakeSupervisor()), retriever=retriever,
    )  # fmt: skip
    s.gestor.sincronizar(LocalFolderSource(SAMPLE_DOCS))
    yield s
    t.metadata.drop_all(s.motor)


def _grafo(servicios, turnos, final="He creado el caso."):  # noqa: ANN001, ANN202
    hr = build_registro_agentes(servicios)["hr_agent"]
    llm = GuionLLM(turnos, final=final)
    grafo = construir_subgrafo(
        hr, llm, AuditoriaSql(servicios.motor), roles_de=lambda u: set(),
        checkpointer=InMemorySaver(),
    )  # fmt: skip
    return grafo, llm


@pytest.mark.postgres
def test_hr_agent_declara_sus_tools(servicios) -> None:
    hr = build_registro_agentes(servicios)["hr_agent"]
    assert hr.returns == "id_only"
    assert {n: (p.scope, p.mode) for n, p in hr.tools.items()} == {
        "search_hr_policies": ("docs:read", "auto"),
        "get_my_hr_cases": ("hr_case:read_own", "auto"),
        "create_hr_case": ("hr_case:create", "confirm_user"),
        "add_hr_case_note": ("hr_case:update_own", "confirm_user"),
    }


@pytest.mark.postgres
def test_crear_un_caso_pide_confirmacion_y_devuelve_solo_el_id(servicios) -> None:
    grafo, _ = _grafo(servicios, [[("create_hr_case", {
        "category": "horas_extra", "summary": "No me pagaron las horas extra de septiembre",
    })]], final="Caso creado para ana@empresa.com por horas extra impagadas.")  # fmt: skip
    cfg = {"configurable": {"thread_id": "hr-1"}}
    pausa = grafo.invoke(
        {"task": "No me pagaron las horas extra", "user": ANA, "trace_id": "tr-hr"}, cfg
    )
    assert pausa["__interrupt__"][0].value["type"] == "confirm_user"
    casos = SqlRepositorioCasosRRHH(servicios.motor)
    assert casos.propios(ANA) == []  # nada escrito sin confirmar
    salida = grafo.invoke(Command(resume={"approved": True, "approver_id": "github:ana"}), cfg)
    (caso,) = casos.propios(ANA)
    assert resumen(salida) == caso.id  # el supervisor solo recibe el ID
    assert (caso.requester_id, caso.trace_id, caso.sensitivity) == ("github:ana", "tr-hr", "normal")
    with servicios.motor.connect() as c:
        assert c.execute(text("select tool, decision from audit_log")).all() == [
            ("create_hr_case", "approved")
        ]


@pytest.mark.postgres
def test_el_llm_no_puede_fijar_el_solicitante(servicios) -> None:
    grafo, llm = _grafo(servicios, [[("create_hr_case", {
        "category": "nomina", "summary": "Caso falso de otro", "requester_id": "github:luis",
    })]])  # fmt: skip
    grafo.invoke({"task": "x", "user": ANA}, {"configurable": {"thread_id": "hr-2"}})
    dato = next(m for m in llm.llamadas[1] if m["role"] == "tool")["content"]
    assert "argumentos no son válidos" in dato


@pytest.mark.postgres
def test_mis_casos_no_resume_los_confidenciales(servicios) -> None:
    casos = SqlRepositorioCasosRRHH(servicios.motor)
    casos.crear(ANA, "nomina", "Error en la nómina de septiembre", "tr")
    casos.crear(ANA, "salud", "Baja médica por un tratamiento", "tr")
    grafo, llm = _grafo(servicios, [[("get_my_hr_cases", {})]], final="Tienes 2 casos.")
    grafo.invoke(
        {"task": "¿Cómo van mis casos?", "user": ANA}, {"configurable": {"thread_id": "hr-3"}}
    )
    dato = next(m for m in llm.llamadas[1] if m["role"] == "tool")["content"]
    assert "Error en la nómina" in dato and "tratamiento" not in dato


@pytest.mark.postgres
def test_las_politicas_de_rrhh_solo_son_publicas_o_internas(servicios) -> None:
    """Aunque el usuario sea de RRHH, search_hr_policies no trae documentos confidenciales."""
    hr = build_registro_agentes(servicios)["hr_agent"]
    from app.agents.subgraph import ContextoTool

    rrhh = contexto_de_usuario("github:marta", ["rrhh"])
    politica = hr.tools["search_hr_policies"]
    ctx = ContextoTool(user=rrhh, agent="hr_agent", trace_id="t")
    fragmentos = politica.fn(politica.args_model(consulta="bandas salariales vacaciones"), ctx)
    assert all(not f["doc_id"].startswith("rrhh/") for f in fragmentos["fragmentos"])
