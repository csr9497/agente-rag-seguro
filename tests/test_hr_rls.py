"""hr_cases con Row Level Security (PostgreSQL). La app fija app.user_id y app.roles por
transacción desde el token y actúa con el rol `agente_rls` (sin bypass): aunque la conexión
sea de un superusuario, las políticas se aplican."""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from app.agents.hr import SqlRepositorioCasosRRHH
from app.agents.scopes import contexto_de_usuario
from app.persistencia import tablas as t
from app.persistencia.repositorios import crear_motor, inicializar
from app.persistencia.rls import sesion_rls

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not URL.startswith("postgresql"), reason="sin TEST_DATABASE_URL"),
]

ANA = contexto_de_usuario("github:ana", ["public"])
LUIS = contexto_de_usuario("github:luis", ["public"])
STAFF = contexto_de_usuario("github:rrhh1", ["public", "hr_staff"])
ESPECIALISTA = contexto_de_usuario("github:rrhh2", ["public", "hr_specialist"])
SOPORTE = contexto_de_usuario("github:ti1", ["public", "it_support"])


@pytest.fixture
def repo():
    m = crear_motor(URL)
    with m.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS hr_case_notes, hr_cases CASCADE"))
    t.metadata.drop_all(m)
    inicializar(m)
    yield SqlRepositorioCasosRRHH(m)
    t.metadata.drop_all(m)
    m.dispose()


def _casos(repo):  # noqa: ANN001, ANN202
    normal = repo.crear(ANA, "horas_extra", "No me pagaron las horas extra de septiembre", "tr-1")
    confidencial = repo.crear(ANA, "acoso", "Situación de acoso con mi responsable", "tr-2")
    return normal, confidencial


def test_la_sensibilidad_la_decide_el_codigo_por_categoria(repo) -> None:
    normal, confidencial = _casos(repo)
    assert (normal.sensitivity, confidencial.sensitivity) == ("normal", "confidential")
    assert normal.requester_id == "github:ana" and normal.status == "open"


def test_cada_rol_ve_lo_suyo(repo) -> None:
    """Criterio de aceptación: hr_staff no ve los confidenciales; hr_specialist sí."""
    normal, confidencial = _casos(repo)
    ids = lambda u: {c.id for c in repo.visibles(u)}  # noqa: E731
    assert ids(ANA) == {normal.id, confidencial.id}  # sus propios casos
    assert ids(LUIS) == set()  # casos ajenos: nada
    assert ids(STAFF) == {normal.id}  # cola de RR.HH. sin confidenciales
    assert ids(ESPECIALISTA) == {normal.id, confidencial.id}
    assert ids(SOPORTE) == set()  # soporte no ve casos de RR.HH.


def test_nadie_crea_casos_en_nombre_de_otro(repo) -> None:
    with pytest.raises(DatabaseError), sesion_rls(repo.motor, LUIS) as c:
        c.execute(text(
            "INSERT INTO hr_cases (id, requester_id, category, sensitivity, summary, status, "
            "created_by_agent, trace_id, created_at) VALUES ('x', 'github:ana', 'nomina', "
            "'normal', 'falso', 'open', 'hr_agent', 'tr', '2026')"
        ))  # fmt: skip


def test_ni_siquiera_un_rol_de_rrhh_puede_borrar_cerrar_o_reasignar(repo) -> None:
    normal, _ = _casos(repo)
    for sentencia in (
        "UPDATE hr_cases SET status = 'closed'",
        "UPDATE hr_cases SET requester_id = 'github:luis'",
        "DELETE FROM hr_cases",
    ):
        with pytest.raises(DatabaseError), sesion_rls(repo.motor, ESPECIALISTA) as c:
            c.execute(text(sentencia))
    assert repo.visibles(ANA)[0].status == "open"


def test_notas_solo_en_casos_propios_y_abiertos(repo) -> None:
    normal, _ = _casos(repo)
    assert repo.agregar_nota(ANA, normal.id, "Adjunto el cuadrante de horas") is True
    assert repo.agregar_nota(LUIS, normal.id, "Me cuelo en un caso ajeno") is False
    assert repo.agregar_nota(STAFF, normal.id, "Nota de RR.HH. vía agente") is False
    with repo.motor.begin() as c:  # se cierra fuera de los agentes (proceso de RR.HH.)
        c.execute(text("UPDATE hr_cases SET status = 'closed' WHERE id = :i"), {"i": normal.id})
    assert repo.agregar_nota(ANA, normal.id, "Otra nota") is False


def test_sin_contexto_de_usuario_no_se_ve_nada(repo) -> None:
    _casos(repo)
    with repo.motor.begin() as c:
        c.execute(text("SET LOCAL ROLE agente_rls"))
        assert c.execute(text("SELECT count(*) FROM hr_cases")).scalar_one() == 0
