"""Dobles de prueba deterministas (sin llamadas a Azure)."""

import hashlib
import math
import re

from app.models.schemas import RespuestaLLM

DIM = 64


class FakeEmbedder:
    """Bolsa de palabras con hashing: textos que comparten palabras quedan cerca."""

    def embed(self, textos: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in textos]

    @staticmethod
    def _vector(texto: str) -> list[float]:
        v = [0.0] * DIM
        for token in re.findall(r"\w+", texto.lower()):
            v[int(hashlib.md5(token.encode()).hexdigest(), 16) % DIM] += 1.0  # noqa: S324
        norma = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norma for x in v]


class FakeLLM:
    """Registra la llamada y devuelve una respuesta configurable."""

    def __init__(self, salida: RespuestaLLM | None = None) -> None:
        self.salida = salida or RespuestaLLM(
            respuesta="Según la política, son 23 días [1].", citas_usadas=[1], encontrado=True
        )
        self.llamadas: list[tuple[str, str]] = []

    def responder(self, system: str, user: str) -> RespuestaLLM:
        self.llamadas.append((system, user))
        return self.salida
