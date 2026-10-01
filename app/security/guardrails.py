"""Guardrails de entrada y salida con veredicto estructurado.

Cada guardrail devuelve un `Veredicto`: si el texto puede seguir, el texto resultante ya
saneado (PII enmascarada, etiquetas eliminadas) y la lista de hallazgos con la acción
aplicada. El grafo usa `texto` aguas abajo y registra los hallazgos en la auditoría.

Implementación local y determinista. En la fase de Azure se añade Content Safety (Prompt
Shields) detrás de esta misma interfaz.
"""

import re
from typing import Protocol

from pydantic import BaseModel, Field

from app.models.schemas import Hallazgo
from app.security.deteccion import (
    INVISIBLES,
    TIPOS_PII,
    TipoPII,
    detectar_inyeccion,
    enmascarar_pii,
)

MENSAJE_BLOQUEO = "No puedo procesar esta consulta porque infringe la política de uso."


class Veredicto(BaseModel):
    permitido: bool
    texto: str = Field(description="Texto a usar aguas abajo (saneado si procede)")
    hallazgos: list[Hallazgo] = Field(default_factory=list)
    mensaje: str | None = Field(
        default=None, description="Respuesta al usuario si se bloquea (si no, la genérica)"
    )

    @property
    def motivo(self) -> str | None:
        bloqueos = [h.detalle for h in self.hallazgos if h.accion == "bloquear"]
        return "; ".join(bloqueos) or None


class Guardrail(Protocol):
    def revisar(self, texto: str) -> Veredicto: ...


class GuardrailPermisivo:
    def revisar(self, texto: str) -> Veredicto:
        return Veredicto(permitido=True, texto=texto)


class GuardrailEntrada:
    """Pregunta del usuario: bloquea inyección y texto oculto; enmascara PII para que no
    llegue al LLM ni a la auditoría."""

    def __init__(self, pii: tuple[TipoPII, ...] = TIPOS_PII) -> None:
        self._pii = pii

    def revisar(self, texto: str) -> Veredicto:
        hallazgos: list[Hallazgo] = []
        if INVISIBLES.search(texto):
            hallazgos.append(
                Hallazgo(tipo="texto_oculto", detalle="caracteres invisibles", accion="bloquear")
            )
        for patron in detectar_inyeccion(texto):
            hallazgos.append(Hallazgo(tipo="inyeccion", detalle=patron, accion="bloquear"))
        if hallazgos:
            # Si se bloquea, no se propaga el texto original (tampoco a la auditoría).
            texto_seguro, _ = enmascarar_pii(INVISIBLES.sub("", texto), self._pii)
            return Veredicto(permitido=False, texto=texto_seguro, hallazgos=hallazgos)

        texto, tipos = enmascarar_pii(texto, self._pii)
        hallazgos += [Hallazgo(tipo="pii", detalle=t, accion="enmascarar") for t in tipos]
        return Veredicto(permitido=True, texto=texto, hallazgos=hallazgos)


_ETIQUETAS = re.compile(r"</?\s*(contexto|fragmento|pregunta)\b[^>]*>", re.IGNORECASE)


def _normalizar(texto: str) -> str:
    return re.sub(r"\s+", " ", texto).strip().lower()


class GuardrailSalida:
    """Respuesta del LLM: bloquea fugas de los prompts de sistema, enmascara PII sensible y
    elimina etiquetas estructurales del prompt."""

    def __init__(
        self,
        prompts_protegidos: list[str],
        pii: tuple[TipoPII, ...] = ("tarjeta", "iban", "dni_es"),
        min_huella: int = 40,
    ) -> None:
        # Huellas: líneas largas y distintivas de los prompts de sistema.
        self._min_huella = min_huella
        self._huellas: set[str] = set()
        for prompt in prompts_protegidos:
            self.proteger(prompt)
        self._pii = pii

    def proteger(self, prompt: str) -> None:
        """Añade un prompt a vigilar (p. ej. una versión cargada de LangSmith)."""
        self._huellas |= {
            _normalizar(linea)
            for linea in prompt.splitlines()
            if len(linea.strip()) >= self._min_huella
        }

    def revisar(self, texto: str) -> Veredicto:
        normalizado = _normalizar(texto)
        if any(h in normalizado for h in self._huellas):
            return Veredicto(
                permitido=False,
                texto=MENSAJE_BLOQUEO,
                hallazgos=[
                    Hallazgo(tipo="fuga_prompt", detalle="prompt de sistema", accion="bloquear")
                ],
            )
        hallazgos: list[Hallazgo] = []
        if _ETIQUETAS.search(texto):
            texto = _ETIQUETAS.sub("", texto)
            hallazgos.append(
                Hallazgo(tipo="etiqueta_estructural", detalle="etiquetas", accion="eliminar")
            )
        texto, tipos = enmascarar_pii(texto, self._pii)
        hallazgos += [Hallazgo(tipo="pii", detalle=t, accion="enmascarar") for t in tipos]
        return Veredicto(permitido=True, texto=texto, hallazgos=hallazgos)
