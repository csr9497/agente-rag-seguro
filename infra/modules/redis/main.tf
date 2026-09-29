# Azure Managed Redis (Azure Cache for Redis deja de admitir instancias nuevas el 1-oct-2026 y
# se retira en 2028). Caché semántica compartida entre réplicas; la app no necesita búsqueda
# vectorial (compara en memoria las entradas de un mismo alcance).
resource "azurerm_managed_redis" "this" {
  name                      = var.name
  location                  = var.location
  resource_group_name       = var.resource_group_name
  sku_name                  = var.sku_name
  high_availability_enabled = var.high_availability_enabled
  public_network_access     = var.public_network_access_enabled ? "Enabled" : "Disabled"
  tags                      = var.tags

  default_database {
    access_keys_authentication_enabled = true
    client_protocol                    = "Encrypted"
    clustering_policy                  = "EnterpriseCluster" # compatible con clientes no-cluster
    eviction_policy                    = "AllKeysLRU"
  }
}
