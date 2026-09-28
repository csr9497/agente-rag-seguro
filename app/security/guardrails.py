"""Guardrails de entrada y salida.

Fase 2: interfaz + implementación permisiva. Fase 3: Azure AI Content Safety (prompt shields)
y detección de PII detrás de esta misma interfaz.
"""

from typing import Protocol

from pydantic import BaseModel

MENSAJE_BLOQUEO = "No puedo procesar esta consulta porque infringe la política de uso."


class Veredicto(BaseModel):
    permitido: bool
    motivo: str | None = None


class Guardrail(Protocol):
    def revisar(self, texto: str) -> Veredicto: ...


class GuardrailPermisivo:
    def revisar(self, texto: str) -> Veredicto:
        return Veredicto(permitido=True)
