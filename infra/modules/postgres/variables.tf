variable "name" { type = string }
variable "location" { type = string }
variable "resource_group_name" { type = string }
variable "tenant_id" { type = string }
variable "vnet_id" { type = string }
variable "subnet_id" { type = string }
variable "sku_name" { type = string }
variable "tags" { type = map(string) }
variable "publico" {
  type        = bool
  description = "Sin VNet: acceso público con TLS limitado a servicios de Azure"
  default     = false
}
