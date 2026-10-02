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

variable "prompts_etiqueta" {
  type        = string
  description = "Etiqueta de los prompts en LangSmith que usa la app (los entornos de CI usan la suya para no mover prod)"
  default     = "prod"
}

variable "login_proveedor" {
  type        = string
  description = "Login de la web: github (OAuth App, roles asignados en la app), entra (app registration con app roles) o ip (prueba sin login, solo desde ips_permitidas)"
  default     = "github"
  validation {
    condition     = contains(["github", "entra", "ip"], var.login_proveedor)
    error_message = "login_proveedor debe ser github, entra o ip."
  }
}

variable "github_oauth_client_id" {
  type        = string
  description = "Client ID de la OAuth App de GitHub (login_proveedor=github)"
  default     = ""
}

variable "github_oauth_client_secret" {
  type        = string
  description = "Client secret de la OAuth App de GitHub; se guarda en Key Vault"
  default     = ""
  sensitive   = true
}

variable "administradores" {
  type        = list(string)
  description = "Usuarios de GitHub que arrancan como administradores (con todos los roles)"
  default     = []
}

variable "identidad_state" {
  type = object({
    resource_group_name  = string
    storage_account_name = string
    container_name       = string
    key                  = string
  })
  description = "Ubicación del estado del stack identidad (solo login_proveedor=entra)"
  default     = null
}

variable "ips_permitidas" {
  type        = list(string)
  description = "login_proveedor=ip: rangos CIDR que pueden abrir la web (p. ej. [\"203.0.113.7/32\"])"
  default     = []
}
