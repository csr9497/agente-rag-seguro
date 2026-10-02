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
        if "<catalogo>" in user:  # orientación sin información: repite el catálogo recibido
            catalogo = user.split("<catalogo>")[1].split("</catalogo>")[0].strip()
            return RespuestaLLM(
                respuesta=f"No tengo esa información. Puedo ayudarte con:\n{catalogo}",
                citas_usadas=[],
                encontrado=False,
            )
        return self.salida


def sin_fragmentos(llm: FakeLLM) -> bool:
    """Ningún fragmento de documento llegó al LLM: como mucho, la orientación sin información
    (catálogo del rol, motivo y pregunta; app/rag/orientacion.py)."""
    return all("<fragmento" not in user for _, user in llm.llamadas)


class FakeSupervisor:
    """Supervisor con guion: `turnos[i]` son las llamadas (nombre, argumentos JSON) de la
    iteración i. Por defecto, una búsqueda con la pregunta literal y después termina."""

    def __init__(self, turnos: list[list[tuple[str, str]]] | None = None) -> None:
        self.turnos = turnos
        self.llamadas: list[list[dict[str, Any]]] = []
        self.obligaciones: list[bool] = []

    def decidir(
        self,
        mensajes: list[dict[str, Any]],
        herramientas: list[dict[str, Any]],
        obligar_herramienta: bool = False,
    ) -> DecisionSupervisor:
        self.llamadas.append(mensajes)
        self.obligaciones.append(obligar_herramienta)
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


class GuionLLM:
    """LLM con tool-calling por guion: turnos[i] son las llamadas de la iteración i; al acabar
    responde `final` sin herramientas."""

    def __init__(self, turnos: list[list[tuple[str, dict | str]]], final: str = "Hecho.") -> None:
        self.turnos, self.final = turnos, final
        self.llamadas: list[list[dict[str, Any]]] = []
        self.obligaciones: list[bool] = []

    def decidir(self, mensajes, herramientas, obligar_herramienta=False) -> DecisionSupervisor:  # noqa: ANN001
        self.llamadas.append([dict(m) for m in mensajes])
        self.obligaciones.append(obligar_herramienta)
        self.herramientas = herramientas
        i = len(self.llamadas) - 1
        if i >= len(self.turnos):
            return DecisionSupervisor(
                tool_calls=[], mensaje_asistente={"role": "assistant", "content": self.final}
            )
        calls = [
            ToolCall(id=f"c{i}{j}", nombre=n, argumentos=a if isinstance(a, str) else json.dumps(a))
            for j, (n, a) in enumerate(self.turnos[i])
        ]
        return DecisionSupervisor(
            tool_calls=calls,
            mensaje_asistente={
                "role": "assistant", "content": None,
                "tool_calls": [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.nombre, "arguments": c.argumentos}}
                    for c in calls
                ],
            },
        )  # fmt: skip


class RagEco(GuionLLM):
    """Supervisor y rag_agent a la vez, sin guion fijo: el supervisor delega la pregunta en
    rag_agent; el agente busca y responde citando el primer documento que le devolvió la
    búsqueda (o dice que no lo encuentra). Sirve para varias consultas seguidas."""

    _DOC = re.compile(r'"doc_id": "([^"]+)"')

    def __init__(self) -> None:
        super().__init__([])
        self.vistos: list[dict[str, Any]] = []  # todo lo que recibieron supervisor y agente

    def decidir(self, mensajes, herramientas, obligar_herramienta=False) -> DecisionSupervisor:  # noqa: ANN001
        self.vistos += [dict(m) for m in mensajes]
        nombres = {h["function"]["name"] for h in herramientas}
        pregunta = next(m["content"] for m in mensajes if m["role"] == "user")
        tool = [m["content"] for m in mensajes if m["role"] == "tool"]
        if "delegar_rag_agent" in nombres:
            tarea = re.sub(r"(?s).*<pregunta>\s*(.*?)\s*</pregunta>.*", r"\1", pregunta)
            self.turnos = [[("delegar_rag_agent", {"tarea": tarea})]]
        elif not tool:
            self.turnos = [[("search_documents", {"consulta": pregunta})]]
        else:
            self.turnos = []
            docs = self._DOC.findall(tool[-1])
            self.final = f"Según el documento [{docs[0]}]." if docs else "No lo encuentro."
        self.llamadas = []
        return super().decidir(mensajes, herramientas, obligar_herramienta)
