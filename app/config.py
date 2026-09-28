from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
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
    embedding_dimensions: int = 1536

    vector_store: Literal["qdrant", "azure_search"] = "qdrant"
    qdrant_url: str = "http://localhost:6333"
    # Modo embebido (sin servidor ni Docker): si se define, tiene prioridad sobre qdrant_url.
    # Solo admite un proceso a la vez (la ingesta y la app no pueden abrirlo simultáneamente).
    qdrant_path: str | None = None
    qdrant_collection: str = "documentos"
    azure_search_endpoint: str = ""
    azure_search_index: str = "documentos"

    azure_storage_account_url: str = ""
    azure_storage_container: str = "documentos"

    retrieval_top_k: int = 4
    min_score: float | None = None
    max_iteraciones: int = 3

    # Fase 1: sin autenticación. El usuario es un stub con estos grupos.
    default_user: str = "anonimo"
    default_groups: list[str] = ["public"]
    # Solo para local/pruebas de integración: permite fijar grupos con la cabecera
    # X-Usuario-Grupos. NUNCA activarlo en Azure (lo sustituye Entra ID en Fase 3).
    identidad_debug: bool = False
    # Expone /grafo y /grafo.mmd con la topología del agente. Solo local.
    exponer_topologia: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
