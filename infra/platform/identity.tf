# Identidades de mínimo privilegio: el backend solo lee; la ingesta escribe el índice.

resource "azurerm_user_assigned_identity" "backend" {
  name                = "id-backend-${local.name}"
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

resource "azurerm_user_assigned_identity" "web" {
  name                = "id-web-${local.name}"
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

resource "azurerm_user_assigned_identity" "ingest" {
  name                = "id-ingest-${local.name}"
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

locals {
  role_assignments = {
    backend_acr    = { scope = module.registry.id, role = "AcrPull", principal = azurerm_user_assigned_identity.backend.principal_id }
    backend_openai = { scope = module.openai.id, role = "Cognitive Services OpenAI User", principal = azurerm_user_assigned_identity.backend.principal_id }
    backend_search = { scope = module.search.id, role = "Search Index Data Reader", principal = azurerm_user_assigned_identity.backend.principal_id }

    web_acr = { scope = module.registry.id, role = "AcrPull", principal = azurerm_user_assigned_identity.web.principal_id }

    ingest_acr        = { scope = module.registry.id, role = "AcrPull", principal = azurerm_user_assigned_identity.ingest.principal_id }
    ingest_openai     = { scope = module.openai.id, role = "Cognitive Services OpenAI User", principal = azurerm_user_assigned_identity.ingest.principal_id }
    ingest_search_svc = { scope = module.search.id, role = "Search Service Contributor", principal = azurerm_user_assigned_identity.ingest.principal_id }
    ingest_search_idx = { scope = module.search.id, role = "Search Index Data Contributor", principal = azurerm_user_assigned_identity.ingest.principal_id }
    ingest_blob       = { scope = module.storage.id, role = "Storage Blob Data Reader", principal = azurerm_user_assigned_identity.ingest.principal_id }

    # Identidad que ejecuta Terraform: escribe el secreto de desarrollo en Key Vault.
    deployer_kv = { scope = module.keyvault.id, role = "Key Vault Secrets Officer", principal = data.azurerm_client_config.current.object_id }
  }

  developer_roles = merge([
    for pid in var.developer_principal_ids : {
      "dev_blob_${pid}" = { scope = module.storage.id, role = "Storage Blob Data Contributor", principal = pid }
      "dev_kv_${pid}"   = { scope = module.keyvault.id, role = "Key Vault Secrets User", principal = pid }
    }
  ]...)
}

resource "azurerm_role_assignment" "this" {
  for_each             = merge(local.role_assignments, local.developer_roles)
  scope                = each.value.scope
  role_definition_name = each.value.role
  principal_id         = each.value.principal
}

# La propagación de RBAC en el plano de datos de Key Vault tarda en aplicarse.
resource "time_sleep" "kv_rbac" {
  depends_on      = [azurerm_role_assignment.this]
  create_duration = "60s"
}

# Clave de Azure OpenAI solo para desarrollo local (regla 3: secretos en Key Vault).
resource "azurerm_key_vault_secret" "openai_key" {
  count        = var.openai_local_auth_enabled ? 1 : 0
  name         = "azure-openai-api-key"
  value        = module.openai.primary_access_key
  key_vault_id = module.keyvault.id
  content_type = "api-key"
  depends_on   = [time_sleep.kv_rbac]
}
