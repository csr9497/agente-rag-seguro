"""Punto de entrada para LangGraph Studio / `langgraph dev` (ver langgraph.json).

Expone el mismo grafo compilado que usa la API, construido con la configuración del
entorno (.env). Entrada de ejemplo en Studio:

    {"pregunta": "¿Cuántos días de vacaciones tengo?", "usuario": {"groups": ["public"]}}
"""

from app.config import get_settings
from app.deps import build_servicios
from app.graph.entrada_studio import crear_entrada_studio, crear_estado_studio

# Misma composición que la API: registro de permisos, access_guardrail y roles. La entrada
# ofrece el rol como desplegable (roles activos) y la versión de guardrails se elige en el
# contexto del asistente (Manage Assistants → Context).
_servicios = build_servicios(get_settings())
_roles = [r.id for r in _servicios.repo_roles.listar(incluir_inactivos=False)]
graph = _servicios.agente.grafo_con_entrada(
    crear_entrada_studio(_roles), crear_estado_studio(_roles)
)
