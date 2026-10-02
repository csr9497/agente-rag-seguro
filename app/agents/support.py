"""support_agent: busca la solución en la base de conocimiento de TI y, si no se resuelve,
crea o actualiza un ticket de soporte. Antes de crear, busca tickets propios abiertos
parecidos para ofrecer comentar ese ticket en lugar de duplicarlo.

- Tickets en PostgreSQL con RLS (app/persistencia/rls.py): sin RLS no se registra.
- P1 (crítico) exige la aprobación de it_support; nunca del solicitante (policy_gate +
  subgrafo). P2–P4 los confirma el propio usuario.
- `requester_id` sale del token, nunca del LLM.
"""

import re
import unicodedata
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, insert, select
from sqlalchemy.exc import DatabaseError

from app.agents.rag import BuscarDocumentosArgs, HerramientasRag
from app.agents.registry import AgentSpec, Mode, ToolPolicy
from app.agents.scopes import UserContext
from app.agents.subgraph import ContextoTool
from app.persistencia import tablas as t
from app.persistencia.rls import sesion_rls
from app.prompts import local

SUPPORT_PROMPT = local("support_agent")

Categoria = Literal["hardware", "software", "red", "accesos", "otro"]
Prioridad = Literal["P1", "P2", "P3", "P4"]
UMBRAL_PARECIDO = 0.3  # solapamiento de palabras para proponer un ticket abierto como duplicado


class Ticket(BaseModel):
    id: str
    requester_id: str
    category: str
    priority: str
    title: str
    steps: str
    status: str
    created_by_agent: str
    trace_id: str
    created_at: str


class SqlRepositorioTickets:
    """Adaptador del sistema de tickets sobre PostgreSQL con RLS (no hay sistema externo)."""

    def __init__(self, motor: Engine) -> None:
        self.motor = motor

    def crear(
        self, user: UserContext, categoria: str, prioridad: str, titulo: str, pasos: str,
        trace_id: str, agente: str = "support_agent",
    ) -> Ticket:  # fmt: skip
        ticket = Ticket(
            id=str(uuid.uuid4()), requester_id=user.id, category=categoria, priority=prioridad,
            title=titulo, steps=pasos, status="open", created_by_agent=agente, trace_id=trace_id,
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )  # fmt: skip
        with sesion_rls(self.motor, user) as c:
            c.execute(insert(t.tickets).values(**ticket.model_dump()))
        return ticket

    def visibles(self, user: UserContext) -> list[Ticket]:
        with sesion_rls(self.motor, user) as c:
            filas = c.execute(select(t.tickets).order_by(t.tickets.c.created_at)).mappings()
            return [Ticket(**f) for f in filas]

    def parecidos_abiertos(self, user: UserContext, consulta: str) -> list[Ticket]:
        """Tickets propios abiertos que se parecen a la consulta (para no duplicar)."""
        objetivo = _palabras(consulta)
        candidatos = [
            (_parecido(objetivo, _palabras(f"{k.title} {k.steps}")), k)
            for k in self.visibles(user)
            if k.requester_id == user.id and k.status == "open"
        ]
        return [k for p, k in sorted(candidatos, key=lambda x: -x[0]) if p >= UMBRAL_PARECIDO]

    def comentar(self, user: UserContext, ticket_id: str, comentario: str) -> bool:
        """False si el ticket no es propio, no existe o está cerrado (lo decide RLS)."""
        try:
            with sesion_rls(self.motor, user) as c:
                c.execute(insert(t.ticket_comments).values(
                    id=str(uuid.uuid4()), ticket_id=ticket_id, author_id=user.id,
                    comment=comentario, created_at=datetime.now(UTC).isoformat(timespec="seconds"),
                ))  # fmt: skip
        except DatabaseError:
            return False
        return True


def _palabras(texto: str) -> set[str]:
    sin_tildes = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    return {p for p in re.findall(r"[a-z0-9]+", sin_tildes) if len(p) > 2}


def _parecido(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a) if a else 0.0


# ---------------------------------------------------------------------------------- tools
class BuscarTicketsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consulta: str = Field(min_length=1, max_length=300, description="Tema de la incidencia")


class CrearTicketArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Categoria
    priority: Prioridad = Field(description="P1 solo si un servicio crítico está caído para varios")
    title: str = Field(min_length=5, max_length=200)
    steps: str = Field(min_length=5, max_length=4000, description="Qué pasa y qué se probó")


class ComentarioArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket_id: str = Field(pattern=r"^[0-9a-f\-]{36}$")
    comment: str = Field(min_length=1, max_length=2000)


def ticket_mode(args: CrearTicketArgs) -> Mode:
    return "approve_staff" if args.priority == "P1" else "confirm_user"


class HerramientasSoporte:
    def __init__(self, tickets: SqlRepositorioTickets, rag: HerramientasRag) -> None:
        self._tickets, self._rag = tickets, rag

    def search_it_kb(self, args: BuscarDocumentosArgs, ctx: ContextoTool) -> dict[str, Any]:
        return self._rag.search_public_internal(args, ctx)

    def search_my_tickets(self, args: BuscarTicketsArgs, ctx: ContextoTool) -> dict[str, Any]:
        return {
            "tickets_abiertos_parecidos": [
                {"id": k.id, "title": k.title, "priority": k.priority, "status": k.status}
                for k in self._tickets.parecidos_abiertos(ctx.user, args.consulta)
            ]
        }

    def create_ticket(self, args: CrearTicketArgs, ctx: ContextoTool) -> dict[str, Any]:
        k = self._tickets.crear(
            ctx.user, args.category, args.priority, args.title, args.steps, ctx.trace_id, ctx.agent
        )
        return {"id": k.id, "priority": k.priority, "status": k.status}

    def add_ticket_comment(self, args: ComentarioArgs, ctx: ContextoTool) -> dict[str, Any]:
        if not self._tickets.comentar(ctx.user, args.ticket_id, args.comment):
            return {"error": "No se pudo comentar: el ticket no existe, no es tuyo o está cerrado."}
        return {"id": args.ticket_id, "comentario": "añadido"}


def crear_support_agent(h: HerramientasSoporte) -> AgentSpec:
    return AgentSpec(
        name="support_agent",
        description=(
            "Incidencias técnicas: equipos, accesos, software, red. Busca solución en la base "
            "de conocimiento y, si no se resuelve, crea o actualiza un ticket."
        ),
        system_prompt=SUPPORT_PROMPT,
        tools={
            "search_it_kb": ToolPolicy(
                h.search_it_kb, BuscarDocumentosArgs, scope="docs:read", mode="auto",
                description="Busca soluciones en la base de conocimiento de TI.",
            ),
            "search_my_tickets": ToolPolicy(
                h.search_my_tickets, BuscarTicketsArgs, scope="ticket:read_own", mode="auto",
                description="Tickets propios abiertos parecidos (para no duplicar).",
            ),
            "create_ticket": ToolPolicy(
                h.create_ticket, CrearTicketArgs, scope="ticket:create", mode="confirm_user",
                writes=True, mode_if=ticket_mode, approver_role="it_support",
                description="Crea un ticket con categoría, prioridad y pasos realizados.",
            ),
            "add_ticket_comment": ToolPolicy(
                h.add_ticket_comment, ComentarioArgs, scope="ticket:update_own",
                mode="confirm_user", writes=True,
                description="Añade información a un ticket propio abierto.",
            ),
        },
    )  # fmt: skip
