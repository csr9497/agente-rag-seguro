"""Conversación básica: saludos, agradecimientos, despedidas y «¿qué puedes hacer?» (el
supervisor del orquestador elige el tipo con su tool `conversacion`).

Responde con plantillas fijas, nunca con texto libre del modelo: así un saludo no dispara una
búsqueda ni el asistente contesta con conocimiento propio fuera de los documentos (regla 1).
"""

from typing import Literal

TipoConversacion = Literal["saludo", "agradecimiento", "despedida", "ayuda", "fuera_de_ambito"]

PLANTILLAS: dict[str, str] = {
    "saludo": (
        "¡Hola! Soy el asistente de documentación interna. Pregúntame lo que necesites sobre "
        "los documentos de tu rol y te responderé indicando de qué documento sale cada dato. "
        "Por ejemplo: «¿Cuántos días de vacaciones tengo?» o «¿Qué documentos puedo consultar?»."
    ),
    "agradecimiento": "¡De nada! Si tienes otra pregunta sobre los documentos, aquí estoy.",
    "despedida": "¡Hasta luego! Tus conversaciones quedan guardadas en «Tus conversaciones».",
    "fuera_de_ambito": (
        "Solo puedo ayudarte con la documentación y los datos internos de la empresa: "
        "políticas, procedimientos, beneficios, calendario… Esa consulta queda fuera de mi "
        "ámbito. ¿Hay algo de la empresa en lo que te pueda ayudar?"
    ),
    "ayuda": (
        "Puedo responder preguntas sobre los documentos visibles para tu rol, siempre citando "
        "la fuente; decirte qué documentos puedes consultar; consultar datos internos como los "
        "festivos; y preparar acciones (abrir un ticket, solicitar vacaciones) que tú apruebas "
        "antes de que se ejecuten."
    ),
}

# Con estos tipos el LLM orienta con el catálogo del rol (app/rag/orientacion.py); la plantilla
# queda de respaldo si el modelo falla.
ORIENTADAS = ("ayuda", "fuera_de_ambito")
