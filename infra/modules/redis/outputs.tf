output "id" { value = azurerm_managed_redis.this.id }
output "hostname" { value = azurerm_managed_redis.this.hostname }
output "redis_url" {
  description = "URL para redis-py (TLS); se guarda en Key Vault"
  value       = "rediss://:${azurerm_managed_redis.this.default_database[0].primary_access_key}@${azurerm_managed_redis.this.hostname}:${azurerm_managed_redis.this.default_database[0].port}/0"
  sensitive   = true
}
