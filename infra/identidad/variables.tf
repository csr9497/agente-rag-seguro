variable "platform_state" {
  type = object({
    resource_group_name  = string
    storage_account_name = string
    container_name       = string
    key                  = string
  })
  description = "Ubicación del estado del stack platform"
}

variable "roles_desplegador" {
  type        = list(string)
  description = "App roles que recibe quien despliega (para probar todos los perfiles)"
  default     = ["administrador", "rrhh", "finanzas", "public"]
}

variable "asignaciones" {
  type        = map(list(string))
  description = "Object ID de usuario (o grupo) de Entra ID → roles. P. ej. { \"<oid>\" = [\"rrhh\"] }"
  default     = {}
}
