from typing import Any

from pydantic import BaseModel, Field

from app.models.schemas import ChunkRecuperado, Hallazgo, RespuestaConsulta, ToolCall, Usuario


class EstadoAgente(BaseModel):
    """Estado del grafo. Cada nodo devuelve solo los campos que actualiza."""

    pregunta: str  # tras input_guardrail: versión saneada (PII enmascarada)
    usuario: Usuario
    top_k: int

    # Historial de tool-calling del supervisor (formato chat de OpenAI).
    mensajes: list[dict[str, Any]] = Field(default_factory=list)
    pendientes: list[ToolCall] = Field(default_factory=list)
    iteraciones: int = 0

    # Contexto acumulado de todas las herramientas; su orden define la numeración [n].
    recuperados: list[ChunkRecuperado] = Field(default_factory=list)

    # Hallazgos de los guardrails de entrada y salida (van a la auditoría).
    hallazgos: list[Hallazgo] = Field(default_factory=list)

    # Se fija al generar o al cortar el flujo (sin permisos, guardrail).
    respuesta: RespuestaConsulta | None = None
