resource "azurerm_search_service" "this" {
  name                          = var.name
  location                      = var.location
  resource_group_name           = var.resource_group_name
  sku                           = var.sku
  replica_count                 = var.sku == "free" ? null : var.replica_count
  partition_count               = var.sku == "free" ? null : 1
  local_authentication_enabled  = var.auth == "api_key"
  authentication_failure_mode   = var.auth == "api_key" ? "http403" : null
  public_network_access_enabled = var.public_network_access_enabled
  tags                          = var.tags
}
