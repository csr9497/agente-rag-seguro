"""Prompts de sistema versionados.

La copia del repositorio (ficheros .md de esta carpeta) es la versión «local». Los mismos
prompts se registran en LangSmith (Prompt Hub) con estos nombres para iterarlos en el
Playground; la app puede cargar una etiqueta o commit concreto (ver app/prompts/registro.py).
"""

from pathlib import Path

_CARPETA = Path(__file__).parent

# nombre interno → (nombre en LangSmith, fichero)
PROMPTS: dict[str, tuple[str, str]] = {
    "guardian": ("agente-rag-guardian", "guardian.md"),
    "orientacion": ("agente-rag-orientacion", "orientacion.md"),
    "rag_agent": ("agente-rag-rag-agent", "rag_agent.md"),
    "hr_agent": ("agente-rag-hr-agent", "hr_agent.md"),
    "support_agent": ("agente-rag-support-agent", "support_agent.md"),
    "orquestador": ("agente-rag-orquestador", "orquestador.md"),
    "sintesis": ("agente-rag-sintesis", "sintesis.md"),
}


def local(nombre: str) -> str:
    return (_CARPETA / PROMPTS[nombre][1]).read_text(encoding="utf-8")
