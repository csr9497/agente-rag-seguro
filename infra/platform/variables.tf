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
  description = "Región con cuota para gpt-4o y text-embedding-ada-002"
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
    deployment_name = "text-embedding-ada-002"
    model_name      = "text-embedding-ada-002"
    model_version   = "2" # GA, retirada 2028-02-09
    sku_name        = "Standard"
    capacity        = 30
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
  description = "modelos: RG, OpenAI, Storage, Key Vault, Log Analytics y vector store (app en local). completo: además red, ACR, Container Apps, PostgreSQL e identidades."
  default     = "modelos"
  validation {
    condition     = contains(["modelos", "completo"], var.alcance)
    error_message = "alcance debe ser 'modelos' o 'completo'."
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
