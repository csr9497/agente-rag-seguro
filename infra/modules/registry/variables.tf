variable "name" {
  type        = string
  description = "Nombre global, solo alfanumérico"
}
variable "location" { type = string }
variable "resource_group_name" { type = string }
variable "sku" {
  type    = string
  default = "Basic"
}
variable "tags" { type = map(string) }
