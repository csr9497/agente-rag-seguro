data "azurerm_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 5
  special = false
  upper   = false
}

locals {
  name     = "${var.project}-${var.environment}"
  flat     = "${var.project}${var.environment}${random_string.suffix.result}" # nombres globales
  public   = !var.private_endpoints_enabled
  completo = var.alcance == "completo"
  base     = var.alcance != "solo_modelos" # Storage, Key Vault y Log Analytics
  search   = var.vector_store == "azure_search" && local.base
  qdrant   = var.vector_store == "qdrant"
  tags = merge(var.tags, {
    project     = var.project
    environment = var.environment
    managed_by  = "terraform"
    alcance     = var.alcance
  })
}

# Combinaciones inválidas: fallan en plan, antes de crear nada.
resource "terraform_data" "validaciones" {
  lifecycle {
    precondition {
      condition     = !(local.qdrant && var.qdrant_modo == "container_efimero" && !local.completo)
      error_message = "qdrant_modo=container_efimero requiere alcance=completo (necesita Container Apps)."
    }
    precondition {
      condition     = !(local.qdrant && var.qdrant_modo == "local" && local.completo)
      error_message = "qdrant_modo=local solo sirve con alcance=modelos (la app corre en tu equipo)."
    }
    precondition {
      condition     = !(local.qdrant && var.qdrant_modo == "cloud" && (var.qdrant_cloud_url == "" || var.qdrant_cloud_api_key == ""))
      error_message = "qdrant_modo=cloud requiere qdrant_cloud_url y qdrant_cloud_api_key."
    }
    precondition {
      condition     = !(var.private_endpoints_enabled && !local.completo)
      error_message = "private_endpoints_enabled requiere alcance=completo (sin VNet, la app local no llegaría a los servicios)."
    }
    precondition {
      condition     = !(var.alcance == "solo_modelos" && local.qdrant && var.qdrant_modo != "local")
      error_message = "alcance=solo_modelos usa el Qdrant local de docker-compose (qdrant_modo=local)."
    }
    precondition {
      condition     = !(var.private_endpoints_enabled && local.search && var.search_sku == "free")
      error_message = "El tier Free de AI Search no admite private endpoints: usa search_sku=basic."
    }
  }
}

resource "azurerm_resource_group" "this" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags
}

# ------------------------------------------------------------------ siempre (alcance=modelos)
module "monitoring" {
  count               = local.base ? 1 : 0
  source              = "../modules/monitoring"
  name                = local.name
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

module "keyvault" {
  count               = local.base ? 1 : 0
  source              = "../modules/keyvault"
  name                = "kv-${local.flat}"
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  tags                = local.tags
}

# Originales de los documentos (fuente de verdad para reindexar; roles en metadatos del blob).
module "storage" {
  count                         = local.base ? 1 : 0
  source                        = "../modules/storage"
  name                          = "st${local.flat}"
  location                      = var.location
  resource_group_name           = azurerm_resource_group.this.name
  public_network_access_enabled = local.public
  tags                          = local.tags
}

module "openai" {
  source                        = "../modules/openai"
  name                          = "oai-${local.flat}"
  location                      = var.location
  resource_group_name           = azurerm_resource_group.this.name
  local_auth_enabled            = var.openai_local_auth_enabled
  public_network_access_enabled = local.public
  chat                          = var.chat_model
  embedding                     = var.embedding_model
  ligero                        = var.modelo_ligero
  tags                          = local.tags
}

module "search" {
  count                         = local.search ? 1 : 0
  source                        = "../modules/search"
  name                          = "srch-${local.flat}"
  location                      = coalesce(var.search_location, var.location)
  resource_group_name           = azurerm_resource_group.this.name
  sku                           = var.search_sku
  auth                          = var.search_auth
  public_network_access_enabled = local.public
  tags                          = local.tags
}

module "content_safety" {
  count                         = var.content_safety ? 1 : 0
  source                        = "../modules/content_safety"
  name                          = "cs-${local.flat}"
  location                      = var.location
  resource_group_name           = azurerm_resource_group.this.name
  sku_name                      = var.content_safety_sku
  public_network_access_enabled = local.public
  tags                          = local.tags
}

# ------------------------------------------------------------------ solo alcance=completo
module "network" {
  count               = local.completo ? 1 : 0
  source              = "../modules/network"
  name                = local.name
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  address_space       = var.address_space
  tags                = local.tags
}

module "registry" {
  count               = local.completo ? 1 : 0
  source              = "../modules/registry"
  name                = "acr${local.flat}"
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

module "postgres" {
  count               = local.completo ? 1 : 0
  source              = "../modules/postgres"
  name                = "psql-${local.flat}"
  location            = coalesce(var.postgres_location, var.location)
  publico             = coalesce(var.postgres_location, var.location) != var.location
  resource_group_name = azurerm_resource_group.this.name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  vnet_id             = module.network[0].vnet_id
  subnet_id           = module.network[0].postgres_subnet_id
  sku_name            = var.postgres_sku
  tags                = local.tags
}

module "redis" {
  count                         = local.completo && var.cache_redis ? 1 : 0
  source                        = "../modules/redis"
  name                          = "redis-${local.flat}"
  location                      = var.location
  resource_group_name           = azurerm_resource_group.this.name
  sku_name                      = var.redis_sku
  public_network_access_enabled = true # acceso con clave TLS; private endpoint en una fase posterior
  tags                          = local.tags
}

resource "azurerm_container_app_environment" "this" {
  count                          = local.completo ? 1 : 0
  name                           = "cae-${local.name}"
  location                       = var.location
  resource_group_name            = azurerm_resource_group.this.name
  infrastructure_subnet_id       = module.network[0].container_apps_subnet_id
  internal_load_balancer_enabled = false # la web es pública; el backend tiene ingress interno
  logs_destination               = "log-analytics"
  log_analytics_workspace_id     = module.monitoring[0].workspace_id
  tags                           = local.tags

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }

  lifecycle {
    # Azure crea este RG gestionado automáticamente al usar perfiles de carga.
    ignore_changes = [infrastructure_resource_group_name]
  }
}
