resource "azurerm_cognitive_account" "this" {
  name                          = var.name
  location                      = var.location
  resource_group_name           = var.resource_group_name
  kind                          = "OpenAI"
  sku_name                      = "S0"
  custom_subdomain_name         = var.name # necesario para autenticación con Entra ID
  local_auth_enabled            = var.local_auth_enabled
  public_network_access_enabled = var.public_network_access_enabled
  tags                          = var.tags

  network_acls {
    default_action = var.public_network_access_enabled ? "Allow" : "Deny"
  }
}

resource "azurerm_cognitive_deployment" "embedding" {
  name                   = var.embedding.deployment_name
  cognitive_account_id   = azurerm_cognitive_account.this.id
  version_upgrade_option = "OnceCurrentVersionExpired"

  model {
    format  = "OpenAI"
    name    = var.embedding.model_name
    version = var.embedding.model_version
  }

  sku {
    name     = var.embedding.sku_name
    capacity = var.embedding.capacity
  }
}

# Los deployments de una misma cuenta no admiten creación en paralelo.
resource "azurerm_cognitive_deployment" "chat" {
  name                   = var.chat.deployment_name
  cognitive_account_id   = azurerm_cognitive_account.this.id
  version_upgrade_option = "OnceCurrentVersionExpired"

  model {
    format  = "OpenAI"
    name    = var.chat.model_name
    version = var.chat.model_version
  }

  sku {
    name     = var.chat.sku_name
    capacity = var.chat.capacity
  }

  depends_on = [azurerm_cognitive_deployment.embedding]
}
