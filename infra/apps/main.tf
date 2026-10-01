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

# Identidad de la app en Entra ID (stack identidad, aplicado con tu sesión por make desplegar).
data "terraform_remote_state" "identidad" {
  backend = "azurerm"
  config = {
    resource_group_name  = var.identidad_state.resource_group_name
    storage_account_name = var.identidad_state.storage_account_name
    container_name       = var.identidad_state.container_name
    key                  = var.identidad_state.key
    use_azuread_auth     = true
  }
}

# Secreto compartido nginx → backend: solo el proxy puede presentar una identidad de Easy Auth.
resource "random_password" "proxy" {
  length  = 48
  special = false
}

locals {
  p  = data.terraform_remote_state.platform.outputs
  id = data.terraform_remote_state.identidad.outputs

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
    # Login obligatorio: Easy Auth (Entra ID) en la web; el backend toma la identidad del
    # principal que reenvía nginx con PROXY_SECRETO. Con ENTORNO=prod la app no arranca sin
    # ello (app/config.py: validar_seguridad).
    AUTH_MODO = "easyauth"
  }
}

resource "terraform_data" "validaciones" {
  lifecycle {
    precondition {
      condition     = local.p.alcance == "completo"
      error_message = "El stack apps requiere que platform se haya desplegado con alcance=completo."
    }
    precondition {
      condition     = local.id.client_id != "" && local.id.tenant_id != ""
      error_message = "La web es pública: aplica antes el stack identidad (make desplegar lo hace)."
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

  secret {
    name  = "proxy-secreto"
    value = random_password.proxy.result
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

      env {
        name        = "PROXY_SECRETO"
        secret_name = "proxy-secreto"
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

  secret {
    name  = "proxy-secreto"
    value = random_password.proxy.result
  }

  # Secreto del cliente de Entra ID para Easy Auth (nombre fijo que espera authConfigs).
  secret {
    name                = "microsoft-provider-authentication-secret"
    key_vault_secret_id = "${local.p.key_vault_uri}secrets/${local.id.secreto_key_vault}"
    identity            = local.p.web_identity_id
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

      env {
        name        = "PROXY_SECRETO"
        secret_name = "proxy-secreto"
      }

      liveness_probe {
        transport = "HTTP"
        path      = "/healthz"
        port      = 8080
      }
    }
  }
}

# Login obligatorio en la web (Easy Auth de Container Apps con Entra ID). Sin sesión, redirige
# al login; solo entran usuarios con algún rol asignado (app_role_assignment_required).
resource "azapi_resource" "web_auth" {
  type      = "Microsoft.App/containerApps/authConfigs@2024-03-01"
  name      = "current"
  parent_id = azurerm_container_app.web.id

  body = {
    properties = {
      platform = { enabled = true }
      globalValidation = {
        unauthenticatedClientAction = "RedirectToLoginPage"
        redirectToProvider          = "azureactivedirectory"
        excludedPaths               = ["/healthz"]
      }
      identityProviders = {
        azureActiveDirectory = {
          enabled = true
          registration = {
            clientId                = local.id.client_id
            clientSecretSettingName = "microsoft-provider-authentication-secret"
            openIdIssuer            = "https://login.microsoftonline.com/${local.id.tenant_id}/v2.0"
          }
          validation = {
            allowedAudiences = [local.id.client_id, "api://${local.id.client_id}"]
          }
        }
      }
      login = {
        preserveUrlFragmentsForLogins = false
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

# ---------------------------------------------------------------- jobs de ingesta (manuales)
# ingest: reindexa desde Blob (originales con sus roles en metadatos).
# sembrar: carga los documentos de ejemplo de la imagen (registro + Blob + índice); idempotente.
locals {
  jobs = {
    ingest  = ["python", "-m", "ingestor.ingest", "--source", "blob"]
    sembrar = ["python", "-m", "ingestor.ingest", "--source", "local", "--path", "ingestor/sample_docs"]
  }
}

resource "azurerm_container_app_job" "ingest" {
  for_each                     = local.jobs
  name                         = "caj-${each.key}-${local.p.name}"
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
      name    = each.key
      image   = var.backend_image
      cpu     = 0.5
      memory  = "1Gi"
      command = each.value

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
