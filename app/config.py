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

    # Proveedor de modelos (ver app/modelos/ y `make check-models`):
    # - azure: Azure OpenAI (AZURE_OPENAI_*). Requiere suscripción, recurso y deployments.
    # - openai: OpenAI o un endpoint compatible (OPENAI_BASE_URL + OPENAI_API_KEY).
    modelos_proveedor: Literal["azure", "openai"] = "azure"
    # Con poca cuota (TPM) el proveedor responde 429 con Retry-After: el SDK espera ese tiempo
    # y reintenta con backoff exponencial hasta este número de veces.
    modelos_max_reintentos: int = 6
    modelos_timeout_s: float = 60.0

    openai_base_url: str = ""  # vacío = https://api.openai.com/v1
    openai_api_key: SecretStr | None = None
    openai_chat_model: str = "gpt-4o"
    openai_embedding_model: str = "text-embedding-3-small"  # 1536 dimensiones
    openai_ligero_model: str = ""

    azure_openai_endpoint: str = ""
    azure_openai_api_key: SecretStr | None = None
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_chat_deployment: str = "gpt-4o"
    azure_openai_embedding_deployment: str = "text-embedding-ada-002"
    # Modelo ligero opcional (p. ej. gpt-4.1-mini): guardián LLM de los guardrails.
    azure_openai_ligero_deployment: str = ""
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

    # Autenticación:
    # - stub: local, sin login.
    # - entra: token de Entra ID (Bearer) validado por la API (firma, emisor, audiencia).
    # - easyauth: en Azure, el login lo hace Easy Auth de Container Apps en la web y la
    #   identidad llega en X-MS-CLIENT-PRINCIPAL; solo se acepta si viene del proxy (nginx)
    #   con PROXY_SECRETO (el backend no tiene ingress público).
    auth_modo: Literal["stub", "entra", "easyauth"] = "stub"
    proxy_secreto: SecretStr | None = None
    # Roles que se asignan al arrancar (si faltan) a personas concretas, p. ej. el primer
    # administrador con login de GitHub: {"github:usuario": ["administrador", "public"]}.
    asignaciones_iniciales: dict[str, list[str]] = {}
    entra_tenant_id: str = ""
    entra_audiencia: str = Field(default="", description="Client ID o App ID URI de la API")
    entra_claim_roles: str = "roles"

    # Azure AI Content Safety (Prompt Shields). Sin endpoint, solo guardrails locales.
    # Sin clave se usa Managed Identity (rol Cognitive Services User sobre el recurso).
    content_safety_endpoint: str = ""
    content_safety_api_key: SecretStr | None = None
    # Si el servicio falla: "cerrado" bloquea la consulta / rechaza el documento.
    content_safety_fallo: Literal["cerrado", "abierto"] = "cerrado"

    # Versión de guardrails que usa la app (ver app/security/versiones.py). "auto": políticas
    # de uso (v3) y, si CONTENT_SAFETY_ENDPOINT está configurado, también Prompt Shields (v4).
    guardrail_entrada: Literal[
        "auto", "v1-heuristico", "v2-prompt-shields", "v3-politicas", "v4-politicas-shields"
    ] = "auto"
    guardrail_salida: Literal["v1-fuga-prompt", "v2-fuga-sensibles"] = "v2-fuga-sensibles"
    # Modelo del guardián LLM (v5). Vacío: el ligero si existe; si no, el de chat.
    modelo_guardian: str = ""

    # Prompts de sistema (app/prompts/): «local» = repositorio; «langsmith» = la etiqueta
    # PROMPTS_ETIQUETA de cada prompt en LangSmith (con la copia local como respaldo).
    prompts_origen: Literal["local", "langsmith"] = "local"
    prompts_etiqueta: str = "prod"

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

    # ------------------------------------------------------------ modelos (según proveedor)
    @property
    def modelo_chat(self) -> str:
        if self.modelos_proveedor == "openai":
            return self.openai_chat_model
        return self.azure_openai_chat_deployment

    @property
    def modelo_embeddings(self) -> str:
        if self.modelos_proveedor == "openai":
            return self.openai_embedding_model
        return self.azure_openai_embedding_deployment

    @property
    def modelo_ligero(self) -> str:
        if self.modelos_proveedor == "openai":
            return self.openai_ligero_model
        return self.azure_openai_ligero_deployment

    def modelos_faltantes(self) -> list[str]:
        """Variables que faltan para poder llamar a los modelos (vacío = configurado)."""
        if self.modelos_proveedor == "openai":
            # Sin URL propia es OpenAI y necesita clave; un endpoint local puede no pedirla.
            return [] if self.openai_api_key or self.openai_base_url else ["OPENAI_API_KEY"]
        return [] if self.azure_openai_endpoint else ["AZURE_OPENAI_ENDPOINT"]


class ConfiguracionInseguraError(RuntimeError):
    pass


def validar_seguridad(settings: Settings) -> None:
    """Falla cerrada: en ENTORNO=prod la API no arranca sin login (Entra ID o Easy Auth) ni
    con modos de depuración (identidad por cabecera, selección libre de rol)."""
    if settings.entorno != "prod":
        return
    problemas = []
    secreto = settings.proxy_secreto.get_secret_value() if settings.proxy_secreto else ""
    if settings.auth_modo == "stub":
        problemas.append("AUTH_MODO debe ser 'entra' o 'easyauth'")
    elif settings.auth_modo == "entra" and not (
        settings.entra_tenant_id and settings.entra_audiencia
    ):
        problemas.append("faltan ENTRA_TENANT_ID / ENTRA_AUDIENCIA")
    elif settings.auth_modo == "easyauth" and len(secreto) < 32:
        problemas.append("AUTH_MODO=easyauth requiere PROXY_SECRETO (32+ caracteres)")
    if settings.identidad_debug:
        problemas.append("IDENTIDAD_DEBUG no está permitido")
    if settings.seleccion_libre_de_rol:
        problemas.append("SELECCION_LIBRE_DE_ROL no está permitido")
    if problemas:
        raise ConfiguracionInseguraError(f"Configuración insegura en prod: {'; '.join(problemas)}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
