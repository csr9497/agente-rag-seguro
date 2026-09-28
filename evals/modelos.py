"""Modelos Pydantic de las evaluaciones."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Capa = Literal["contrato", "deterministas", "recuperacion", "seguridad", "juez"]


class JuicioRespuesta(BaseModel):
    """Salida estructurada del juez (LLM)."""

    model_config = ConfigDict(extra="forbid")

    fidelidad: int = Field(
        ge=1, le=5, description="Cada afirmación está respaldada por el contexto"
    )
    relevancia: int = Field(ge=1, le=5, description="Responde a lo que se preguntó")
    completitud: int = Field(ge=1, le=5, description="Cubre lo que el contexto permite responder")
    afirmaciones_sin_soporte: list[str] = Field(description="Frases no respaldadas por el contexto")
    razonamiento: str = Field(max_length=1200)


class Metrica(BaseModel):
    capa: Capa
    nombre: str
    valor: float
    detalle: str = ""


class ResultadoEvaluacion(BaseModel):
    escenario_id: str
    rol: str
    capacidades: list[str]
    metricas: list[Metrica]
    fallos: list[str] = Field(default_factory=list)

    def valor(self, nombre: str) -> float | None:
        return next((m.valor for m in self.metricas if m.nombre == nombre), None)


class Umbral(BaseModel):
    metrica: str
    minimo: float
    agregacion: Literal["media", "minimo"] = "media"
    bloqueante: bool = True


class ResultadoUmbral(BaseModel):
    umbral: Umbral
    valor: float | None
    aprobado: bool
    muestras: int


class InformeEvaluacion(BaseModel):
    destino: str
    resultados: list[ResultadoEvaluacion]
    umbrales: list[ResultadoUmbral]

    @property
    def aprobado(self) -> bool:
        return all(u.aprobado for u in self.umbrales if u.umbral.bloqueante)
