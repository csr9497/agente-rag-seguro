# Azure AI Content Safety (Prompt Shields). Sin claves: la app usa Managed Identity
# (rol Cognitive Services User), por eso local_auth_enabled = false.
resource "azurerm_cognitive_account" "this" {
  name                          = var.name
  location                      = var.location
  resource_group_name           = var.resource_group_name
  kind                          = "ContentSafety"
  sku_name                      = var.sku_name
  custom_subdomain_name         = var.name # necesario para autenticación con Entra ID
  local_auth_enabled            = false
  public_network_access_enabled = var.public_network_access_enabled
  tags                          = var.tags

  network_acls {
    default_action = var.public_network_access_enabled ? "Allow" : "Deny"
  }
}
