"""Fase 3, infraestructura: audit_log de solo inserción, approvals con vencimiento y
checkpointer PostgreSQL cifrado que permite reanudar un interrupt tras un reinicio.

Lo marcado `postgres` corre con TEST_DATABASE_URL (make test-postgres); el resto, en SQLite."""

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from app.agents.aprobaciones import SqlRepositorioAprobaciones
from app.agents.subgraph import AuditoriaSql, RegistroAuditoria
from app.persistencia import tablas as t
from app.persistencia.repositorios import crear_motor, inicializar

URL_PG = os.environ.get("TEST_DATABASE_URL", "")
solo_pg = [
    pytest.mark.postgres,
    pytest.mark.skipif(not URL_PG.startswith("postgresql"), reason="sin TEST_DATABASE_URL"),
]


def _motor_sqlite():
    m = crear_motor("sqlite://")
    inicializar(m)
    return m


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=solo_pg)])
def motor(request):
    if request.param == "sqlite":
        yield _motor_sqlite()
        return
    m = crear_motor(URL_PG)
    t.metadata.drop_all(m)
    inicializar(m)
    yield m
    t.metadata.drop_all(m)
    m.dispose()


def _fila(**kw) -> RegistroAuditoria:
    base = {
        "trace_id": "tr-1", "user_id": "github:ana", "agent": "hr_agent",
        "tool": "create_hr_case", "args_hash": "a" * 64, "decision": "approved",
        "approver_id": "github:ana", "fecha": "2026-10-01T10:00:00+00:00",
    }  # fmt: skip
    return RegistroAuditoria(**{**base, **kw})


# -------------------------------------------------------------------------------- audit_log
def test_audit_log_registra_cada_decision(motor) -> None:
    AuditoriaSql(motor).registrar(_fila())
    AuditoriaSql(motor).registrar(_fila(decision="deny", approver_id=None, reason="sin scope"))
    with motor.connect() as c:
        filas = c.execute(text("select decision, approver_id from audit_log order by id")).all()
    assert [tuple(f) for f in filas] == [("approved", "github:ana"), ("deny", None)]


@pytest.mark.parametrize(
    "sentencia", ["update audit_log set decision = 'allow'", "delete from audit_log"]
)
def test_audit_log_es_de_solo_insercion(motor, sentencia) -> None:
    AuditoriaSql(motor).registrar(_fila())
    with pytest.raises(DatabaseError), motor.begin() as c:
        c.execute(text(sentencia))
    with motor.connect() as c:
        assert c.execute(text("select decision from audit_log")).scalar_one() == "approved"


# -------------------------------------------------------------------------------- approvals
AHORA = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)


def _pendiente(repo, **kw):  # noqa: ANN001, ANN202
    datos = {
        "thread_id": "th-1", "trace_id": "tr-1", "agent": "support_agent",
        "tool": "create_ticket", "user_id": "github:ana", "tipo": "approve_staff",
        "approver_role": "it_support", "args_preview": '{"priority": "P1"}', "risk": "alto",
        "expires_at": (AHORA + timedelta(hours=24)).isoformat(),
    }  # fmt: skip
    return repo.crear(**{**datos, **kw})


def test_aprobacion_pendiente_y_decision(motor) -> None:
    repo = SqlRepositorioAprobaciones(motor)
    a = _pendiente(repo)
    assert repo.obtener(a.id).estado == "pendiente"
    assert [p.id for p in repo.pendientes(roles=["it_support"])] == [a.id]
    assert repo.pendientes(roles=["hr_staff"]) == []
    repo.decidir(a.id, aprobada=True, approver_id="github:soporte", motivo=None)
    final = repo.obtener(a.id)
    assert final.estado == "aprobada" and final.approver_id == "github:soporte"
    assert repo.pendientes(roles=["it_support"]) == []


def test_las_aprobaciones_vencen_a_las_24h_y_se_notifica(motor) -> None:
    avisos: list[str] = []
    repo = SqlRepositorioAprobaciones(motor, notificar=lambda a: avisos.append(a.id))
    vieja = _pendiente(repo, expires_at=(AHORA - timedelta(minutes=1)).isoformat())
    nueva = _pendiente(repo, thread_id="th-2")
    vencidas = repo.vencer(AHORA)
    assert [a.id for a in vencidas] == [vieja.id] and avisos == [vieja.id]
    assert repo.obtener(vieja.id).estado == "vencida"
    assert repo.obtener(nueva.id).estado == "pendiente"
    assert repo.vencer(AHORA) == []  # idempotente


