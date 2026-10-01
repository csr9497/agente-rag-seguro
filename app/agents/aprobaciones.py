"""Aprobaciones humanas pendientes (interrupt de los agentes), en la tabla `approvals`.

- Se crea una al pausar una tool que necesita aprobación (policy_gate → needs_approval).
- Se resuelve una sola vez (aprobada / rechazada); una resuelta no se vuelve a decidir.
- Vencen a las 24 h: `vencer()` las cancela y notifica (lo ejecuta una tarea periódica).
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import Engine, and_, insert, or_, select, update

from app.persistencia import tablas as t

EstadoAprobacion = Literal["pendiente", "aprobada", "rechazada", "vencida"]


class Aprobacion(BaseModel):
    id: str
    thread_id: str
    trace_id: str
    agent: str
    tool: str
    user_id: str
    tipo: Literal["confirm_user", "approve_staff"]
    approver_role: str | None = None
    args_preview: str
    risk: str
    estado: EstadoAprobacion = "pendiente"
    approver_id: str | None = None
    motivo: str | None = None
    created_at: str
    expires_at: str
    decided_at: str | None = None


class SqlRepositorioAprobaciones:
    def __init__(
        self,
        motor: Engine,
        notificar: Callable[[Aprobacion], None] | None = None,
        ahora: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._motor = motor
        self._notificar = notificar or (lambda _: None)
        self._ahora = ahora

    def crear(self, **datos: str | None) -> Aprobacion:
        aprobacion = Aprobacion(id=str(uuid.uuid4()), created_at=self._ahora().isoformat(), **datos)
        with self._motor.begin() as c:
            c.execute(insert(t.approvals).values(**aprobacion.model_dump()))
        return aprobacion

    def obtener(self, aprobacion_id: str) -> Aprobacion | None:
        with self._motor.connect() as c:
            fila = (
                c.execute(select(t.approvals).where(t.approvals.c.id == aprobacion_id))
                .mappings()
                .first()
            )
        return Aprobacion(**fila) if fila else None

    def pendientes(
        self, roles: list[str] | None = None, user_id: str | None = None
    ) -> list[Aprobacion]:
        """Las que puede resolver alguien: approve_staff de sus roles y confirm_user propias."""
        condiciones = []
        if roles:
            condiciones.append(
                and_(t.approvals.c.tipo == "approve_staff", t.approvals.c.approver_role.in_(roles))
            )
        if user_id:
            condiciones.append(
                and_(t.approvals.c.tipo == "confirm_user", t.approvals.c.user_id == user_id)
            )
        if not condiciones:
            return []
        consulta = (
            select(t.approvals)
            .where(t.approvals.c.estado == "pendiente", or_(*condiciones))
            .order_by(t.approvals.c.created_at)
        )
        with self._motor.connect() as c:
            return [Aprobacion(**f) for f in c.execute(consulta).mappings()]

    def decidir(
        self, aprobacion_id: str, aprobada: bool, approver_id: str, motivo: str | None
    ) -> bool:
        """False si ya no estaba pendiente (resuelta o vencida): no se decide dos veces."""
        with self._motor.begin() as c:
            filas = c.execute(
                update(t.approvals)
                .where(t.approvals.c.id == aprobacion_id, t.approvals.c.estado == "pendiente")
                .values(
                    estado="aprobada" if aprobada else "rechazada",
                    approver_id=approver_id,
                    motivo=motivo,
                    decided_at=self._ahora().isoformat(),
                )  # fmt: skip
            ).rowcount
        return filas == 1

    def escalar(
        self, aprobacion_id: str, tipo: str, approver_role: str | None, args_preview: str
    ) -> None:
        """Los argumentos editados exigen más aprobación (p. ej. P3 → P1); solo si sigue
        pendiente."""
        with self._motor.begin() as c:
            c.execute(
                update(t.approvals)
                .where(t.approvals.c.id == aprobacion_id, t.approvals.c.estado == "pendiente")
                .values(tipo=tipo, approver_role=approver_role, args_preview=args_preview)
            )

    def vencer(self, ahora: datetime | None = None) -> list[Aprobacion]:
        momento = (ahora or self._ahora()).isoformat()
        with self._motor.begin() as c:
            vencidas = [
                Aprobacion(**f)
                for f in c.execute(
                    select(t.approvals).where(
                        t.approvals.c.estado == "pendiente", t.approvals.c.expires_at <= momento
                    )
                ).mappings()
            ]
            if vencidas:
                c.execute(
                    update(t.approvals)
                    .where(t.approvals.c.id.in_([a.id for a in vencidas]))
                    .values(estado="vencida", decided_at=momento, motivo="Vencida a las 24 h")
                )
        for a in vencidas:
            self._notificar(a)
        return vencidas
