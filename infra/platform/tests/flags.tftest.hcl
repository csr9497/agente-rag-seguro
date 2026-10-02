# Verifica las combinaciones de flags con providers simulados: no usa credenciales ni toca
# Azure. Ejecutar: terraform -chdir=infra/platform test

mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id       = "00000000-0000-0000-0000-000000000001"
      object_id       = "00000000-0000-0000-0000-000000000002"
      subscription_id = "00000000-0000-0000-0000-000000000003"
    }
  }
}
mock_provider "random" {}
mock_provider "time" {}

run "modelos_con_ai_search_free" {
  command = plan
  variables {
    alcance      = "modelos"
    vector_store = "azure_search"
    search_sku   = "free"
  }
  assert {
    condition     = length(module.search) == 1
    error_message = "Debe crear AI Search"
  }
  assert {
    condition     = length(module.network) == 0 && length(module.registry) == 0 && length(module.postgres) == 0 && length(module.redis) == 0
    error_message = "alcance=modelos no crea red, ACR, PostgreSQL ni Redis"
  }
  assert {
    condition     = length(azurerm_container_app_environment.this) == 0
    error_message = "alcance=modelos no crea Container Apps"
  }
  assert {
    condition     = toset(output.secretos_en_key_vault) == toset(["azure-openai-api-key"])
    error_message = "Solo la clave de OpenAI para desarrollo local"
  }
}

run "modelos_con_qdrant_local" {
  command = plan
  variables {
    alcance      = "modelos"
    vector_store = "qdrant"
    qdrant_modo  = "local"
  }
  assert {
    condition     = length(module.search) == 0
    error_message = "Con Qdrant local no se crea AI Search"
  }
}

run "completo_con_ai_search_y_langsmith" {
  command = plan
  variables {
    alcance           = "completo"
    vector_store      = "azure_search"
    search_sku        = "basic"
    search_auth       = "api_key"
    langsmith_api_key = "lsv2_prueba"
  }
  assert {
    condition     = length(module.network) == 1 && length(module.postgres) == 1 && length(azurerm_container_app_environment.this) == 1
    error_message = "alcance=completo crea red, PostgreSQL y Container Apps"
  }
  assert {
    condition = toset(output.secretos_en_key_vault) == toset([
      "azure-openai-api-key", "azure-search-api-key", "checkpoint-clave", "database-url",
      "langsmith-api-key", "redis-url",
    ])
    error_message = "Secretos esperados en Key Vault"
  }
  assert {
    condition     = contains(keys(azurerm_role_assignment.this), "backend_search_idx")
    error_message = "El backend necesita escribir en el índice (subidas desde la UI)"
  }
}

run "qdrant_cloud_guarda_la_clave_en_key_vault" {
  command = plan
  variables {
    vector_store         = "qdrant"
    qdrant_modo          = "cloud"
    qdrant_cloud_url     = "https://x.cloud.qdrant.io"
    qdrant_cloud_api_key = "clave"
  }
  assert {
    condition     = contains(output.secretos_en_key_vault, "qdrant-api-key") && output.qdrant_url == "https://x.cloud.qdrant.io"
    error_message = "Qdrant Cloud: URL en outputs y clave en Key Vault"
  }
}

run "qdrant_efimero_requiere_completo" {
  command = plan
  variables {
    alcance      = "modelos"
    vector_store = "qdrant"
    qdrant_modo  = "container_efimero"
  }
  expect_failures = [terraform_data.validaciones]
}

run "qdrant_cloud_sin_url_falla" {
  command = plan
  variables {
    vector_store = "qdrant"
    qdrant_modo  = "cloud"
  }
  expect_failures = [terraform_data.validaciones]
}

run "private_endpoints_con_search_free_falla" {
  command = plan
  variables {
    alcance                   = "completo"
    private_endpoints_enabled = true
    search_sku                = "free"
  }
  expect_failures = [terraform_data.validaciones]
}

run "alcance_invalido" {
  command = plan
  variables {
    alcance = "todo"
  }
  expect_failures = [var.alcance]
}

