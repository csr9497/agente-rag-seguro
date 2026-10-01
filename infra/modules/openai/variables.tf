variable "name" { type = string }
variable "location" { type = string }
variable "resource_group_name" { type = string }
variable "local_auth_enabled" {
  type        = bool
  description = "Claves API habilitadas (solo para desarrollo local; en Azure se usa Managed Identity)"
}
variable "public_network_access_enabled" { type = bool }

variable "chat" {
  type = object({
    deployment_name = string
    model_name      = string
    model_version   = string
    sku_name        = string
    capacity        = number # miles de tokens por minuto
  })
}

variable "embedding" {
  type = object({
    deployment_name = string
    model_name      = string
    model_version   = string
    sku_name        = string
    capacity        = number
  })
}

variable "ligero" {
  type = object({
    deployment_name = string
    model_name      = string
    model_version   = string
    sku_name        = string
    capacity        = number
  })
  description = "Modelo ligero opcional (guardián de guardrails, pruebas de coste/latencia). null = no se crea"
  default     = null
}

variable "tags" { type = map(string) }
