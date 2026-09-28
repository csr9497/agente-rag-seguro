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
    condition     = length(module.network) == 0 && length(module.registry) == 0 && length(module.postgres) == 0
    error_message = "alcance=modelos no crea red, ACR ni PostgreSQL"
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
      "azure-openai-api-key", "azure-search-api-key", "database-url", "langsmith-api-key",
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
