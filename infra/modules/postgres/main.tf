# PostgreSQL Flexible Server (roles, registro de documentos, conversaciones y auditoría).
# Privado (por defecto): solo accesible desde la VNet (subnet delegada + DNS privado).
# Público (publico=true, cuando la región de la VNet no admite PostgreSQL para la suscripción,
# p. ej. Azure for Students): TLS obligatorio y firewall limitado a servicios de Azure.
# Usuario y contraseña de la app en Key Vault; la administración se hace con Entra ID.

resource "random_password" "app" {
  length  = 32
  special = false
}

resource "azurerm_private_dns_zone" "this" {
  count               = var.publico ? 0 : 1
  name                = "${var.name}.private.postgres.database.azure.com"
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "this" {
  count               = var.publico ? 0 : 1
  name                = "link-postgres"
  private_dns_zone_id = azurerm_private_dns_zone.this[0].id
  virtual_network_id  = var.vnet_id
  tags                = var.tags
}

resource "azurerm_postgresql_flexible_server" "this" {
  name                          = var.name
  location                      = var.location
  resource_group_name           = var.resource_group_name
  version                       = "16"
  sku_name                      = var.sku_name
  storage_mb                    = 32768
  backup_retention_days         = 7
  delegated_subnet_id           = var.publico ? null : var.subnet_id
  private_dns_zone_id           = var.publico ? null : azurerm_private_dns_zone.this[0].id
  public_network_access_enabled = var.publico
  administrator_login           = "agente_app"
  administrator_password        = random_password.app.result
  tags                          = var.tags

  authentication {
    active_directory_auth_enabled = true
    password_auth_enabled         = true
    tenant_id                     = var.tenant_id
  }

  depends_on = [azurerm_private_dns_zone_virtual_network_link.this]

  lifecycle {
    ignore_changes = [zone] # Azure elige la zona; fijarla falla en regiones sin zonas
  }
}

# Modo público: solo servicios de Azure (Container Apps); nada desde Internet.
resource "azurerm_postgresql_flexible_server_firewall_rule" "azure" {
  count            = var.publico ? 1 : 0
  name             = "servicios-de-azure"
  server_id        = azurerm_postgresql_flexible_server.this.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

resource "azurerm_postgresql_flexible_server_database" "app" {
  name      = "agente"
  server_id = azurerm_postgresql_flexible_server.this.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# Antes sin count: mismas instancias en el estado.
moved {
  from = azurerm_private_dns_zone.this
  to   = azurerm_private_dns_zone.this[0]
}

moved {
  from = azurerm_private_dns_zone_virtual_network_link.this
  to   = azurerm_private_dns_zone_virtual_network_link.this[0]
}
