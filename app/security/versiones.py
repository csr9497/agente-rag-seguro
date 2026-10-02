"""Versiones de guardrails seleccionables.

El catálogo permite iterar sobre los guardrails sin tocar el grafo: cada versión es una
implementación de `Guardrail` con nombre. La app usa siempre la versión configurada
(GUARDRAIL_ENTRADA / GUARDRAIL_SALIDA); ninguna petición puede elegirla, para que un usuario no
pueda desactivar un guardrail.

Para añadir una versión: implementa la clase, regístrala en `catalogo_entrada` o
`catalogo_salida` y añade su nombre al Literal correspondiente.
"""

from typing import Any, Literal

from app.config import Settings
from app.prompts import PROMPTS, local
from app.security.content_safety import GuardrailPromptShields
from app.security.guardrails import Guardrail, GuardrailEntrada, GuardrailPermisivo, GuardrailSalida
from app.security.politicas import GuardrailPoliticas, GuardrailSalidaSensibles

VersionEntrada = Literal[
    "v1-heuristico", "v2-prompt-shields", "v3-politicas", "v4-politicas-shields", "sin-guardrail"
]
VersionSalida = Literal["v1-fuga-prompt", "v2-fuga-sensibles", "sin-guardrail"]


def catalogo_entrada(settings: Settings, shields: Any | None) -> dict[str, Guardrail]:
    catalogo: dict[str, Guardrail] = {
        "v1-heuristico": GuardrailEntrada(),
        "v3-politicas": GuardrailPoliticas(),
    }
    if shields is not None:
        fallo = settings.content_safety_fallo
        catalogo["v2-prompt-shields"] = GuardrailPromptShields(GuardrailEntrada(), shields, fallo)
        catalogo["v4-politicas-shields"] = GuardrailPromptShields(
            GuardrailPoliticas(), shields, fallo
        )
    if settings.entorno != "prod":
        catalogo["sin-guardrail"] = GuardrailPermisivo()
    return catalogo


def catalogo_salida(settings: Settings) -> dict[str, Guardrail]:
    # Todos los prompts de sistema registrados (también los de cada agente): nunca se filtran.
    protegidos = [local(nombre) for nombre in PROMPTS]
    catalogo: dict[str, Guardrail] = {
        "v1-fuga-prompt": GuardrailSalida(protegidos),
        "v2-fuga-sensibles": GuardrailSalidaSensibles(protegidos),
    }
    if settings.entorno != "prod":
        catalogo["sin-guardrail"] = GuardrailPermisivo()
    return catalogo


def version_por_defecto(settings: Settings, shields: Any | None) -> str:
    """GUARDRAIL_ENTRADA=auto: políticas de uso (+ Prompt Shields si está configurado)."""
    if settings.guardrail_entrada != "auto":
        return settings.guardrail_entrada
    return "v4-politicas-shields" if shields is not None else "v3-politicas"
