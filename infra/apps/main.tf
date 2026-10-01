data "terraform_remote_state" "platform" {
  backend = "azurerm"
  config = {
    resource_group_name  = var.platform_state.resource_group_name
    storage_account_name = var.platform_state.storage_account_name
    container_name       = var.platform_state.container_name
    key                  = var.platform_state.key
    use_azuread_auth     = true
  }
}

locals {
  p = data.terraform_remote_state.platform.outputs

  qdrant_efimero = local.p.vector_store == "qdrant" && local.p.qdrant_modo == "container_efimero"
  qdrant_url = (
    local.qdrant_efimero ? "http://ca-qdrant-${local.p.name}" : local.p.qdrant_url
  )
  langsmith = contains(local.p.secretos_en_key_vault, "langsmith-api-key")

  # Secretos: referencias a Key Vault resueltas con la identidad gestionada (nunca en claro).
  secretos = {
    for nombre, variable in {
      "database-url"         = "DATABASE_URL"
      "langsmith-api-key"    = "LANGSMITH_API_KEY"
      "qdrant-api-key"       = "QDRANT_API_KEY"
      "azure-search-api-key" = "AZURE_SEARCH_API_KEY"
      "redis-url"            = "REDIS_URL"
    } : nombre => variable if contains(local.p.secretos_en_key_vault, nombre)
  }

  common_env = {
    AZURE_OPENAI_ENDPOINT             = local.p.openai_endpoint
    AZURE_OPENAI_API_VERSION          = var.azure_openai_api_version
    AZURE_OPENAI_CHAT_DEPLOYMENT      = local.p.chat_deployment
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT = local.p.embedding_deployment
    AZURE_OPENAI_LIGERO_DEPLOYMENT    = local.p.ligero_deployment
    EMBEDDING_DIMENSIONS              = tostring(local.p.embedding_dimensions)
    VECTOR_STORE                      = local.p.vector_store
    AZURE_SEARCH_ENDPOINT             = local.p.search_endpoint
    AZURE_SEARCH_INDEX                = "documentos"
    QDRANT_URL                        = local.qdrant_url
    CONTENT_SAFETY_ENDPOINT           = local.p.content_safety_endpoint
    CONTENT_SAFETY_FALLO              = "cerrado"
    ALMACEN_DOCUMENTOS                = "blob"
    AZURE_STORAGE_ACCOUNT_URL         = local.p.storage_blob_endpoint
    AZURE_STORAGE_CONTAINER           = local.p.storage_container
    ENTORNO                           = "prod"
    TRAZAS_MODO                       = local.langsmith ? "enmascarado" : "apagado"
    CACHE_BACKEND                     = contains(local.p.secretos_en_key_vault, "redis-url") ? "redis" : "memoria"
    LANGSMITH_PROJECT                 = "agente-rag-${local.p.name}"
    APP_VERSION                       = var.app_version
    # Sin Entra ID todavía: ni selección libre de rol ni gestión de documentos en Azure.
    SELECCION_LIBRE_DE_ROL = "false"
    GESTION_DOCUMENTOS     = "false"
    # Entra ID: si hay app registration, la API exige token (roles = app roles de Entra).
    AUTH_MODO       = var.entra_audiencia != "" ? "entra" : "stub"
    ENTRA_TENANT_ID = var.entra_tenant_id
    ENTRA_AUDIENCIA = var.entra_audiencia
  }
}

resource "terraform_data" "validaciones" {
  lifecycle {
    precondition {
      condition     = local.p.alcance == "completo"
      error_message = "El stack apps requiere que platform se haya desplegado con alcance=completo."
    }
  }
}

