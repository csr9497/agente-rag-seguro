resource "azurerm_storage_account" "this" {
  name                            = var.name
  location                        = var.location
  resource_group_name             = var.resource_group_name
  account_tier                    = "Standard"
  account_replication_type        = var.replication_type
  min_tls_version                 = "TLS1_2"
  shared_access_key_enabled       = false # solo Entra ID (Managed Identity / az login)
  allow_nested_items_to_be_public = false
  public_network_access           = var.public_network_access_enabled ? "Enabled" : "Disabled"
  tags                            = var.tags

  blob_properties {
    versioning_enabled = true
    delete_retention_policy {
      days = 7
    }
  }
}

# Documentos fuente de la ingesta. Convención: <grupo>/<documento>.md
resource "azurerm_storage_container" "documentos" {
  name                  = var.container_name
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}
