# Login de la web (GitHub / Entra ID) con providers simulados: sin credenciales ni Azure.
# Ejecutar: terraform -chdir=infra/apps test

mock_provider "azurerm" {}
mock_provider "azapi" {}
mock_provider "random" {}

override_data {
  target = data.terraform_remote_state.platform
  values = {
    outputs = {
      alcance                      = "completo"
      name                         = "ragseg-dev"
      location                     = "eastus2"
      resource_group_name          = "rg-ragseg-dev"
      tags                         = {}
      acr_login_server             = "acr.azurecr.io"
      container_app_environment_id = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.App/managedEnvironments/cae"
      backend_identity_id          = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/b"
      backend_identity_client_id   = "00000000-0000-0000-0000-00000000000b"
      web_identity_id              = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/w"
      ingest_identity_id           = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/i"
      ingest_identity_client_id    = "00000000-0000-0000-0000-00000000000c"
      key_vault_id                 = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/kv"
      key_vault_uri                = "https://kv.vault.azure.net/"
      secretos_en_key_vault        = ["database-url"]
      openai_endpoint              = "https://oai.openai.azure.com/"
      chat_deployment              = "gpt-4o"
      embedding_deployment         = "text-embedding-ada-002"
      ligero_deployment            = ""
      embedding_dimensions         = 1536
      vector_store                 = "azure_search"
      search_endpoint              = "https://srch.search.windows.net"
      qdrant_modo                  = "local"
      qdrant_url                   = ""
      content_safety_endpoint      = ""
      storage_blob_endpoint        = "https://st.blob.core.windows.net/"
      storage_container            = "documentos"
    }
  }
}

variables {
  platform_state = {
    resource_group_name  = "rg-tfstate"
    storage_account_name = "sttfstate"
    container_name       = "tfstate"
    key                  = "dev/platform.tfstate"
  }
  backend_image = "acr.azurecr.io/backend:x"
  web_image     = "acr.azurecr.io/web:x"
}

run "github_por_defecto" {
  command = plan
  variables {
    github_oauth_client_id     = "Iv1.abc"
    github_oauth_client_secret = "secreto"
    administradores            = ["CSR9497"]
  }
  assert {
    condition     = length(azurerm_key_vault_secret.github_oauth) == 1
    error_message = "El secreto de la OAuth App va a Key Vault"
  }
  assert {
    condition     = local.secreto_login == "github-provider-authentication-secret"
    error_message = "Easy Auth de GitHub lee su secreto con este nombre"
  }
  assert {
    condition     = local.common_env.ASIGNACIONES_INICIALES == jsonencode({ "github:csr9497" = ["administrador", "rrhh", "finanzas", "public"] })
    error_message = "Los administradores iniciales se normalizan a github:<usuario> con todos los roles"
  }
  assert {
    condition     = local.common_env.AUTH_MODO == "easyauth"
    error_message = "El backend toma la identidad de Easy Auth"
  }
}

run "github_sin_oauth_app_falla" {
  command = plan
  variables {
    administradores = ["csr9497"]
  }
  expect_failures = [terraform_data.validaciones]
}

run "github_sin_administrador_falla" {
  command = plan
  variables {
    github_oauth_client_id     = "Iv1.abc"
    github_oauth_client_secret = "secreto"
  }
  expect_failures = [terraform_data.validaciones]
}

run "entra" {
  command = plan
  variables {
    login_proveedor = "entra"
    identidad_state = {
      resource_group_name  = "rg-tfstate"
      storage_account_name = "sttfstate"
      container_name       = "tfstate"
      key                  = "dev/identidad.tfstate"
    }
  }
  override_data {
    target = data.terraform_remote_state.identidad[0]
    values = {
      outputs = {
        client_id         = "11111111-1111-1111-1111-111111111111"
        tenant_id         = "22222222-2222-2222-2222-222222222222"
        secreto_key_vault = "entra-client-secret"
      }
    }
  }
  assert {
    condition     = length(azurerm_key_vault_secret.github_oauth) == 0 && local.secreto_login == "microsoft-provider-authentication-secret"
    error_message = "Con Entra ID no se crea el secreto de GitHub"
  }
}

run "prueba_sin_login_solo_desde_mi_ip" {
  command = plan
  variables {
    login_proveedor = "ip"
    ips_permitidas  = ["203.0.113.7/32"]
  }
  assert {
    condition     = length(azapi_resource.web_auth) == 0 && length(azurerm_key_vault_secret.github_oauth) == 0
    error_message = "Sin login no se configura Easy Auth ni la OAuth App"
  }
  assert {
    condition     = azurerm_container_app.web.ingress[0].ip_security_restriction[0].ip_address_range == "203.0.113.7/32"
    error_message = "La web solo admite las IPs permitidas"
  }
  assert {
    condition     = local.common_env.ENTORNO == "dev" && local.common_env.AUTH_MODO == "stub"
    error_message = "Modo prueba: entorno dev sin login (prod lo rechazaría)"
  }
}

run "prueba_sin_ips_falla" {
  command = plan
  variables {
    login_proveedor = "ip"
  }
  expect_failures = [terraform_data.validaciones]
}
