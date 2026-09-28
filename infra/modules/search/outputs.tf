output "id" { value = azurerm_search_service.this.id }
output "endpoint" { value = "https://${azurerm_search_service.this.name}.search.windows.net" }
output "name" { value = azurerm_search_service.this.name }
output "primary_key" {
  value     = azurerm_search_service.this.primary_key
  sensitive = true
}
