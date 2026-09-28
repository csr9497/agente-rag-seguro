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
