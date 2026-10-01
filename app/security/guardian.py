"""Guardián LLM (guardrail v5-llm-politicas): un modelo clasifica cada mensaje según la
política de uso escrita en lenguaje natural (app/prompts/guardian.md, versionada en
LangSmith). Entiende paráfrasis, jerga, faltas de ortografía y otros idiomas, que las reglas
en código no cubren. Se ejecuta en paralelo con la caché semántica.

Si el guardián falla, la consulta sigue (las reglas en código ya se aplicaron) y queda un
hallazgo «servicio_no_disponible» en la auditoría; si el filtro de contenido del proveedor
rechaza el mensaje, se bloquea.
"""

from typing import Literal

import openai
from openai import AzureOpenAI
from pydantic import BaseModel, ConfigDict, Field

from app.rag.prompts import neutralizar

Categoria = Literal[
    "ninguna",
    "dano_a_personas",
    "autolesion",
    "acoso",
    "dato_sensible",
    "manipulacion",
    "ilicito",
    "fuera_de_ambito",
]


class VeredictoGuardian(BaseModel):
    """Salida estructurada del guardián."""

    model_config = ConfigDict(extra="forbid")

    categoria: Categoria
    severidad: Literal["ninguna", "baja", "media", "alta"]
    accion: Literal["permitir", "bloquear", "redirigir"]
    motivo: str = Field(max_length=300)


class GuardianLLM:
    def __init__(self, client: AzureOpenAI, deployment: str) -> None:
        self._client = client
        self.deployment = deployment

    def clasificar(self, politica: str, mensaje: str) -> VeredictoGuardian:
        completion = self._client.chat.completions.parse(
            model=self.deployment,
            messages=[
                {"role": "system", "content": politica},
                {"role": "user", "content": f"<mensaje>\n{neutralizar(mensaje)}\n</mensaje>"},
            ],
            response_format=VeredictoGuardian,
            temperature=0,
        )
        veredicto = completion.choices[0].message.parsed
        if veredicto is None:  # el modelo se negó a clasificar: se trata como sospechoso
            return VeredictoGuardian(
                categoria="manipulacion", severidad="media", accion="bloquear",
                motivo="El clasificador no devolvió un veredicto válido.",
            )  # fmt: skip
        return veredicto


def es_filtro_de_contenido(exc: Exception) -> bool:
    return isinstance(exc, openai.BadRequestError) and (
        getattr(exc, "code", None) == "content_filter" or "content_filter" in str(exc)
    )
