"""Esquemas Pydantic v2 de todo el I/O del asistente."""

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
