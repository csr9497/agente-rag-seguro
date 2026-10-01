"""Registro de agentes (AgentSpec/ToolPolicy), scopes por rol y policy_gate.

Reglas de la spec que se prueban aquí: permisos en código (1), ninguna escritura en «auto» (4)
y decisión determinista allow / deny / needs_approval."""

import json

import pytest
from pydantic import BaseModel, ConfigDict

from app.agents.policy_gate import evaluar
from app.agents.registry import AgentSpec, RegistroAgentes, ToolPolicy, resolve_mode
from app.agents.scopes import ROLE_SCOPES, UserContext, contexto_de_usuario
from app.models.schemas import ToolCall


class Busqueda(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consulta: str


class Ticket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asunto: str
    priority: str = "P3"


def _fn(args, ctx):  # noqa: ANN001, ANN202
    return {"ok": True}


def ticket_mode(args: Ticket) -> str:
    return "approve_staff" if args.priority == "P1" else "confirm_user"


BUSCAR = ToolPolicy(_fn, Busqueda, scope="docs:read", mode="auto")
CREAR = ToolPolicy(
    _fn, Ticket, scope="ticket:create", mode="confirm_user", writes=True,
    mode_if=ticket_mode, approver_role="it_support",
)  # fmt: skip
SOPORTE = AgentSpec(
    name="support_agent", description="Incidencias de TI", system_prompt="Eres soporte.",
    tools={"search_it_kb": BUSCAR, "create_ticket": CREAR}, max_iterations=4,
)  # fmt: skip
EMPLEADO = UserContext(id="u1", roles=("public",), scopes=frozenset({"docs:read", "ticket:create"}))


def _llamada(nombre: str, args: dict) -> ToolCall:
    return ToolCall(id="c1", nombre=nombre, argumentos=json.dumps(args))


# ------------------------------------------------------------------------------ registro
def test_registro_y_duplicados() -> None:
    reg = RegistroAgentes()
    reg.register(SOPORTE)
    assert reg["support_agent"] is SOPORTE and list(reg) == ["support_agent"]
    with pytest.raises(ValueError, match="duplicado"):
        reg.register(SOPORTE)


def test_una_escritura_en_auto_no_se_puede_registrar() -> None:
    peligrosa = ToolPolicy(_fn, Ticket, scope="ticket:create", mode="auto", writes=True)
    with pytest.raises(ValueError, match="escritura"):
        RegistroAgentes().register(
            AgentSpec(name="x", description="d", system_prompt="p", tools={"t": peligrosa})
        )


def test_approve_staff_exige_rol_aprobador() -> None:
    with pytest.raises(ValueError, match="approver_role"):
        ToolPolicy(_fn, Ticket, scope="ticket:create", mode="approve_staff", writes=True)


# ----------------------------------------------------------------------------- resolve_mode
def test_resolve_mode_por_scope_y_argumentos() -> None:
    assert resolve_mode(BUSCAR, Busqueda(consulta="vpn"), EMPLEADO) == "auto"
    assert resolve_mode(CREAR, Ticket(asunto="x", priority="P3"), EMPLEADO) == "confirm_user"
    assert resolve_mode(CREAR, Ticket(asunto="x", priority="P1"), EMPLEADO) == "approve_staff"
    sin_scope = UserContext(id="u2", roles=("public",), scopes=frozenset({"docs:read"}))
    assert resolve_mode(CREAR, Ticket(asunto="x"), sin_scope) == "deny"


def test_si_mode_if_devuelve_auto_en_una_escritura_se_deniega() -> None:
    """Defensa en profundidad de la regla 4: nada que escriba corre en auto."""
    traviesa = ToolPolicy(
        _fn, Ticket, scope="ticket:create", mode="confirm_user", writes=True,
        mode_if=lambda a: "auto",
    )  # fmt: skip
    assert resolve_mode(traviesa, Ticket(asunto="x"), EMPLEADO) == "deny"


# ---------------------------------------------------------------------------------- scopes
def test_scopes_salen_de_los_roles() -> None:
    u = contexto_de_usuario("u1", ["public"])
    assert {"docs:read", "ticket:create", "hr_case:create"} <= u.scopes
    assert "ticket:approve_p1" not in u.scopes
    soporte = contexto_de_usuario("u2", ["public", "it_support"])
    assert "ticket:approve_p1" in soporte.scopes
    assert contexto_de_usuario("u3", []).scopes == frozenset()
    assert contexto_de_usuario("u4", ["rol_inventado"]).scopes == frozenset()


def test_ningun_rol_puede_borrar_reasignar_ni_cerrar() -> None:
    todos = set().union(*ROLE_SCOPES.values())
    assert not any(s.split(":")[1].startswith(("delete", "reassign", "close")) for s in todos)


def test_el_contexto_de_usuario_es_inmutable() -> None:
    with pytest.raises(Exception):  # noqa: B017, PT011 — pydantic frozen
        EMPLEADO.id = "otro"  # type: ignore[misc]


# ------------------------------------------------------------------------------ policy_gate
def test_gate_permite_lectura_en_auto() -> None:
    d = evaluar(SOPORTE, _llamada("search_it_kb", {"consulta": "vpn"}), EMPLEADO, iteraciones=0)
    assert d.decision == "allow" and d.mode == "auto" and d.args == {"consulta": "vpn"}


def test_gate_pide_aprobacion_para_escribir() -> None:
    d = evaluar(SOPORTE, _llamada("create_ticket", {"asunto": "VPN"}), EMPLEADO, iteraciones=0)
    assert d.decision == "needs_approval" and d.mode == "confirm_user"
    p1 = evaluar(
        SOPORTE, _llamada("create_ticket", {"asunto": "caído", "priority": "P1"}), EMPLEADO, 0
    )
    assert p1.decision == "needs_approval" and p1.mode == "approve_staff"
    assert p1.approver_role == "it_support"


@pytest.mark.parametrize(
    ("llamada", "motivo"),
    [
        (_llamada("borrar_ticket", {}), "no está disponible"),
        (_llamada("search_hr_policies", {"consulta": "x"}), "no está disponible"),
        (_llamada("create_ticket", {"asunto": "x", "requester_id": "otro"}), "argumentos"),
        (ToolCall(id="c", nombre="search_it_kb", argumentos="{no json"), "argumentos"),
    ],
)
def test_gate_deniega_lo_que_no_es_del_agente_o_no_valida(llamada, motivo) -> None:
    d = evaluar(SOPORTE, llamada, EMPLEADO, iteraciones=0)
    assert d.decision == "deny" and motivo in d.reason


def test_gate_deniega_sin_scope() -> None:
    sin_scope = UserContext(id="u2", roles=("public",), scopes=frozenset({"docs:read"}))
    d = evaluar(SOPORTE, _llamada("create_ticket", {"asunto": "x"}), sin_scope, iteraciones=0)
    assert d.decision == "deny" and "permiso" in d.reason


def test_gate_deniega_sin_presupuesto() -> None:
    d = evaluar(SOPORTE, _llamada("search_it_kb", {"consulta": "vpn"}), EMPLEADO, iteraciones=4)
    assert d.decision == "deny" and "presupuesto" in d.reason
