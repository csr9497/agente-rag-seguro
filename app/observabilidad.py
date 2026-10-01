"""Trazas en LangSmith.

Modos (`TRAZAS_MODO`):
- apagado: no se envía nada (por defecto).
- completo: todo el contenido (preguntas, fragmentos, respuestas). Solo dev.
- enmascarado: estructura completa (nodos, tools, tiempos, tokens, rol, doc_ids) con la PII
  enmascarada y el texto de los fragmentos oculto. Para prod.

Arrancar con `completo` en `ENTORNO=prod` es un error de configuración: la app no arranca.
"""

import hashlib
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from langsmith import Client, tracing_context

from app.config import ConfiguracionInseguraError, Settings
from app.security.deteccion import TIPOS_PII, enmascarar_pii

logger = logging.getLogger(__name__)

OCULTO = "[oculto]"
_CLAVES_CONTENIDO = {"contenido", "fragmento"}


def ocultar(valor: Any) -> Any:
    """Enmascara PII en todo texto y oculta el contenido de documentos (modo enmascarado)."""
    if isinstance(valor, str):
        return enmascarar_pii(valor, TIPOS_PII)[0]
    if isinstance(valor, dict):
        es_tool = valor.get("role") == "tool"
        return {
            k: OCULTO if k in _CLAVES_CONTENIDO or (es_tool and k == "content") else ocultar(v)
            for k, v in valor.items()
        }
    if isinstance(valor, list | tuple):
        return [ocultar(v) for v in valor]
    if hasattr(valor, "model_dump"):
        return ocultar(valor.model_dump(mode="json"))
    return valor


def configurar_trazas(settings: Settings) -> Client | None:
    """Prepara LangSmith según el modo. Devuelve el cliente a usar, o None si no se traza."""
    if settings.trazas_modo == "apagado":
        os.environ["LANGSMITH_TRACING"] = "false"
        return None
    if settings.trazas_modo == "completo" and settings.entorno == "prod":
        raise ConfiguracionInseguraError(
            "TRAZAS_MODO=completo no está permitido en ENTORNO=prod (usa 'enmascarado')"
        )
    if settings.langsmith_api_key is None:
        logger.warning(
            "TRAZAS_MODO=%s sin LANGSMITH_API_KEY: trazas desactivadas", settings.trazas_modo
        )
        os.environ["LANGSMITH_TRACING"] = "false"
        return None
    # El SDK y el tracer de LangGraph leen estas variables.
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key.get_secret_value()
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    if settings.trazas_modo == "enmascarado":
        return Client(hide_inputs=ocultar, hide_outputs=ocultar)
    return Client()


def usuario_seudonimo(usuario_id: str) -> str:
    return hashlib.sha256(usuario_id.encode()).hexdigest()[:12]


@contextmanager
def traza_consulta(
    cliente: Client | None, settings: Settings, *, roles: list[str], conversacion_id: str | None
) -> Iterator[dict[str, Any]]:
    """Contexto de una consulta: cliente/proyecto de LangSmith y metadata de la ejecución raíz.
    Devuelve el `config` para `grafo.invoke`."""
    config: dict[str, Any] = {
        "run_name": "consulta",
        "tags": [f"rol:{r}" for r in roles] + [f"entorno:{settings.entorno}"],
        "metadata": {
            "roles": roles,
            "conversacion_id": conversacion_id,
            "entorno": settings.entorno,
            "vector_store": settings.vector_store,
            "version": settings.app_version,
            "trazas_modo": settings.trazas_modo,
        },
    }
    if cliente is None:
        yield config
        return
    with tracing_context(enabled=True, client=cliente, project_name=settings.langsmith_project):
        yield config
