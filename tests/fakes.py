"""Dobles de prueba deterministas (sin llamadas a Azure)."""

import hashlib
import json
import math
import re
from typing import Any

from app.models.schemas import DecisionSupervisor, Hallazgo, RespuestaLLM, ToolCall
from app.security.guardrails import Veredicto

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


class FakeSupervisor:
    """Supervisor con guion: `turnos[i]` son las llamadas (nombre, argumentos JSON) de la
    iteración i. Por defecto, una búsqueda con la pregunta literal y después termina."""

    def __init__(self, turnos: list[list[tuple[str, str]]] | None = None) -> None:
        self.turnos = turnos
        self.llamadas: list[list[dict[str, Any]]] = []

    def decidir(
        self, mensajes: list[dict[str, Any]], herramientas: list[dict[str, Any]]
    ) -> DecisionSupervisor:
        self.llamadas.append(mensajes)
        # Sin estado entre consultas (como un LLM real): el turno sale del historial.
        i = sum(m["role"] == "assistant" for m in mensajes)
        turnos = self.turnos
        if turnos is None:
            pregunta = next(m["content"] for m in mensajes if m["role"] == "user")
            turnos = [[("rag_retrieve", json.dumps({"consulta": pregunta}))]]
        llamadas = turnos[i] if i < len(turnos) else []
        tool_calls = [
            ToolCall(id=f"call_{i}_{j}", nombre=n, argumentos=a)
            for j, (n, a) in enumerate(llamadas)
        ]
        mensaje = {"role": "assistant", "content": None if tool_calls else "LISTO"}
        if tool_calls:
            mensaje["tool_calls"] = [
                {
                    "id": t.id,
                    "type": "function",
                    "function": {"name": t.nombre, "arguments": t.argumentos},
                }
                for t in tool_calls
            ]
        return DecisionSupervisor(tool_calls=tool_calls, mensaje_asistente=mensaje)


class GuardrailQueBloquea:
    def __init__(self, palabra: str) -> None:
        self.palabra = palabra

    def revisar(self, texto: str) -> Veredicto:
        if self.palabra in texto.lower():
            hallazgo = Hallazgo(tipo="inyeccion", detalle=self.palabra, accion="bloquear")
            return Veredicto(permitido=False, texto=texto, hallazgos=[hallazgo])
        return Veredicto(permitido=True, texto=texto)
