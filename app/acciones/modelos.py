"""Acciones con efecto (action_tool). El agente solo propone; ejecutar exige aprobación humana."""

from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

TipoAccion = Literal["abrir_ticket", "solicitar_vacaciones"]
EstadoAccion = Literal["pendiente", "ejecutada", "rechazada", "error"]


class DatosTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asunto: str = Field(min_length=5, max_length=120)
    descripcion: str = Field(min_length=1, max_length=1000)
    prioridad: Literal["baja", "media", "alta"] = "media"


class DatosVacaciones(BaseModel):
    model_config = ConfigDict(extra="forbid")
    desde: date
    hasta: date
    comentario: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _rango(self) -> Self:
        if self.hasta < self.desde:
            raise ValueError("'hasta' no puede ser anterior a 'desde'")
        if (self.hasta - self.desde).days > 30:
            raise ValueError("una solicitud no puede superar 30 días")
        return self


CATALOGO: dict[TipoAccion, tuple[type[BaseModel], tuple[str, ...] | None, str]] = {
    # tipo: (modelo de datos, roles autorizados (None = todos), descripción)
    "abrir_ticket": (DatosTicket, None, "Abrir un ticket de soporte a IT"),
    "solicitar_vacaciones": (
        DatosVacaciones,
        ("public", "rrhh"),
        "Registrar una solicitud de vacaciones para su aprobación por el responsable",
    ),
}


class PropuestaAccion(BaseModel):
    id: str
    tipo: TipoAccion
    rol_id: str
    usuario: str
    datos: dict
    estado: EstadoAccion = "pendiente"
    resultado: str | None = None
    creada_en: str = ""
    decidida_en: str | None = None

    @property
    def resumen(self) -> str:
        if self.tipo == "abrir_ticket":
            return f"Ticket «{self.datos['asunto']}» (prioridad {self.datos['prioridad']})"
        return f"Vacaciones del {self.datos['desde']} al {self.datos['hasta']}"
