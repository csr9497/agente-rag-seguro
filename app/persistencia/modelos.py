"""Modelos Pydantic de roles, documentos registrados y conversaciones."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models.schemas import Aclaracion, AprobacionPendiente, Cita, Hallazgo

PATRON_ROL = r"^[a-z0-9][a-z0-9_\-]{0,63}$"
Permiso = Literal["gestionar_documentos", "administrar_roles"]
EstadoDocumento = Literal["activo", "bloqueado", "pendiente"]


class Rol(BaseModel):
    id: str = Field(pattern=PATRON_ROL)
    nombre: str = Field(min_length=1, max_length=80)
    descripcion: str = Field(default="", max_length=300)
    activo: bool = True
    permisos: list[Permiso] = Field(default_factory=list)
    publica_para: list[str] = Field(
        default_factory=list, description="Roles a los que puede asignar documentos"
    )
    creado_en: str = ""

    @field_validator("permisos", "publica_para")
    @classmethod
    def _ordenado_sin_duplicados(cls, v: list[str]) -> list[str]:
        return sorted(set(v))

    def puede(self, permiso: Permiso) -> bool:
        return self.activo and permiso in self.permisos


class DocumentoRegistrado(BaseModel):
    """Fuente de verdad de los permisos de un documento (se contrasta con el índice).

    `roles` es la ACL completa, igual que `acl_groups` en el índice: roles, «dept:<d>» y
    «user:<id>» (app/security/acl.py)."""

    doc_id: str
    titulo: str
    roles: list[str] = Field(min_length=1)
    doc_hash: str
    chunks: int = Field(ge=0)
    estado: EstadoDocumento = "activo"
    motivo_estado: str | None = None
    subido_por: str | None = None
    indexado_en: str = ""
    revocado: bool = False
    expira_en: str | None = None  # ISO 8601 con zona

    @field_validator("roles")
    @classmethod
    def _roles_ordenados(cls, v: list[str]) -> list[str]:
        return sorted(set(v))


class SolicitudAcceso(BaseModel):
    id: str
    solicitante_id: str
    titulo: str
    motivo: str
    doc_id: str | None = None
    propietario_id: str | None = None
    estado: Literal["pendiente", "concedida", "denegada"] = "pendiente"
    trace_id: str | None = None
    creada_en: str = ""


class Feedback(BaseModel):
    valoracion: Literal["positiva", "negativa"]
    comentario: str | None = Field(default=None, max_length=500)
    creado_en: str = ""


class MensajeGuardado(BaseModel):
    id: int | None = None
    pregunta: str = Field(description="Tal como la procesó el agente (PII enmascarada)")
    respuesta: str
    sin_contexto: bool
    citas: list[Cita] = Field(default_factory=list)
    documentos_consultados: list[str] = Field(default_factory=list)
    fragmentos_descartados: int = 0
    hallazgos: list[Hallazgo] = Field(default_factory=list)
    traza_id: str | None = Field(default=None, description="run_id de la ejecución (LangSmith)")
    desde_cache: bool = False
    conversacional: bool = False
    aclaracion: Aclaracion | None = None
    consultas: list[str] = Field(default_factory=list, description="Consultas curadas enviadas")
    feedback: Feedback | None = None
    # Orquestador multiagente: pausas pendientes, hilo del checkpointer y agentes que actuaron.
    aprobaciones: list[AprobacionPendiente] = Field(default_factory=list)
    thread_id: str | None = None
    agentes: list[str] = Field(default_factory=list)
    creado_en: str = ""


class Conversacion(BaseModel):
    id: str
    rol_id: str
    creada_en: str
    usuario_id: str | None = Field(default=None, exclude=True)  # no se expone en la API
    mensajes: list[MensajeGuardado] = Field(default_factory=list)


class ResumenConversacion(BaseModel):
    """Entrada del historial: sin respuestas ni fragmentos."""

    id: str
    rol_id: str
    creada_en: str
    mensajes: int
    titulo: str = Field(description="Primera pregunta (PII enmascarada), recortada")
