variable "name" { type = string }
variable "location" { type = string }
variable "resource_group_name" { type = string }
variable "sku_name" {
  type        = string
  description = "F0 (gratuito, 1 por suscripción y con límite de peticiones) o S0"
  default     = "S0"
}
variable "public_network_access_enabled" { type = bool }
variable "tags" { type = map(string) }