def test_una_aprobacion_ya_resuelta_no_se_vuelve_a_decidir(motor) -> None:
    repo = SqlRepositorioAprobaciones(motor)
    a = _pendiente(repo)
    repo.decidir(a.id, aprobada=False, approver_id="github:soporte", motivo="no procede")
    assert repo.decidir(a.id, aprobada=True, approver_id="github:soporte", motivo=None) is False
    assert repo.obtener(a.id).estado == "rechazada"


# ----------------------------------------------------------------------- checkpointer cifrado
CLAVE = "0f" * 32  # 32 bytes en hex (en producción: Key Vault / .env, nunca en el repo)


def test_la_clave_del_checkpointer_es_obligatoria_y_de_32_bytes() -> None:
    from app.agents.checkpointer import clave_checkpointer

    assert len(clave_checkpointer(CLAVE)) == 32
    for mala in (None, "", "abc", "zz" * 32, "0f" * 16):
        with pytest.raises(ValueError):
            clave_checkpointer(mala)


def test_un_tipo_fuera_de_la_lista_no_se_instancia_desde_el_checkpoint() -> None:
    """Un checkpoint manipulado no puede construir clases arbitrarias: llegan como dict."""
    from app.agents.checkpointer import serializador_cifrado
    from app.persistencia.modelos import Rol

    serde = serializador_cifrado(bytes.fromhex(CLAVE))
    vuelta = serde.loads_typed(serde.dumps_typed(Rol(id="x", nombre="X")))
    assert isinstance(vuelta, dict) and not isinstance(vuelta, Rol)


def _grafo_soporte(checkpointer):  # noqa: ANN001, ANN202
    """support_agent mínimo: una escritura con confirmación del usuario."""
    from pydantic import BaseModel, ConfigDict

    from app.agents.registry import AgentSpec, ToolPolicy
    from app.agents.subgraph import AuditoriaMemoria, construir_subgrafo
    from tests.fakes import GuionLLM

    class Ticket(BaseModel):
        model_config = ConfigDict(extra="forbid")
        asunto: str

    creados: list[str] = []
    spec = AgentSpec(
        name="support_agent", description="TI", system_prompt="Soporte.",
        tools={"create_ticket": ToolPolicy(
            lambda a, ctx: creados.append(a.asunto) or {"id": "TCK-9"}, Ticket,
            scope="ticket:create", mode="confirm_user", writes=True,
        )},
    )  # fmt: skip
    llm = GuionLLM([[("create_ticket", {"asunto": "VPN de ana@empresa.com"})]])
    grafo = construir_subgrafo(
        spec, llm, AuditoriaMemoria(), roles_de=lambda u: set(), checkpointer=checkpointer
    )
    return grafo, creados


@pytest.mark.postgres
@pytest.mark.skipif(not URL_PG.startswith("postgresql"), reason="sin TEST_DATABASE_URL")
@pytest.mark.filterwarnings("error:Blocked deserialization")  # todo tipo del estado, en la lista
def test_un_interrupt_sobrevive_a_un_reinicio_y_no_queda_en_claro() -> None:
    from langgraph.types import Command

    from app.agents.checkpointer import checkpointer_postgres, clave_checkpointer
    from app.agents.scopes import contexto_de_usuario

    clave = clave_checkpointer(CLAVE)
    cfg = {"configurable": {"thread_id": f"reinicio-{datetime.now(UTC).timestamp()}"}}
    entrada = {
        "task": "Mi VPN no va, soy ana@empresa.com",
        "user": contexto_de_usuario("u1", ["public"]),
    }

    with checkpointer_postgres(URL_PG, clave) as saver:  # proceso 1: se pausa
        grafo, creados = _grafo_soporte(saver)
        assert "__interrupt__" in grafo.invoke(entrada, cfg) and creados == []

    m = crear_motor(URL_PG)  # en reposo: nada legible (ni la tarea ni la PII)
    hilo = {"t": cfg["configurable"]["thread_id"]}
    with m.connect() as c:
        consultas = [
            "select blob from checkpoint_blobs where thread_id = :t and blob is not null",
            "select blob from checkpoint_writes where thread_id = :t and blob is not null",
            "select convert_to(checkpoint::text || metadata::text, 'UTF8') "
            "from checkpoints where thread_id = :t",
        ]
        crudo = b"".join(bytes(f[0]) for q in consultas for f in c.execute(text(q), hilo))
    m.dispose()
    assert crudo  # hay estado guardado
    assert b"ana@empresa.com" not in crudo and b"Mi VPN no va" not in crudo

    with checkpointer_postgres(URL_PG, clave) as saver:  # proceso 2: grafo y conexión nuevos
        grafo, creados = _grafo_soporte(saver)
        grafo.invoke(Command(resume={"approved": True, "approver_id": "u1"}), cfg)
        assert creados == ["VPN de ana@empresa.com"]


