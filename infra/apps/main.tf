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

  # Configuración común de la app (sin secretos: todo con Managed Identity).
  common_env = {
    AZURE_OPENAI_ENDPOINT             = local.p.openai_endpoint
    AZURE_OPENAI_API_VERSION          = var.azure_openai_api_version
    AZURE_OPENAI_CHAT_DEPLOYMENT      = local.p.chat_deployment
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT = local.p.embedding_deployment
    EMBEDDING_DIMENSIONS              = tostring(local.p.embedding_dimensions)
    VECTOR_STORE                      = "azure_search"
    AZURE_SEARCH_ENDPOINT             = local.p.search_endpoint
    AZURE_SEARCH_INDEX                = "documentos"
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

      liveness_probe {
        transport = "HTTP"
        path      = "/health"
        port      = 8000
      }

      readiness_probe {
        transport = "HTTP"
        path      = "/health"
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

  template {
    container {
      name    = "ingest"
      image   = var.backend_image
      cpu     = 0.5
      memory  = "1Gi"
      command = ["python", "-m", "ingestor.ingest", "--source", "blob"]

      dynamic "env" {
        for_each = merge(local.common_env, {
          AZURE_CLIENT_ID           = local.p.ingest_identity_client_id
          AZURE_STORAGE_ACCOUNT_URL = local.p.storage_blob_endpoint
          AZURE_STORAGE_CONTAINER   = local.p.storage_container
        })
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }
}