# ---------------------------------------------------------------- backend (FastAPI)
resource "azurerm_container_app" "backend" {
  name                         = "ca-backend-${local.p.name}"
  resource_group_name          = local.p.resource_group_name
  container_app_environment_id = local.p.container_app_environment_id
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.p.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [local.p.backend_identity_id]
  }

  registry {
    server   = local.p.acr_login_server
    identity = local.p.backend_identity_id
  }

  dynamic "secret" {
    for_each = local.secretos
    content {
      name                = secret.key
      key_vault_secret_id = "${local.p.key_vault_uri}secrets/${secret.key}"
      identity            = local.p.backend_identity_id
    }
  }

  # Solo accesible desde dentro del entorno (la web hace de proxy).
  ingress {
    external_enabled = false
    target_port      = 8000
    transport        = "auto"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = var.backend_min_replicas
    max_replicas = var.backend_max_replicas

    http_scale_rule {
      name                = "http"
      concurrent_requests = "20"
    }

    container {
      name   = "backend"
      image  = var.backend_image
      cpu    = 0.5
      memory = "1Gi"

      dynamic "env" {
        for_each = merge(local.common_env, { AZURE_CLIENT_ID = local.p.backend_identity_client_id })
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.secretos
        content {
          name        = env.value
          secret_name = env.key
        }
      }

      liveness_probe {
        transport = "HTTP"
        path      = "/health"
        port      = 8000
      }

      # /ready: base de datos y modelos configurados (/health solo indica que el proceso vive).
      readiness_probe {
        transport = "HTTP"
        path      = "/ready"
        port      = 8000
      }
    }
  }
}

# ---------------------------------------------------------------- web (nginx + estático)
resource "azurerm_container_app" "web" {
  name                         = "ca-web-${local.p.name}"
  resource_group_name          = local.p.resource_group_name
  container_app_environment_id = local.p.container_app_environment_id
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.p.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [local.p.web_identity_id]
  }

  registry {
    server   = local.p.acr_login_server
    identity = local.p.web_identity_id
  }

  ingress {
    external_enabled = true
    target_port      = 8080
    transport        = "auto"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 1
    max_replicas = 2

    container {
      name   = "web"
      image  = var.web_image
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name  = "BACKEND_URL"
        value = "https://${azurerm_container_app.backend.ingress[0].fqdn}"
      }

      liveness_probe {
        transport = "HTTP"
        path      = "/healthz"
        port      = 8080
      }
    }
  }
}

# ---------------------------------------------------------------- Qdrant efímero (opcional)
# Sin volumen: Qdrant no admite Azure Files. El índice se reconstruye desde Blob con el job
# de ingesta (roles en los metadatos de cada blob). Solo para demos.
resource "azurerm_container_app" "qdrant" {
  count                        = local.qdrant_efimero ? 1 : 0
  name                         = "ca-qdrant-${local.p.name}"
  resource_group_name          = local.p.resource_group_name
  container_app_environment_id = local.p.container_app_environment_id
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.p.tags

  ingress {
    external_enabled = false
    target_port      = 6333
    transport        = "http"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 1
    max_replicas = 1

    container {
      name   = "qdrant"
      image  = "qdrant/qdrant:v1.19.1"
      cpu    = 0.5
      memory = "1Gi"
    }
  }
}

# ---------------------------------------------------------------- job de ingesta (manual)
resource "azurerm_container_app_job" "ingest" {
  name                         = "caj-ingest-${local.p.name}"
  location                     = local.p.location
  resource_group_name          = local.p.resource_group_name
  container_app_environment_id = local.p.container_app_environment_id
  workload_profile_name        = "Consumption"
  replica_timeout_in_seconds   = 1800
  replica_retry_limit          = 1
  tags                         = local.p.tags

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [local.p.ingest_identity_id]
  }

  registry {
    server   = local.p.acr_login_server
    identity = local.p.ingest_identity_id
  }

  dynamic "secret" {
    for_each = local.secretos
    content {
      name                = secret.key
      key_vault_secret_id = "${local.p.key_vault_uri}secrets/${secret.key}"
      identity            = local.p.ingest_identity_id
    }
  }

  template {
    container {
      name    = "ingest"
      image   = var.backend_image
      cpu     = 0.5
      memory  = "1Gi"
      command = ["python", "-m", "ingestor.ingest", "--source", "blob"]

      dynamic "env" {
        for_each = merge(local.common_env, { AZURE_CLIENT_ID = local.p.ingest_identity_client_id })
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.secretos
        content {
          name        = env.value
          secret_name = env.key
        }
      }
    }
  }
}
