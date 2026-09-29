"""Acciones propuestas por el agente: el humano las aprueba o rechaza (X-Rol)."""

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.acciones.modelos import PropuestaAccion
from app.api.dependencias import Actor, ServiciosDep, UsuarioDep

router = APIRouter(prefix="/acciones", tags=["acciones"])


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    aprobar: bool


@router.get("", response_model=list[PropuestaAccion])
def listar(actor: Actor, usuario: UsuarioDep, servicios: ServiciosDep) -> list[PropuestaAccion]:
    return servicios.acciones.listar(actor.id, usuario.id)


@router.post("/{accion_id}/decision", response_model=PropuestaAccion)
def decidir(
    accion_id: str, body: Decision, actor: Actor, usuario: UsuarioDep, servicios: ServiciosDep
) -> PropuestaAccion:
    """Human-in-the-loop: solo aquí se ejecuta una acción, y solo por quien la propuso."""
    return servicios.acciones.decidir(accion_id, body.aprobar, actor.id, usuario.id)
