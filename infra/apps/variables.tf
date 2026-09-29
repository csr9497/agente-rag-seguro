variable "platform_state" {
  type = object({
    resource_group_name  = string
    storage_account_name = string
    container_name       = string
    key                  = string
  })
  description = "Ubicación del estado del stack platform"
}

variable "backend_image" {
  type        = string
  description = "Imagen completa del backend (<acr>.azurecr.io/backend:<tag>)"
}

variable "web_image" {
  type        = string
  description = "Imagen completa de la web (<acr>.azurecr.io/web:<tag>)"
}

variable "backend_min_replicas" {
  type    = number
  default = 1
}

variable "backend_max_replicas" {
  type    = number
  default = 3
}

variable "azure_openai_api_version" {
  type    = string
  default = "2024-10-21"
}

variable "app_version" {
  type        = string
  description = "Versión desplegada (git SHA); va a la metadata de las trazas"
  default     = "desconocida"
}

variable "entra_tenant_id" {
  type    = string
  default = ""
}

variable "entra_audiencia" {
  type        = string
  description = "Client ID de la app registration de la API. Vacío = sin autenticación (no recomendado)."
  default     = ""
}
