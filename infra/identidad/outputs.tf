output "client_id" { value = azuread_application.app.client_id }
output "tenant_id" { value = data.azuread_client_config.actual.tenant_id }
output "secreto_key_vault" { value = azurerm_key_vault_secret.cliente.name }
output "web_url" { value = local.web_url }
