"""Aprobaciones de los agentes (human-in-the-loop): la cola de cada persona y la decisión.

Quien decide sale SIEMPRE del token; el cuerpo solo lleva la decisión. confirm_user lo
resuelve el solicitante; approve_staff (P1) y escalate_human, alguien con el rol pedido que no
sea el solicitante.
"""

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencias import ServiciosDep, UsuarioDep
from app.servicios.conversaciones import AprobacionVista, ResultadoDecision

router = APIRouter(prefix="/aprobaciones", tags=["aprobaciones"])


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")  # nada de approver_id en el cuerpo
    aprobar: bool
    motivo: str | None = Field(default=None, max_length=500)
    edited_args: dict | None = None
    respuesta: str | None = Field(default=None, max_length=4000)  # escalados


@router.get("", response_model=list[AprobacionVista])
def pendientes(usuario: UsuarioDep, servicios: ServiciosDep) -> list[AprobacionVista]:
    return servicios.conversaciones.aprobaciones_pendientes(usuario)


@router.post("/{aprobacion_id}/decision", response_model=ResultadoDecision)
def decidir(
    aprobacion_id: str, body: Decision, usuario: UsuarioDep, servicios: ServiciosDep
) -> ResultadoDecision:
    return servicios.conversaciones.decidir(
        usuario, aprobacion_id, body.aprobar, motivo=body.motivo,
        edited_args=body.edited_args, respuesta=body.respuesta,
    )  # fmt: skip
