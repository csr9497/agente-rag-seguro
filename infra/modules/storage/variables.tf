variable "name" { type = string }
variable "location" { type = string }
variable "resource_group_name" { type = string }
variable "replication_type" {
  type    = string
  default = "LRS"
}
variable "container_name" {
  type    = string
  default = "documentos"
}
variable "public_network_access_enabled" { type = bool }
variable "tags" { type = map(string) }
