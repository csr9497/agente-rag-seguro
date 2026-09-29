from typing import Any

from pydantic import BaseModel, Field

from app.models.schemas import (
    ChunkRecuperado,
    Hallazgo,
    RespuestaConsulta,
    ToolCall,
    Turno,
    Usuario,
)
from app.tools.base import ResultadoHerramienta


class ResultadoLlamada(BaseModel):
    tool_call_id: str
    resultado: ResultadoHerramienta


class EstadoAgente(BaseModel):
    """Estado del grafo. Cada nodo devuelve solo los campos que actualiza."""

    pregunta: str  # tras input_guardrail: versión saneada (PII enmascarada)
    usuario: Usuario
    top_k: int
    # Turnos previos de la misma conversación (mismo rol); solo para resolver referencias.
    historial: list[Turno] = Field(default_factory=list)

    # Historial de tool-calling del supervisor (formato chat de OpenAI).
    mensajes: list[dict[str, Any]] = Field(default_factory=list)
    pendientes: list[ToolCall] = Field(default_factory=list)
    # Resultados de tools pendientes de access_guardrail (aún no visibles para el supervisor).
    por_revisar: list[ResultadoLlamada] = Field(default_factory=list)
    iteraciones: int = 0

    # Contexto acumulado de todas las herramientas; su orden define la numeración [n].
    recuperados: list[ChunkRecuperado] = Field(default_factory=list)

    # Fragmentos devueltos por las tools que access_guardrail descartó (solo se cuentan).
    fragmentos_descartados: int = 0

    # Hallazgos de los guardrails de entrada y salida (van a la auditoría).
    hallazgos: list[Hallazgo] = Field(default_factory=list)

    # Caché semántica: embedding de la pregunta y, si hubo acierto, lo recuperado de la caché.
    vector_pregunta: list[float] | None = None
    desde_cache: bool = False
    documentos_cache: list[str] = Field(default_factory=list)

    # Se fija al generar o al cortar el flujo (sin permisos, guardrail).
    respuesta: RespuestaConsulta | None = None
