variable "project" {
  type        = string
  description = "Prefijo corto del proyecto (minúsculas, sin guiones)"
  default     = "ragseg"
  validation {
    condition     = can(regex("^[a-z0-9]{3,10}$", var.project))
    error_message = "Entre 3 y 10 caracteres [a-z0-9]."
  }
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "location" {
  type        = string
  description = "Región con cuota para gpt-4o y text-embedding-3-small"
  default     = "eastus2"
}

variable "address_space" {
  type    = string
  default = "10.20.0.0/16"
}

variable "private_endpoints_enabled" {
  type        = bool
  description = "Private endpoints para OpenAI, AI Search, Storage y Key Vault; desactiva el acceso público a OpenAI, Search y Storage"
  default     = false
}

variable "openai_local_auth_enabled" {
  type        = bool
  description = "Permite claves API en Azure OpenAI (desarrollo local). Desactivar en prod."
  default     = true
}

variable "chat_model" {
  type = object({
    deployment_name = string
    model_name      = string
    model_version   = string
    sku_name        = string
    capacity        = number
  })
  default = {
    deployment_name = "gpt-4o"
    model_name      = "gpt-4o"
    model_version   = "2024-11-20" # Legacy, retirada 2027-04-14 → migrar a gpt-5.1
    sku_name        = "GlobalStandard"
    capacity        = 30
  }
}

variable "embedding_model" {
  type = object({
    deployment_name = string
    model_name      = string
    model_version   = string
    sku_name        = string
    capacity        = number
  })
  default = {
    # El mismo modelo que con MODELOS_PROVEEDOR=openai: los vectores de un índice valen con
    # cualquiera de los dos proveedores (1536 dimensiones).
    deployment_name = "text-embedding-3-small"
    model_name      = "text-embedding-3-small"
    model_version   = "1"
    sku_name        = "Standard"
    capacity        = 30
  }
}

variable "modelo_ligero" {
  type = object({
    deployment_name = string
    model_name      = string
    model_version   = string
    sku_name        = string
    capacity        = number
  })
  description = "Modelo ligero (guardián LLM de los guardrails; seleccionable en Studio). null = no se crea"
  # gpt-4o-mini ya no admite despliegues nuevos (retirado el 31-mar-2026): gpt-4.1-mini es su
  # sucesor directo (sin razonamiento extendido, rápido para clasificar). GlobalStandard: la
  # suscripción no tiene cuota Standard (regional) para este modelo.
  default = {
    deployment_name = "gpt-4.1-mini"
    model_name      = "gpt-4.1-mini"
    model_version   = "2025-04-14"
    sku_name        = "GlobalStandard"
    capacity        = 100
  }
}

variable "embedding_dimensions" {
  type    = number
  default = 1536
}

variable "search_location" {
  type        = string
  description = "Región de AI Search si difiere de location (p. ej. sin capacidad para nuevos servicios allí)"
  default     = null
}

variable "search_sku" {
  type        = string
  description = "free (50 MB, 3 índices, uno por suscripción, puede borrarse por inactividad) | basic | standard"
  default     = "free"
  validation {
    condition     = contains(["free", "basic", "standard"], var.search_sku)
    error_message = "search_sku debe ser 'free', 'basic' o 'standard'."
  }
}

variable "developer_principal_ids" {
  type        = list(string)
  description = "Object IDs (usuarios o grupos) que pueden subir documentos y leer el secreto de desarrollo"
  default     = []
}

variable "tags" {
  type    = map(string)
  default = {}
}

# ------------------------------------------------------------------ alcance y vector store
variable "alcance" {
  type        = string
  description = "solo_modelos: RG y Azure OpenAI (gpt-4o + text-embedding-3-small) para desarrollar en local contra los modelos en la nube (Qdrant, SQLite y Redis en Docker). modelos: además Storage, Key Vault, Log Analytics, AI Search y Content Safety (app en local). completo: además red, ACR, Container Apps, PostgreSQL e identidades."
  default     = "modelos"
  validation {
    condition     = contains(["solo_modelos", "modelos", "completo"], var.alcance)
    error_message = "alcance debe ser 'solo_modelos', 'modelos' o 'completo'."
  }
}

variable "vector_store" {
  type    = string
  default = "azure_search"
  validation {
    condition     = contains(["azure_search", "qdrant"], var.vector_store)
    error_message = "vector_store debe ser 'azure_search' o 'qdrant'."
  }
}

variable "qdrant_modo" {
  type        = string
  description = "local: el Qdrant de docker-compose (solo alcance=modelos). cloud: Qdrant Cloud (URL + API key en Key Vault). container_efimero: Container App sin volumen que se reconstruye desde Blob (solo alcance=completo; Qdrant no admite Azure Files)."
  default     = "local"
  validation {
    condition     = contains(["local", "cloud", "container_efimero"], var.qdrant_modo)
    error_message = "qdrant_modo debe ser 'local', 'cloud' o 'container_efimero'."
  }
}

variable "qdrant_cloud_url" {
  type    = string
  default = ""
}

variable "qdrant_cloud_api_key" {
  type      = string
  default   = ""
  sensitive = true
}

variable "search_auth" {
  type        = string
  description = "rbac (Managed Identity, sin claves) o api_key (clave en Key Vault; alternativa si el tier Free no admitiera RBAC)."
  default     = "rbac"
  validation {
    condition     = contains(["rbac", "api_key"], var.search_auth)
    error_message = "search_auth debe ser 'rbac' o 'api_key'."
  }
}

variable "langsmith_api_key" {
  type        = string
  description = "Opcional. Se guarda en Key Vault para que la app en Azure envíe trazas (enmascaradas)."
  default     = ""
  sensitive   = true
}

variable "postgres_sku" {
  type    = string
  default = "B_Standard_B1ms"
}

variable "postgres_location" {
  type        = string
  description = "Región de PostgreSQL si la de location no lo admite (Azure for Students). Distinta de location ⇒ acceso público con TLS limitado a servicios de Azure (la VNet no cruza regiones)"
  default     = null
}

variable "cache_redis" {
  type        = bool
  description = "Azure Managed Redis para la caché semántica compartida (solo alcance=completo)"
  default     = true
}

variable "content_safety" {
  type        = bool
  description = "Azure AI Content Safety (Prompt Shields) además de los guardrails locales"
  default     = true
}

variable "content_safety_sku" {
  type    = string
  default = "S0"
}

variable "redis_sku" {
  type    = string
  default = "Balanced_B0"
}
