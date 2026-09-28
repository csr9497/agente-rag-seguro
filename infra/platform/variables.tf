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

variable "search_sku" {
  type    = string
  default = "basic"
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
