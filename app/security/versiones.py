"""Versiones de guardrails seleccionables.

El catálogo permite iterar sobre los guardrails sin tocar el grafo: cada versión es una
implementación de `Guardrail` con nombre. En LangGraph Studio se eligen por ejecución desde el
contexto del grafo (`ContextoAgente`); la API de la app nunca acepta ese contexto y usa siempre
la versión configurada (GUARDRAIL_ENTRADA / GUARDRAIL_SALIDA), para que un usuario no pueda
desactivar un guardrail.

Para añadir una versión: implementa la clase, regístrala en `catalogo_entrada` o
`catalogo_salida` y añade su nombre al Literal correspondiente.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.graph.prompts import SUPERVISOR_PROMPT
from app.rag.prompts import SYSTEM_PROMPT
from app.security.content_safety import GuardrailPromptShields
from app.security.guardrails import Guardrail, GuardrailEntrada, GuardrailPermisivo, GuardrailSalida

VersionEntrada = Literal["v1-heuristico", "v2-prompt-shields", "sin-guardrail"]
VersionSalida = Literal["v1-fuga-prompt", "sin-guardrail"]

DESCRIPCION_ENTRADA = (
    "v1-heuristico: inyección, texto oculto y PII con reglas locales · "
    "v2-prompt-shields: v1 + Azure AI Content Safety · "
    "sin-guardrail: desactivado (solo fuera de producción, para comparar)"
)
DESCRIPCION_SALIDA = (
    "v1-fuga-prompt: fugas del prompt de sistema, etiquetas y PII · "
    "sin-guardrail: desactivado (solo fuera de producción, para comparar)"
)


class ContextoAgente(BaseModel):
    """Contexto de ejecución del grafo (LangGraph `context_schema`). Vacío = configuración."""

    model_config = ConfigDict(extra="forbid")

    guardrail_entrada: VersionEntrada | None = Field(
        default=None, description=f"Versión del guardrail de entrada. {DESCRIPCION_ENTRADA}"
    )
    guardrail_salida: VersionSalida | None = Field(
        default=None, description=f"Versión del guardrail de salida. {DESCRIPCION_SALIDA}"
    )


def catalogo_entrada(settings: Settings, shields: Any | None) -> dict[str, Guardrail]:
    catalogo: dict[str, Guardrail] = {"v1-heuristico": GuardrailEntrada()}
    if shields is not None:
        catalogo["v2-prompt-shields"] = GuardrailPromptShields(
            GuardrailEntrada(), shields, settings.content_safety_fallo
        )
    if settings.entorno != "prod":
        catalogo["sin-guardrail"] = GuardrailPermisivo()
    return catalogo


def catalogo_salida(settings: Settings) -> dict[str, Guardrail]:
    catalogo: dict[str, Guardrail] = {
        "v1-fuga-prompt": GuardrailSalida([SYSTEM_PROMPT, SUPERVISOR_PROMPT])
    }
    if settings.entorno != "prod":
        catalogo["sin-guardrail"] = GuardrailPermisivo()
    return catalogo


def version_por_defecto(settings: Settings, shields: Any | None) -> str:
    """GUARDRAIL_ENTRADA=auto: Prompt Shields si está configurado; si no, heurístico."""
    if settings.guardrail_entrada != "auto":
        return settings.guardrail_entrada
    return "v2-prompt-shields" if shields is not None else "v1-heuristico"