run "completo_sin_redis" {
  command = plan
  variables {
    alcance     = "completo"
    search_sku  = "basic"
    cache_redis = false
  }
  assert {
    condition     = length(module.redis) == 0 && !contains(output.secretos_en_key_vault, "redis-url")
    error_message = "cache_redis=false no crea Redis ni su secreto"
  }
}

run "content_safety_con_rbac_y_sin_claves" {
  command = plan
  variables {
    alcance      = "completo"
    vector_store = "azure_search"
    search_sku   = "basic"
  }
  assert {
    condition     = length(module.content_safety) == 1
    error_message = "Content Safety se crea por defecto"
  }
  assert {
    condition     = alltrue([for k in ["backend_cs", "ingest_cs"] : azurerm_role_assignment.this[k].role_definition_name == "Cognitive Services User"])
    error_message = "Backend e ingesta acceden a Content Safety con Managed Identity"
  }
  assert {
    condition     = !anytrue([for s in output.secretos_en_key_vault : strcontains(s, "content-safety")])
    error_message = "Content Safety no usa claves: ningún secreto en Key Vault"
  }
}

run "sin_content_safety" {
  command = plan
  variables {
    alcance        = "modelos"
    vector_store   = "qdrant"
    qdrant_modo    = "local"
    content_safety = false
  }
  assert {
    condition     = length(module.content_safety) == 0 && output.content_safety_endpoint == ""
    error_message = "content_safety=false no crea el recurso y deja el endpoint vacío"
  }
}

run "solo_modelos_para_desarrollo_local" {
  command = plan
  variables {
    alcance        = "solo_modelos"
    vector_store   = "qdrant"
    qdrant_modo    = "local"
    content_safety = false
  }
  assert {
    condition     = length(module.keyvault) == 0 && length(module.storage) == 0 && length(module.monitoring) == 0 && length(module.search) == 0
    error_message = "solo_modelos no crea Key Vault, Storage, Log Analytics ni AI Search"
  }
  assert {
    condition     = length(module.content_safety) == 0 && length(output.secretos_en_key_vault) == 0
    error_message = "Sin Key Vault no hay secretos"
  }
  assert {
    condition     = output.key_vault_name == "" && output.storage_blob_endpoint == ""
    error_message = "Salidas vacías para lo que no se crea"
  }
}

run "solo_modelos_ignora_azure_search" {
  command = plan
  variables {
    alcance      = "solo_modelos"
    vector_store = "azure_search"
  }
  assert {
    condition     = length(module.search) == 0
    error_message = "solo_modelos usa Qdrant local aunque vector_store diga azure_search"
  }
}

run "modelo_ligero_opcional" {
  command = plan
  variables {
    alcance       = "solo_modelos"
    vector_store  = "qdrant"
    qdrant_modo   = "local"
    modelo_ligero = null
  }
  assert {
    condition     = output.ligero_deployment == ""
    error_message = "modelo_ligero = null no crea el despliegue ligero"
  }
}

run "postgres_en_otra_region_es_publico_y_restringido" {
  command = plan
  variables {
    alcance           = "completo"
    search_sku        = "basic"
    postgres_location = "southcentralus"
  }
  assert {
    condition     = module.postgres[0].publico_efectivo && length(module.postgres[0].reglas_firewall) == 1
    error_message = "PostgreSQL fuera de la región de la VNet: público con firewall solo para servicios de Azure"
  }
  assert {
    condition     = local.sufijo_postgres == substr(md5("southcentralus"), 0, 4)
    error_message = "El nombre depende de la región: el reintento en otra región no choca con el fallido"
  }
}

run "postgres_en_la_region_es_privado" {
  command = plan
  variables {
    alcance    = "completo"
    search_sku = "basic"
  }
  assert {
    condition     = !module.postgres[0].publico_efectivo && length(module.postgres[0].reglas_firewall) == 0
    error_message = "Por defecto PostgreSQL solo es accesible desde la VNet"
  }
}
