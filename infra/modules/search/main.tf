resource "azurerm_search_service" "this" {
  name                          = var.name
  location                      = var.location
  resource_group_name           = var.resource_group_name
  sku                           = var.sku
  replica_count                 = var.replica_count
  partition_count               = 1
  local_authentication_enabled  = false # solo RBAC (Managed Identity)
  public_network_access_enabled = var.public_network_access_enabled
  tags                          = var.tags
}
