"""Conversaciones con permisos de un rol. El historial muestra qué documentos se consultaron."""

import logging
from typing import Literal

import openai
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencias import ServiciosDep, UsuarioDep
from app.persistencia.modelos import (
    Conversacion,
    Feedback,
    MensajeGuardado,
    ResumenConversacion,
)
from app.retrieval.no_configurado import ProveedorNoConfiguradoError

router = APIRouter(prefix="/conversaciones", tags=["conversaciones"])
logger = logging.getLogger(__name__)


class NuevaConversacion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rol_id: str


class NuevaPregunta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pregunta: str = Field(min_length=1, max_length=2000)


@router.post("", response_model=Conversacion, status_code=status.HTTP_201_CREATED)
def iniciar(body: NuevaConversacion, usuario: UsuarioDep, servicios: ServiciosDep) -> Conversacion:
    return servicios.conversaciones.iniciar(usuario, body.rol_id)


@router.get("", response_model=list[ResumenConversacion])
def historial(
    rol_id: str, usuario: UsuarioDep, servicios: ServiciosDep
) -> list[ResumenConversacion]:
    """Mis conversaciones con este rol (las más recientes primero)."""
    return servicios.conversaciones.listar(usuario, rol_id)


@router.get("/{conversacion_id}", response_model=Conversacion)
def obtener(conversacion_id: str, usuario: UsuarioDep, servicios: ServiciosDep) -> Conversacion:
    return servicios.conversaciones.obtener(usuario, conversacion_id)


@router.post("/{conversacion_id}/mensajes", response_model=MensajeGuardado)
def preguntar(
    conversacion_id: str, body: NuevaPregunta, usuario: UsuarioDep, servicios: ServiciosDep
) -> MensajeGuardado:
    try:
        return servicios.conversaciones.preguntar(usuario, conversacion_id, body.pregunta)
    except ProveedorNoConfiguradoError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except openai.APIError as exc:
        logger.exception("Error del proveedor LLM")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Error del proveedor de IA") from exc


class NuevaValoracion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    valoracion: Literal["positiva", "negativa"]
    comentario: str | None = Field(default=None, max_length=500)


@router.post("/{conversacion_id}/mensajes/{mensaje_id}/feedback", response_model=MensajeGuardado)
def valorar(
    conversacion_id: str,
    mensaje_id: int,
    body: NuevaValoracion,
    usuario: UsuarioDep,
    servicios: ServiciosDep,
) -> MensajeGuardado:
    """Valoración 👍/👎 de una respuesta: se guarda en el historial y va a LangSmith."""
    return servicios.conversaciones.valorar(
        usuario, conversacion_id, mensaje_id, Feedback(**body.model_dump())
    )
