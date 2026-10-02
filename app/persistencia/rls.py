"""Row Level Security de PostgreSQL para los datos de los agentes (hr_cases, tickets…).

- La app actúa con el rol `agente_rls`: sin login, sin superusuario y sin BYPASSRLS. Así las
  políticas se aplican aunque la conexión sea de un superusuario (Docker local) o del dueño de
  las tablas (Azure). Solo tiene SELECT/INSERT: ningún agente actualiza, cierra ni borra.
- `sesion_rls` abre una transacción con `SET LOCAL ROLE agente_rls` y fija app.user_id y
  app.roles desde el token (UserContext), nunca desde el LLM. Sin ellos no se ve nada.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, text

from app.agents.scopes import UserContext

ROL_RLS = "agente_rls"
_USUARIO = "current_setting('app.user_id', true)"
_TIENE_ROL = "'{rol}' = any(string_to_array(current_setting('app.roles', true), ','))"

# (tabla, política, operación, USING | WITH CHECK). El DDL no admite parámetros: solo se
# interpolan constantes de este módulo (nunca datos de usuario), de ahí los noqa: S608.
POLITICAS = [
    ("hr_cases", "requester_reads_own", "SELECT", f"USING (requester_id = {_USUARIO})"),
    ("hr_cases", "hr_staff_reads_normal", "SELECT",
     f"USING ({_TIENE_ROL.format(rol='hr_staff')} AND sensitivity = 'normal')"),
    ("hr_cases", "hr_specialist_reads_all", "SELECT",
     f"USING ({_TIENE_ROL.format(rol='hr_specialist')})"),
    ("hr_cases", "requester_inserts_own", "INSERT", f"WITH CHECK (requester_id = {_USUARIO})"),
    # Notas: visibles si el caso es visible; solo el solicitante, en su caso abierto.
    ("hr_case_notes", "notes_read_with_case", "SELECT",
     "USING (EXISTS (SELECT 1 FROM hr_cases c WHERE c.id = case_id))"),
    ("hr_case_notes", "requester_notes_own_open", "INSERT",
     f"WITH CHECK (author_id = {_USUARIO} AND EXISTS (SELECT 1 FROM hr_cases c "  # noqa: S608
     f"WHERE c.id = case_id AND c.requester_id = {_USUARIO} AND c.status = 'open'))"),
    # Tickets: su solicitante y la cola de it_support (RR.HH. no los ve).
    ("tickets", "requester_reads_own_tickets", "SELECT", f"USING (requester_id = {_USUARIO})"),
    ("tickets", "it_support_reads_queue", "SELECT",
     f"USING ({_TIENE_ROL.format(rol='it_support')})"),
    ("tickets", "requester_inserts_own_ticket", "INSERT",
     f"WITH CHECK (requester_id = {_USUARIO})"),
    ("ticket_comments", "comments_read_with_ticket", "SELECT",
     "USING (EXISTS (SELECT 1 FROM tickets k WHERE k.id = ticket_id))"),
    ("ticket_comments", "requester_comments_own_open", "INSERT",
     f"WITH CHECK (author_id = {_USUARIO} AND EXISTS (SELECT 1 FROM tickets k "  # noqa: S608
     f"WHERE k.id = ticket_id AND k.requester_id = {_USUARIO} AND k.status = 'open'))"),
]  # fmt: skip
TABLAS_RLS = sorted({tabla for tabla, *_ in POLITICAS})


def preparar_rls(motor: Engine) -> None:
    """Idempotente. Solo PostgreSQL (en SQLite no hay RLS: los agentes que lo necesitan no se
    registran)."""
    if motor.dialect.name != "postgresql":
        return
    with motor.begin() as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROL_RLS}') "  # noqa: S608
            f"THEN CREATE ROLE {ROL_RLS} NOLOGIN NOSUPERUSER NOBYPASSRLS; END IF; END $$"
        ))  # fmt: skip
        c.execute(text(f"GRANT {ROL_RLS} TO CURRENT_USER"))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {ROL_RLS}"))
        for tabla in TABLAS_RLS:
            c.execute(text(f"REVOKE ALL ON {tabla} FROM {ROL_RLS}"))
            c.execute(text(f"GRANT SELECT, INSERT ON {tabla} TO {ROL_RLS}"))
            c.execute(text(f"ALTER TABLE {tabla} ENABLE ROW LEVEL SECURITY"))
            c.execute(text(f"ALTER TABLE {tabla} FORCE ROW LEVEL SECURITY"))
        for tabla, nombre, operacion, condicion in POLITICAS:
            c.execute(text(f"DROP POLICY IF EXISTS {nombre} ON {tabla}"))
            c.execute(text(
                f"CREATE POLICY {nombre} ON {tabla} FOR {operacion} TO {ROL_RLS} {condicion}"
            ))  # fmt: skip


@contextmanager
def sesion_rls(motor: Engine, user: UserContext) -> Iterator[Connection]:
    if motor.dialect.name != "postgresql":
        raise RuntimeError("Los datos con RLS exigen PostgreSQL")
    with motor.begin() as c:
        c.execute(text(f"SET LOCAL ROLE {ROL_RLS}"))
        c.execute(
            text("SELECT set_config('app.user_id', :u, true), set_config('app.roles', :r, true)"),
            {"u": user.id, "r": ",".join(user.roles)},
        )
        yield c
