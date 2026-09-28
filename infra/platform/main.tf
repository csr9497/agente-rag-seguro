data "azurerm_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 5
  special = false
  upper   = false
}

locals {
  name   = "${var.project}-${var.environment}"
  flat   = "${var.project}${var.environment}${random_string.suffix.result}" # nombres globales
  public = !var.private_endpoints_enabled
  tags = merge(var.tags, {
    project     = var.project
    environment = var.environment
    managed_by  = "terraform"
  })
}

resource "azurerm_resource_group" "this" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags
}

module "network" {
  source              = "../modules/network"
  name                = local.name
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  address_space       = var.address_space
  tags                = local.tags
}

module "monitoring" {
  source              = "../modules/monitoring"
  name                = local.name
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

module "registry" {
  source              = "../modules/registry"
  name                = "acr${local.flat}"
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

module "keyvault" {
  source              = "../modules/keyvault"
  name                = "kv-${local.flat}"
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  tags                = local.tags
}

module "storage" {
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
  tags                          = local.tags
}

module "search" {
  source                        = "../modules/search"
  name                          = "srch-${local.flat}"
  location                      = var.location
  resource_group_name           = azurerm_resource_group.this.name
  sku                           = var.search_sku
  public_network_access_enabled = local.public
  tags                          = local.tags
}

resource "azurerm_container_app_environment" "this" {
  name                           = "cae-${local.name}"
  location                       = var.location
  resource_group_name            = azurerm_resource_group.this.name
  infrastructure_subnet_id       = module.network.container_apps_subnet_id
  internal_load_balancer_enabled = false # la web es pública; el backend tiene ingress interno
  logs_destination               = "log-analytics"
  log_analytics_workspace_id     = module.monitoring.workspace_id
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
