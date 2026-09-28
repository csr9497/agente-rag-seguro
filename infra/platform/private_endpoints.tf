locals {
  private_endpoints = var.private_endpoints_enabled && local.completo ? merge(
    {
      openai = { resource_id = module.openai.id, subresource = "account", zone = "privatelink.openai.azure.com" }
      blob   = { resource_id = module.storage.id, subresource = "blob", zone = "privatelink.blob.core.windows.net" }
      vault  = { resource_id = module.keyvault.id, subresource = "vault", zone = "privatelink.vaultcore.azure.net" }
    },
    local.search ? {
      search = { resource_id = module.search[0].id, subresource = "searchService", zone = "privatelink.search.windows.net" }
    } : {}
  ) : {}
}

resource "azurerm_private_dns_zone" "this" {
  for_each            = local.private_endpoints
  name                = each.value.zone
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "this" {
  for_each            = local.private_endpoints
  name                = "link-${each.key}"
  private_dns_zone_id = azurerm_private_dns_zone.this[each.key].id
  virtual_network_id  = module.network[0].vnet_id
  tags                = local.tags
}

resource "azurerm_private_endpoint" "this" {
  for_each            = local.private_endpoints
  name                = "pe-${each.key}-${local.name}"
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  subnet_id           = module.network[0].private_endpoints_subnet_id
  tags                = local.tags

  private_service_connection {
    name                           = "psc-${each.key}"
    private_connection_resource_id = each.value.resource_id
    subresource_names              = [each.value.subresource]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [azurerm_private_dns_zone.this[each.key].id]
  }
}
