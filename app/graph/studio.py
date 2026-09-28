"""Punto de entrada para LangGraph Studio / `langgraph dev` (ver langgraph.json).

Expone el mismo grafo compilado que usa la API, construido con la configuración del
entorno (.env). Entrada de ejemplo en Studio:

    {"pregunta": "¿Cuántos días de vacaciones tengo?",
     "usuario": {"id": "studio", "groups": ["public"]},   # groups = [rol de la conversación]
     "top_k": 4}
"""

from app.config import get_settings
from app.deps import build_servicios

# Misma composición que la API: registro de permisos, access_guardrail y roles.
graph = build_servicios(get_settings()).agente.grafo
