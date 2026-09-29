from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración desde variables de entorno (.env en local, Container Apps en Azure).

    En Azure no hay claves: la app usa Managed Identity (DefaultAzureCredential).
    `azure_openai_api_key` solo existe para desarrollo local y se obtiene de Key Vault.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    azure_openai_endpoint: str = ""
    azure_openai_api_key: SecretStr | None = None
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_chat_deployment: str = "gpt-4o"
    azure_openai_embedding_deployment: str = "text-embedding-ada-002"
    # Con poca cuota (TPM) Azure responde 429 con Retry-After: el SDK espera ese tiempo y
    # reintenta con backoff exponencial hasta este número de veces.
    azure_openai_max_reintentos: int = 6
    azure_openai_timeout_s: float = 60.0
    embedding_dimensions: int = 1536

    vector_store: Literal["qdrant", "azure_search"] = "qdrant"
    qdrant_url: str = "http://localhost:6333"
    # Modo embebido (sin servidor ni Docker): si se define, tiene prioridad sobre qdrant_url.
    # Solo admite un proceso a la vez (la ingesta y la app no pueden abrirlo simultáneamente).
    qdrant_path: str | None = None
    qdrant_api_key: SecretStr | None = None  # Qdrant Cloud
    qdrant_collection: str = "documentos"
    azure_search_endpoint: str = ""
    azure_search_index: str = "documentos"
    # Solo si el servicio usa claves (search_auth=api_key); si no, Managed Identity.
    azure_search_api_key: SecretStr | None = None

    azure_storage_account_url: str = ""
    azure_storage_container: str = "documentos"
    # Originales de los documentos subidos: carpeta local o Azure Blob (roles en metadatos).
    almacen_documentos: Literal["local", "blob"] = "local"
    almacen_local_dir: str = "data/documentos"

    # Roles, registro de documentos y conversaciones. PostgreSQL en Azure (misma interfaz).
    database_url: str = "sqlite:///data/app.db"

    retrieval_top_k: int = 4
    min_score: float | None = None
    max_iteraciones: int = 3
    max_fragmentos_contexto: int = 12
    max_turnos_historial: int = 3
    # Caché semántica con permisos (clave = roles + huella de documentos visibles + versión).
    cache_semantica: bool = True
    # ada-002 puntúa ~0.95 preguntas distintas pero cercanas (p. ej. una pregunta compuesta y
    # una de sus partes): 0.97 evita servir la respuesta de la vecina (prueba en Azure).
    cache_umbral: float = 0.97
    cache_backend: Literal["memoria", "redis"] = "memoria"
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    cache_ttl_s: int = 86400

    # Observabilidad (LangSmith). Ver app/observabilidad.py.
    entorno: Literal["local", "dev", "prod"] = "local"
    trazas_modo: Literal["apagado", "completo", "enmascarado"] = "apagado"
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "agente-rag-dev"
    app_version: str = "local"

    # Autenticación: stub (local, sin login) o entra (token de Entra ID validado).
    auth_modo: Literal["stub", "entra"] = "stub"
    entra_tenant_id: str = ""
    entra_audiencia: str = Field(default="", description="Client ID o App ID URI de la API")
    entra_claim_roles: str = "roles"

    # Azure AI Content Safety (Prompt Shields). Sin endpoint, solo guardrails locales.
    # Sin clave se usa Managed Identity (rol Cognitive Services User sobre el recurso).
    content_safety_endpoint: str = ""
    content_safety_api_key: SecretStr | None = None
    # Si el servicio falla: "cerrado" bloquea la consulta / rechaza el documento.
    content_safety_fallo: Literal["cerrado", "abierto"] = "cerrado"

    # Fase 1: sin autenticación. El usuario es un stub con estos grupos.
    default_user: str = "anonimo"
    default_groups: list[str] = ["public"]
    # Solo para local/pruebas de integración: permite fijar grupos con la cabecera
    # X-Usuario-Grupos. NUNCA activarlo en Azure (lo sustituye Entra ID en Fase 3).
    identidad_debug: bool = False
    # Expone /grafo y /grafo.mmd con la topología del agente. Solo local.
    exponer_topologia: bool = False
    # API de gestión de documentos (subir/borrar). Desactivada por defecto hasta tener Entra ID.
    gestion_documentos: bool = False
    # Solo local: cualquier rol activo es elegible. En Azure, los roles del usuario (Entra ID).
    seleccion_libre_de_rol: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
