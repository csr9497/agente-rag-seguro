"""Esquemas Pydantic v2 de todo el I/O del asistente."""

from typing import Any, Literal

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


class Aclaracion(BaseModel):
    """Pregunta al usuario cuando su mensaje no permite una consulta precisa."""

    pregunta: str
    opciones: list[str] = Field(default_factory=list)


class RespuestaConsulta(BaseModel):
    respuesta: str
    citas: list[Cita]
    sin_contexto: bool = Field(
        description="True si la respuesta no se pudo fundamentar en documentos visibles"
    )
    conversacional: bool = Field(
        default=False, description="Respuesta de cortesía por plantilla (saludo, ayuda…)"
    )
    aclaracion: Aclaracion | None = Field(
        default=None, description="Si se pide al usuario que concrete (sin buscar)"
    )


class Chunk(BaseModel):
    """Unidad indexada. `acl_groups` es la ACL que aplica el security trimming."""

    chunk_id: str
    doc_id: str
    fuente: str
    contenido: str
    acl_groups: list[str] = Field(min_length=1)
    doc_hash: str = Field(default="", description="SHA-256 del texto saneado del documento")
    indexado_en: str = Field(default="", description="Fecha ISO-8601 (UTC) de indexación")


def indice_chunk(chunk_id: str) -> int:
    """Posición del chunk dentro de su documento (`<doc_id>#<n>`)."""
    return int(chunk_id.rsplit("#", 1)[1])


class Turno(BaseModel):
    """Pregunta y respuesta previas de la misma conversación (mismo rol)."""

    pregunta: str
    respuesta: str


class ChunkRecuperado(BaseModel):
    chunk: Chunk
    score: float


class RespuestaLLM(BaseModel):
    """Salida estructurada que exigimos al LLM (structured outputs)."""

    model_config = ConfigDict(extra="forbid")

    respuesta: str = Field(
        description="Respuesta con la marca [n] del fragmento tras cada afirmación, p. ej. "
        "'Son 23 días [1].'"
    )
    citas_usadas: list[int] = Field(description="Números [n] de los fragmentos citados")
    encontrado: bool = Field(description="False si la respuesta no está en el contexto")


class Usuario(BaseModel):
    id: str
    groups: list[str]


class Hallazgo(BaseModel):
    """Resultado de un guardrail sobre la entrada o la salida."""

    tipo: Literal[
        "inyeccion",
        "texto_oculto",
        "pii",
        "fuga_prompt",
        "etiqueta_estructural",
        "acceso_no_autorizado",
        "servicio_no_disponible",
    ]
    detalle: str
    accion: Literal["bloquear", "enmascarar", "eliminar", "registrar"]


class RegistroAuditoria(BaseModel):
    fecha: str = Field(description="UTC, ISO 8601")
    traza_id: str | None = Field(default=None, description="run_id en LangSmith (correlación)")
    conversacion_id: str | None = None
    usuario: str
    grupos: list[str]
    pregunta: str = Field(description="Tal como la procesó el agente (PII enmascarada)")
    fuentes: list[str]
    respuesta: str
    sin_contexto: bool
    documentos_consultados: list[str] = Field(default_factory=list)
    desde_cache: bool = False
    hallazgos: list[Hallazgo] = Field(default_factory=list)


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


class DocumentoIndexado(BaseModel):
    """Vista agregada de un documento en el índice."""

    doc_id: str
    acl_groups: list[str]
    doc_hash: str
    chunks: int
    indexado_en: str


EstadoOperacion = Literal[
    "indexado", "actualizado", "sin_cambios", "duplicado", "rechazado", "eliminado", "no_encontrado"
]


class ResultadoOperacion(BaseModel):
    doc_id: str
    estado: EstadoOperacion
    chunks: int = 0
    motivos: list[str] = Field(default_factory=list)
    avisos: list[str] = Field(default_factory=list)
    duplicado_de: str | None = None
