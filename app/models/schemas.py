"""Esquemas Pydantic v2 de todo el I/O del asistente."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConsultaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pregunta: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=20)


class Cita(BaseModel):
    numero: int = Field(ge=1, description="Índice [n] usado en la respuesta")
    doc_id: str
    chunk_id: str
    fuente: str
    fragmento: str
    score: float


class RespuestaConsulta(BaseModel):
    respuesta: str
    citas: list[Cita]
    sin_contexto: bool = Field(
        description="True si la respuesta no se pudo fundamentar en documentos visibles"
    )


class Chunk(BaseModel):
    """Unidad indexada. `acl_groups` es la ACL que aplica el security trimming."""

    chunk_id: str
    doc_id: str
    fuente: str
    contenido: str
    acl_groups: list[str] = Field(min_length=1)


class ChunkRecuperado(BaseModel):
    chunk: Chunk
    score: float


class RespuestaLLM(BaseModel):
    """Salida estructurada que exigimos al LLM (structured outputs)."""

    model_config = ConfigDict(extra="forbid")

    respuesta: str
    citas_usadas: list[int] = Field(description="Números [n] de los fragmentos citados")
    encontrado: bool = Field(description="False si la respuesta no está en el contexto")


class Usuario(BaseModel):
    id: str
    groups: list[str]


class RegistroAuditoria(BaseModel):
    usuario: str
    grupos: list[str]
    pregunta: str
    fuentes: list[str]
    respuesta: str
    sin_contexto: bool


class ToolCall(BaseModel):
    """Llamada a herramienta decidida por el supervisor."""

    id: str
    nombre: str
    argumentos: str = Field(description="JSON crudo; lo valida el args_model de la herramienta")


class DecisionSupervisor(BaseModel):
    tool_calls: list[ToolCall]
    mensaje_asistente: dict[str, Any] = Field(
        description="Mensaje del asistente tal cual, para mantener el historial de tool-calling"
    )
