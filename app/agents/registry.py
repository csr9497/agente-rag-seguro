"""Registro de agentes: cada agente declara sus tools con el scope y el modo que exigen.

Agregar un agente no requiere tocar el grafo principal: basta con registrar su AgentSpec. Las
reglas de permisos viven aquí y en policy_gate (código), nunca en el prompt (regla 1).
"""

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from app.agents.scopes import UserContext

Mode = Literal["auto", "confirm_user", "approve_staff", "deny"]


@dataclass(frozen=True)
class ToolPolicy:
    """`fn(args, contexto)` ejecuta la tool con los argumentos ya validados por `args_model`.
    `writes`: la tool escribe (ticket, caso…); nunca puede correr en «auto» (regla 4).
    `mode_if`: modo según los argumentos (p. ej. P1 → approve_staff)."""

    fn: Callable[[Any, Any], Any]
    args_model: type[BaseModel]
    scope: str
    mode: Mode
    writes: bool = False
    approver_role: str | None = None  # quién puede aprobar en approve_staff
    mode_if: Callable[[Any], Mode] | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if self.mode == "approve_staff" and not self.approver_role:
            raise ValueError("approve_staff exige approver_role")


@dataclass(frozen=True)
class AgentSpec:
    name: str
    description: str  # el supervisor enruta leyendo esto
    system_prompt: str
    tools: Mapping[str, ToolPolicy] = field(default_factory=dict)
    max_iterations: int = 6
    returns: Literal["summary", "id_only"] = "summary"
    # Con id_only, lo que ve el usuario (plantilla, sin LLM: no puede colarse nada del caso).
    mensaje_id_only: str = "He registrado tu solicitud con la referencia {ids}."


class RegistroAgentes(Mapping[str, AgentSpec]):
    def __init__(self) -> None:
        self._agentes: dict[str, AgentSpec] = {}

    def register(self, spec: AgentSpec) -> AgentSpec:
        if spec.name in self._agentes:
            raise ValueError(f"agente duplicado: {spec.name}")
        for nombre, politica in spec.tools.items():
            if politica.writes and politica.mode == "auto":
                raise ValueError(f"{spec.name}.{nombre}: una escritura no puede correr en auto")
        self._agentes[spec.name] = spec
        return spec

    def __getitem__(self, nombre: str) -> AgentSpec:
        return self._agentes[nombre]

    def __iter__(self) -> Iterator[str]:
        return iter(self._agentes)

    def __len__(self) -> int:
        return len(self._agentes)


AGENTS = RegistroAgentes()
register = AGENTS.register


def resolve_mode(policy: ToolPolicy, args: BaseModel, user: UserContext) -> Mode:
    """Modo efectivo de una llamada: sin el scope, deny; si no, el de `mode_if` o el fijo."""
    if policy.scope not in user.scopes:
        return "deny"
    modo = policy.mode_if(args) if policy.mode_if else policy.mode
    if policy.writes and modo == "auto":
        return "deny"  # defensa en profundidad de la regla 4
    if modo == "approve_staff" and not policy.approver_role:
        return "deny"
    return modo
