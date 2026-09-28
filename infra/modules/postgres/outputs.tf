output "id" { value = azurerm_postgresql_flexible_server.this.id }
output "fqdn" { value = azurerm_postgresql_flexible_server.this.fqdn }
output "database_url" {
  description = "URL SQLAlchemy de la app (se guarda en Key Vault)"
  value       = "postgresql+psycopg://agente_app:${random_password.app.result}@${azurerm_postgresql_flexible_server.this.fqdn}:5432/agente?sslmode=require"
  sensitive   = true
}
