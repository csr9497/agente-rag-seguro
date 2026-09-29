# Identidades de mínimo privilegio (solo alcance=completo): el backend solo lee; la ingesta
# escribe el índice; la web solo descarga su imagen.

resource "azurerm_user_assigned_identity" "backend" {
  count               = local.completo ? 1 : 0
  name                = "id-backend-${local.name}"
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

resource "azurerm_user_assigned_identity" "web" {
  count               = local.completo ? 1 : 0
  name                = "id-web-${local.name}"
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

resource "azurerm_user_assigned_identity" "ingest" {
  count               = local.completo ? 1 : 0
  name                = "id-ingest-${local.name}"
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

locals {
  backend = local.completo ? azurerm_user_assigned_identity.backend[0].principal_id : null
  web     = local.completo ? azurerm_user_assigned_identity.web[0].principal_id : null
  ingest  = local.completo ? azurerm_user_assigned_identity.ingest[0].principal_id : null

  roles_completo = local.completo ? merge(
    {
      backend_acr    = { scope = module.registry[0].id, role = "AcrPull", principal = local.backend }
      backend_openai = { scope = module.openai.id, role = "Cognitive Services OpenAI User", principal = local.backend }
      backend_kv     = { scope = module.keyvault[0].id, role = "Key Vault Secrets User", principal = local.backend }
      # El backend también indexa (subida desde la UI): escribe blobs e índice.
      backend_blob  = { scope = module.storage[0].id, role = "Storage Blob Data Contributor", principal = local.backend }
      web_acr       = { scope = module.registry[0].id, role = "AcrPull", principal = local.web }
      ingest_acr    = { scope = module.registry[0].id, role = "AcrPull", principal = local.ingest }
      ingest_openai = { scope = module.openai.id, role = "Cognitive Services OpenAI User", principal = local.ingest }
      ingest_blob   = { scope = module.storage[0].id, role = "Storage Blob Data Reader", principal = local.ingest }
      ingest_kv     = { scope = module.keyvault[0].id, role = "Key Vault Secrets User", principal = local.ingest }
    },
    var.content_safety ? {
      backend_cs = { scope = module.content_safety[0].id, role = "Cognitive Services User", principal = local.backend }
      ingest_cs  = { scope = module.content_safety[0].id, role = "Cognitive Services User", principal = local.ingest }
    } : {},
    local.search ? {
      backend_search_idx = { scope = module.search[0].id, role = "Search Index Data Contributor", principal = local.backend }
      backend_search_svc = { scope = module.search[0].id, role = "Search Service Contributor", principal = local.backend }
      ingest_search_svc  = { scope = module.search[0].id, role = "Search Service Contributor", principal = local.ingest }
      ingest_search_idx  = { scope = module.search[0].id, role = "Search Index Data Contributor", principal = local.ingest }
    } : {}
  ) : {}

  # Identidad que ejecuta Terraform: escribe secretos en Key Vault.
  roles_base = local.base ? {
    deployer_kv = { scope = module.keyvault[0].id, role = "Key Vault Secrets Officer", principal = data.azurerm_client_config.current.object_id }
  } : {}

  # Desarrolladores (app en local contra Azure): OpenAI, blobs, secretos e índice.
  developer_roles = merge([
    for pid in var.developer_principal_ids : merge(
      { "dev_openai_${pid}" = { scope = module.openai.id, role = "Cognitive Services OpenAI User", principal = pid } },
      local.base ? {
        "dev_blob_${pid}" = { scope = module.storage[0].id, role = "Storage Blob Data Contributor", principal = pid }
        "dev_kv_${pid}"   = { scope = module.keyvault[0].id, role = "Key Vault Secrets User", principal = pid }
      } : {},
      var.content_safety ? {
        "dev_cs_${pid}" = { scope = module.content_safety[0].id, role = "Cognitive Services User", principal = pid }
      } : {},
      local.search ? {
        "dev_search_idx_${pid}" = { scope = module.search[0].id, role = "Search Index Data Contributor", principal = pid }
        "dev_search_svc_${pid}" = { scope = module.search[0].id, role = "Search Service Contributor", principal = pid }
      } : {}
    )
  ]...)
}

resource "azurerm_role_assignment" "this" {
  for_each             = merge(local.roles_base, local.roles_completo, local.developer_roles)
  scope                = each.value.scope
  role_definition_name = each.value.role
  principal_id         = each.value.principal
}

# La propagación de RBAC en el plano de datos de Key Vault tarda en aplicarse.
resource "time_sleep" "kv_rbac" {
  depends_on      = [azurerm_role_assignment.this]
  create_duration = "60s"
}

# ------------------------------------------------------------------ secretos (regla 3)
locals {
  # Sin Key Vault (solo_modelos) no hay secretos: la clave de OpenAI se lee con az (make env-modelos).
  secretos = !local.base ? {} : merge(
    var.openai_local_auth_enabled ? { "azure-openai-api-key" = module.openai.primary_access_key } : {},
    local.search && var.search_auth == "api_key" ? { "azure-search-api-key" = module.search[0].primary_key } : {},
    local.qdrant && var.qdrant_modo == "cloud" ? { "qdrant-api-key" = var.qdrant_cloud_api_key } : {},
    var.langsmith_api_key != "" ? { "langsmith-api-key" = var.langsmith_api_key } : {},
    local.completo ? { "database-url" = module.postgres[0].database_url } : {},
    local.completo && var.cache_redis ? { "redis-url" = module.redis[0].redis_url } : {},
  )
}

resource "azurerm_key_vault_secret" "this" {
  for_each     = nonsensitive(toset(keys(local.secretos)))
  name         = each.key
  value        = local.secretos[each.key]
  key_vault_id = module.keyvault[0].id
  depends_on   = [time_sleep.kv_rbac]
}
