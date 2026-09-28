variable "name" { type = string }
variable "location" { type = string }
variable "resource_group_name" { type = string }
variable "address_space" {
  type        = string
  description = "Rango de la VNet (/16 recomendado)"
}
variable "tags" { type = map(string) }
