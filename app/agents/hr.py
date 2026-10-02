"""hr_agent: detecta problemas que debe atender RR.HH., crea un caso en su bandeja (hr_cases)
y consulta políticas de RR.HH. Devuelve al supervisor solo el ID del caso (regla 5).

- Los casos viven en PostgreSQL con RLS (app/persistencia/rls.py): sin RLS no se registra.
- La sensibilidad la decide el código por categoría (acoso, salud, discriminación,
  disciplina → confidential), nunca el LLM. Los confidenciales solo los ve hr_specialist y no
  se resumen en el chat (regla 9).
- `requester_id` sale del token (ContextoTool.user), nunca de los argumentos del LLM.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, insert, select
from sqlalchemy.exc import DatabaseError

from app.agents.rag import BuscarDocumentosArgs, HerramientasRag
from app.agents.registry import AgentSpec, ToolPolicy
from app.agents.scopes import UserContext
from app.agents.subgraph import ContextoTool
from app.persistencia import tablas as t
from app.persistencia.repositorios import RepositorioDocumentos
from app.persistencia.rls import sesion_rls
from app.prompts import local

HR_PROMPT = local("hr_agent")

Categoria = Literal[
    "nomina", "horas_extra", "vacaciones", "licencias", "beneficios", "conflicto",
    "acoso", "salud", "discriminacion", "disciplina", "otro",
]  # fmt: skip
CONFIDENCIALES = frozenset({"acoso", "salud", "discriminacion", "disciplina"})


def sensibilidad(categoria: str) -> Literal["normal", "confidential"]:
    return "confidential" if categoria in CONFIDENCIALES else "normal"


class CasoRRHH(BaseModel):
    id: str
    requester_id: str
    category: str
    sensitivity: Literal["normal", "confidential"]
    summary: str
    status: str
    created_by_agent: str
    trace_id: str
    created_at: str


class SqlRepositorioCasosRRHH:
    """Adaptador de la bandeja de RR.HH. sobre PostgreSQL con RLS (no hay sistema externo)."""

    def __init__(self, motor: Engine) -> None:
        self.motor = motor

    def crear(
        self, user: UserContext, categoria: str, resumen: str, trace_id: str,
        agente: str = "hr_agent",
    ) -> CasoRRHH:  # fmt: skip
        caso = CasoRRHH(
            id=str(uuid.uuid4()), requester_id=user.id, category=categoria,
            sensitivity=sensibilidad(categoria), summary=resumen, status="open",
            created_by_agent=agente, trace_id=trace_id,
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )  # fmt: skip
        with sesion_rls(self.motor, user) as c:
            c.execute(insert(t.hr_cases).values(**caso.model_dump()))
        return caso

    def visibles(self, user: UserContext) -> list[CasoRRHH]:
        """Lo que RLS deja ver a este usuario (los suyos, la cola de RR.HH. según su rol)."""
        with sesion_rls(self.motor, user) as c:
            filas = c.execute(select(t.hr_cases).order_by(t.hr_cases.c.created_at)).mappings()
            return [CasoRRHH(**f) for f in filas]

    def propios(self, user: UserContext) -> list[CasoRRHH]:
        return [c for c in self.visibles(user) if c.requester_id == user.id]

    def agregar_nota(self, user: UserContext, case_id: str, nota: str) -> bool:
        """False si el caso no es propio, no existe o no está abierto (lo decide RLS)."""
        try:
            with sesion_rls(self.motor, user) as c:
                c.execute(insert(t.hr_case_notes).values(
                    id=str(uuid.uuid4()), case_id=case_id, author_id=user.id, note=nota,
                    created_at=datetime.now(UTC).isoformat(timespec="seconds"),
                ))  # fmt: skip
        except DatabaseError:
            return False
        return True


# ---------------------------------------------------------------------------------- tools
class CrearCasoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Categoria
    summary: str = Field(min_length=5, max_length=2000, description="Qué ocurre, sin juicios")


class NotaArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(pattern=r"^[0-9a-f\-]{36}$")
    note: str = Field(min_length=1, max_length=2000)


class SinArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HerramientasHR:
    def __init__(
        self, casos: SqlRepositorioCasosRRHH, rag: HerramientasRag, registro: RepositorioDocumentos
    ) -> None:
        self._casos, self._rag, self._registro = casos, rag, registro

    def search_hr_policies(self, args: BuscarDocumentosArgs, ctx: ContextoTool) -> dict[str, Any]:
        """Políticas de RR.HH.: solo documentos públicos o internos que el usuario puede leer."""
        return self._rag.search_public_internal(args, ctx)

    def get_my_hr_cases(self, args: SinArgs, ctx: ContextoTool) -> dict[str, Any]:
        """Estado de los casos propios; de los confidenciales no se da el resumen (regla 9)."""
        return {
            "casos": [
                {"id": c.id, "category": c.category, "status": c.status,
                 "created_at": c.created_at,
                 **({"summary": c.summary} if c.sensitivity == "normal" else {})}
                for c in self._casos.propios(ctx.user)
            ]
        }  # fmt: skip

    def create_hr_case(self, args: CrearCasoArgs, ctx: ContextoTool) -> dict[str, Any]:
        caso = self._casos.crear(ctx.user, args.category, args.summary, ctx.trace_id, ctx.agent)
        return {"id": caso.id}

    def add_hr_case_note(self, args: NotaArgs, ctx: ContextoTool) -> dict[str, Any]:
        if not self._casos.agregar_nota(ctx.user, args.case_id, args.note):
            return {
                "error": "No se pudo añadir la nota: el caso no existe, no es tuyo o está cerrado."
            }
        return {"id": args.case_id, "nota": "añadida"}


def crear_hr_agent(h: HerramientasHR) -> AgentSpec:
    return AgentSpec(
        name="hr_agent",
        description=(
            "Problemas laborales del usuario que debe atender RR.HH.: pagos, horas extra, "
            "vacaciones, licencias, conflictos, acoso. También dudas sobre políticas de RR.HH."
        ),
        system_prompt=HR_PROMPT,
        returns="id_only",
        mensaje_id_only=(
            "He registrado tu caso para RR.HH. con la referencia {ids}. El equipo de RR.HH. lo "
            "revisará; puedes consultar su estado preguntándome por tus casos."
        ),
        tools={
            "search_hr_policies": ToolPolicy(
                h.search_hr_policies, BuscarDocumentosArgs, scope="docs:read", mode="auto",
                description="Busca en las políticas de RR.HH. (vacaciones, beneficios, licencias).",
            ),
            "get_my_hr_cases": ToolPolicy(
                h.get_my_hr_cases, SinArgs, scope="hr_case:read_own", mode="auto",
                description="Estado de los casos de RR.HH. del propio usuario.",
            ),
            "create_hr_case": ToolPolicy(
                h.create_hr_case, CrearCasoArgs, scope="hr_case:create",
                mode="confirm_user", writes=True,
                description="Crea un caso para RR.HH. con su categoría y un resumen.",
            ),
            "add_hr_case_note": ToolPolicy(
                h.add_hr_case_note, NotaArgs, scope="hr_case:update_own",
                mode="confirm_user", writes=True,
                description="Añade información a un caso propio abierto.",
            ),
        },
    )  # fmt: skip
