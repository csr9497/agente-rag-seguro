variable "name" { type = string }
variable "location" { type = string }
variable "resource_group_name" { type = string }
variable "sku_name" {
  type    = string
  default = "Balanced_B0"
}
variable "high_availability_enabled" {
  type    = bool
  default = false
}
variable "public_network_access_enabled" { type = bool }
variable "tags" { type = map(string) }
