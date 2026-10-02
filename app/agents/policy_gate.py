"""policy_gate: decide en código, sin LLM, si una llamada a tool se ejecuta, se deniega o
necesita aprobación humana (reglas 1 y 4 de la spec).

Comprueba, en orden: que la tool sea del agente, que los argumentos validen contra su modelo
(sin campos extra: el LLM no puede fijar requester_id ni similares), que quede presupuesto y
el modo efectivo (scope del usuario + mode_if).
"""

import json
from typing import Literal

from pydantic import BaseModel, ValidationError

from app.agents.registry import AgentSpec, Mode, resolve_mode
from app.agents.scopes import UserContext
from app.models.schemas import ToolCall


class DecisionGate(BaseModel):
    decision: Literal["allow", "deny", "needs_approval"]
    mode: Mode
    tool: str
    args: dict | None = None  # validados y normalizados por el args_model
    reason: str | None = None
    approver_role: str | None = None


def evaluar(
    spec: AgentSpec, llamada: ToolCall, user: UserContext, iteraciones: int
) -> DecisionGate:
    politica = spec.tools.get(llamada.nombre)
    if politica is None:
        return _denegar(llamada, f"La herramienta '{llamada.nombre}' no está disponible.")
    try:
        args = politica.args_model.model_validate(json.loads(llamada.argumentos or "{}"))
    except (ValueError, ValidationError):
        return _denegar(llamada, "Los argumentos no son válidos para esta herramienta.")
    if iteraciones >= spec.max_iterations:
        return _denegar(llamada, "Se agotó el presupuesto de iteraciones del agente.")
    modo = resolve_mode(politica, args, user)
    datos = args.model_dump(mode="json")
    if modo == "deny":
        return _denegar(llamada, "El usuario no tiene permiso para esta acción.", datos)
    if modo == "auto":
        return DecisionGate(decision="allow", mode=modo, tool=llamada.nombre, args=datos)
    return DecisionGate(
        decision="needs_approval", mode=modo, tool=llamada.nombre, args=datos,
        approver_role=politica.approver_role if modo == "approve_staff" else None,
    )  # fmt: skip


def _denegar(llamada: ToolCall, motivo: str, args: dict | None = None) -> DecisionGate:
    return DecisionGate(decision="deny", mode="deny", tool=llamada.nombre, args=args, reason=motivo)
