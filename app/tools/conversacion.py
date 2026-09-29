"""Conversación básica: saludos, agradecimientos, despedidas y «¿qué puedes hacer?».

Responde con plantillas fijas, nunca con texto libre del modelo: así un saludo no dispara una
búsqueda ni el asistente contesta con conocimiento propio fuera de los documentos (regla 1).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.models.schemas import Usuario
from app.tools.base import ResultadoHerramienta

TipoConversacion = Literal["saludo", "agradecimiento", "despedida", "ayuda"]

PLANTILLAS: dict[str, str] = {
    "saludo": (
        "¡Hola! Soy el asistente de documentación interna. Pregúntame lo que necesites sobre "
        "los documentos de tu rol y te responderé indicando de qué documento sale cada dato. "
        "Por ejemplo: «¿Cuántos días de vacaciones tengo?» o «¿Qué documentos puedo consultar?»."
    ),
    "agradecimiento": "¡De nada! Si tienes otra pregunta sobre los documentos, aquí estoy.",
    "despedida": "¡Hasta luego! Tus conversaciones quedan guardadas en «Tus conversaciones».",
    "ayuda": (
        "Puedo responder preguntas sobre los documentos visibles para tu rol, siempre citando "
        "la fuente; decirte qué documentos puedes consultar; consultar datos internos como los "
        "festivos; y preparar acciones (abrir un ticket, solicitar vacaciones) que tú apruebas "
        "antes de que se ejecuten."
    ),
}


class ConversacionArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tipo: TipoConversacion


class ResponderConversacion:
    nombre = "conversacion"
    descripcion = (
        "Respuesta de cortesía para saludos, agradecimientos, despedidas o preguntas sobre qué "
        "puede hacer el asistente. NUNCA para preguntas sobre contenido de documentos o datos."
    )
    args_model = ConversacionArgs

    def ejecutar(
        self, args: ConversacionArgs, usuario: Usuario, top_k: int
    ) -> ResultadoHerramienta:
        return ResultadoHerramienta(
            conversacion=args.tipo, nota="Respuesta de cortesía preparada. Contesta LISTO."
        )
