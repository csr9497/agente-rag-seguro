output "id" { value = azurerm_search_service.this.id }
output "endpoint" { value = "https://${azurerm_search_service.this.name}.search.windows.net" }
