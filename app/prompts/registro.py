"""Registro de prompts: versión «local» (repositorio) o una etiqueta/commit de LangSmith.

- PROMPTS_ORIGEN=local (por defecto): siempre los ficheros del repositorio.
- PROMPTS_ORIGEN=langsmith: la etiqueta PROMPTS_ETIQUETA (p. ej. «prod») de cada prompt.
- En Studio, el contexto puede pedir otra versión por ejecución («dev», un hash de commit…).

Si LangSmith no responde o la versión no existe, se usa la copia local y se registra un aviso:
la app nunca se queda sin prompt. Cada texto cargado se notifica a los guardrails de salida
para que también detecten fugas de esa variante.
"""

import logging
import threading
from collections.abc import Callable
from typing import Any

from app.config import Settings
from app.prompts import PROMPTS, local

logger = logging.getLogger(__name__)


class RegistroPrompts:
    def __init__(self, settings: Settings, cliente: Any | None) -> None:
        self._settings = settings
        self._cliente = cliente
        self._cache: dict[tuple[str, str], str] = {}
        self._cerrojo = threading.Lock()
        self._al_cargar: list[Callable[[str], None]] = []

    def al_cargar(self, funcion: Callable[[str], None]) -> None:
        self._al_cargar.append(funcion)

    def version_por_defecto(self) -> str:
        return (
            "local" if self._settings.prompts_origen == "local" else self._settings.prompts_etiqueta
        )

    def texto(self, nombre: str, version: str | None = None) -> tuple[str, str]:
        """(texto, versión usada). «local (fallback)» si no se pudo cargar de LangSmith."""
        version = version or self.version_por_defecto()
        if version == "local":
            return local(nombre), "local"
        if self._cliente is None:
            logger.warning(
                "Prompt %s:%s pedido sin cliente de LangSmith: se usa el local", nombre, version
            )
            return local(nombre), "local (fallback)"
        clave = (nombre, version)
        with self._cerrojo:
            if clave in self._cache:
                return self._cache[clave], version
        try:
            plantilla = self._cliente.pull_prompt(f"{PROMPTS[nombre][0]}:{version}")
            texto = plantilla.messages[0].prompt.template
        except Exception as exc:  # noqa: BLE001 — sin LangSmith la app sigue con el local
            logger.warning("No se pudo cargar %s:%s de LangSmith (%s): se usa el local",
                           nombre, version, type(exc).__name__)  # fmt: skip
            return local(nombre), "local (fallback)"
        with self._cerrojo:
            self._cache[clave] = texto
        for funcion in self._al_cargar:
            funcion(texto)
        return texto, version
