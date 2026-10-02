"""LangGraph Studio: el orquestador multiagente (fase 5), con la composición real (.env).

Se ve cada agente del registro como un nodo (rag_agent, hr_agent, support_agent). Modo chat:
escribe como en la web (usuario de prueba con el rol `public`). Modo grafo: puedes elegir el
usuario, p. ej. {"usuario": {"id": "studio", "groups": ["public", "it_support", "dept:it"]}}, y
resolver las aprobaciones (interrupt) con
{"approved": true, "approver_id": "studio"} (confirm_user) u otro usuario con el rol que pida.
"""

from app.config import get_settings
from app.deps import build_orquestador, build_servicios
from app.models.schemas import Usuario

_servicios = build_servicios(get_settings())
graph = build_orquestador(_servicios).grafo_studio(Usuario(id="studio", groups=["public"]))
