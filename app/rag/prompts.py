import html
import re

from app.models.schemas import ChunkRecuperado, Turno
from app.prompts import local

SIN_CONTEXTO = "No encuentro esa información en los documentos a los que tienes acceso."

# Texto en app/prompts/generacion.md (versionado también en LangSmith como
# «agente-rag-generacion»). Debe contener literalmente SIN_CONTEXTO (regla 3).
SYSTEM_PROMPT = local("generacion")

# Impide que un documento o la pregunta abran/cierren nuestras etiquetas estructurales.
_ETIQUETAS = re.compile(
    r"</?\s*(contexto|fragmento|pregunta|historial|turno)\b[^>]*>", re.IGNORECASE
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


def build_user_prompt(
    pregunta: str, recuperados: list[ChunkRecuperado], historial: list[Turno] | None = None
) -> str:
    bloques = [
        f'<fragmento n="{i}" fuente="{html.escape(r.chunk.fuente, quote=True)}">\n'
        f"{neutralizar(r.chunk.contenido)}\n</fragmento>"
        for i, r in enumerate(recuperados, start=1)
    ]
    contexto = "\n".join(bloques)
    return (
        f"{build_historial(historial or [])}<contexto>\n{contexto}\n</contexto>\n\n"
        f"<pregunta>\n{neutralizar(pregunta)}\n</pregunta>"
    )
