output "id" { value = azurerm_cognitive_account.this.id }
output "endpoint" { value = azurerm_cognitive_account.this.endpoint }
output "chat_deployment" { value = azurerm_cognitive_deployment.chat.name }
output "embedding_deployment" { value = azurerm_cognitive_deployment.embedding.name }
output "primary_access_key" {
  value     = azurerm_cognitive_account.this.primary_access_key
  sensitive = true
}
output "name" { value = azurerm_cognitive_account.this.name }
output "ligero_deployment" { value = var.ligero == null ? "" : azurerm_cognitive_deployment.ligero[0].name }
