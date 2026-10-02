import html
import re

from app.models.schemas import Turno
from app.prompts import local

SIN_CONTEXTO = "No encuentro esa información en los documentos a los que tienes acceso."

# Texto en app/prompts/orientacion.md («agente-rag-orientacion»): respuesta amable cuando no hay
# información, con el catálogo del rol (app/rag/orientacion.py).
ORIENTACION_PROMPT = local("orientacion")

# Impide que un documento o la pregunta abran/cierren nuestras etiquetas estructurales.
_ETIQUETAS = re.compile(
    r"</?\s*(contexto|fragmento|pregunta|historial|turno|catalogo|motivo)\b[^>]*>", re.IGNORECASE
)


def neutralizar(texto: str) -> str:
    return _ETIQUETAS.sub(lambda m: html.escape(m.group(0)), texto)


def build_historial(historial: list[Turno]) -> str:
    """Bloque <historial> delimitado; vacío si no hay turnos previos."""
    if not historial:
        return ""
    turnos = "\n".join(
        f"<turno>\nPregunta: {neutralizar(t.pregunta)}\n"
        f"Respuesta: {neutralizar(t.respuesta)}\n</turno>"
        for t in historial
    )
    return f"<historial>\n{turnos}\n</historial>\n\n"


def build_orientacion_prompt(pregunta: str, catalogo: str, motivo: str) -> str:
    return (
        f"<motivo>\n{motivo}\n</motivo>\n\n<catalogo>\n{neutralizar(catalogo)}\n</catalogo>\n\n"
        f"<pregunta>\n{neutralizar(pregunta)}\n</pregunta>"
    )