# ------------------------------------------------------------- approvals dentro del subgrafo
def _grafo_con_aprobaciones(motor, prioridad="P3"):  # noqa: ANN001, ANN202
    from langgraph.checkpoint.memory import InMemorySaver
    from pydantic import BaseModel, ConfigDict

    from app.agents.registry import AgentSpec, ToolPolicy
    from app.agents.subgraph import construir_subgrafo
    from tests.fakes import GuionLLM

    class Ticket(BaseModel):
        model_config = ConfigDict(extra="forbid")
        asunto: str
        priority: str = "P3"

    creados: list[str] = []
    spec = AgentSpec(
        name="support_agent", description="TI", system_prompt="Soporte.",
        tools={"create_ticket": ToolPolicy(
            lambda a, ctx: creados.append(a.asunto) or {"id": "TCK-1"}, Ticket,
            scope="ticket:create", mode="confirm_user", writes=True, approver_role="it_support",
            mode_if=lambda a: "approve_staff" if a.priority == "P1" else "confirm_user",
        )},
    )  # fmt: skip
    repo = SqlRepositorioAprobaciones(motor)
    grafo = construir_subgrafo(
        spec, GuionLLM([[("create_ticket", {"asunto": "VPN", "priority": prioridad})]]),
        AuditoriaSql(motor), roles_de=lambda u: {"it_support"} if u == "s1" else set(),
        checkpointer=InMemorySaver(), aprobaciones=repo,
    )  # fmt: skip
    return grafo, repo, creados


def test_cada_pausa_queda_en_approvals_y_se_resuelve_una_vez(motor) -> None:
    from langgraph.types import Command

    from app.agents.scopes import contexto_de_usuario

    grafo, repo, creados = _grafo_con_aprobaciones(motor, prioridad="P1")
    cfg = {"configurable": {"thread_id": "th-aprob"}}
    grafo.invoke({"task": "VPN caída", "user": contexto_de_usuario("u1", ["public"])}, cfg)
    (pendiente,) = repo.pendientes(roles=["it_support"])
    assert (pendiente.thread_id, pendiente.tipo, pendiente.estado) == (
        "th-aprob", "approve_staff", "pendiente",
    )  # fmt: skip
    grafo.invoke(Command(resume={"approved": True, "approver_id": "s1"}), cfg)
    assert creados == ["VPN"] and repo.obtener(pendiente.id).estado == "aprobada"
    with motor.connect() as c:
        assert c.execute(text("select decision, approver_id from audit_log")).all() == [
            ("approved", "s1")
        ]


def test_una_aprobacion_vencida_no_ejecuta_aunque_se_reanude(motor) -> None:
    from langgraph.types import Command

    from app.agents.scopes import contexto_de_usuario

    grafo, repo, creados = _grafo_con_aprobaciones(motor)
    cfg = {"configurable": {"thread_id": "th-vencida"}}
    grafo.invoke({"task": "VPN", "user": contexto_de_usuario("u1", ["public"])}, cfg)
    (pendiente,) = repo.pendientes(user_id="u1")
    repo.vencer(datetime.fromisoformat(pendiente.expires_at) + timedelta(seconds=1))
    grafo.invoke(Command(resume={"approved": True, "approver_id": "u1"}), cfg)
    assert creados == [] and repo.obtener(pendiente.id).estado == "vencida"
