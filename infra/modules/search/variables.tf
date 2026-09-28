variable "name" { type = string }
variable "location" { type = string }
variable "resource_group_name" { type = string }
variable "sku" {
  type    = string
  default = "basic"
}
variable "replica_count" {
  type    = number
  default = 1
}
variable "public_network_access_enabled" { type = bool }
variable "tags" { type = map(string) }
