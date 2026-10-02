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

# Login con Entra ID (opcional, login_proveedor=entra): identidad de la app creada por el
# stack identidad con tu sesión (make deploy). Con GitHub no hace falta.
data "terraform_remote_state" "identidad" {
  count   = local.entra ? 1 : 0
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
  p         = data.terraform_remote_state.platform.outputs
  entra     = var.login_proveedor == "entra"
  github    = var.login_proveedor == "github"
  con_login = var.login_proveedor != "ip"
  id        = local.entra ? data.terraform_remote_state.identidad[0].outputs : null
  # Secreto del cliente OAuth que lee Easy Auth (nombre de secreto de la Container App).
  secreto_login = local.entra ? "microsoft-provider-authentication-secret" : "github-provider-authentication-secret"
  # Primeros administradores (con todos los roles para poder probar cada perfil); el resto de
  # personas recibe roles desde la app.
  asignaciones_iniciales = !local.github ? {} : {
    for u in var.administradores : "github:${lower(u)}" => ["administrador", "rrhh", "finanzas", "public"]
  }

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
      "checkpoint-clave"     = "CHECKPOINT_CLAVE"
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
    # Modo «ip» (prueba sin login, solo desde las IPs permitidas): dev; si no, prod (exige login).
    ENTORNO           = local.con_login ? "prod" : "dev"
    TRAZAS_MODO       = local.langsmith ? "enmascarado" : "apagado"
    CACHE_BACKEND     = contains(local.p.secretos_en_key_vault, "redis-url") ? "redis" : "memoria"
    LANGSMITH_PROJECT = "agente-rag-${local.p.name}"
    # Prompts de LangSmith con la etiqueta var.prompts_etiqueta (los publica make deploy); sin
    # LangSmith o si no existe esa versión, los del repositorio.
    PROMPTS_ORIGEN   = local.langsmith ? "langsmith" : "local"
    PROMPTS_ETIQUETA = var.prompts_etiqueta
    APP_VERSION      = var.app_version
    # Sin Entra ID todavía: ni selección libre de rol ni gestión de documentos en Azure.
    SELECCION_LIBRE_DE_ROL = "false"
    GESTION_DOCUMENTOS     = "false"
    # Login: Easy Auth (GitHub o Entra ID) en la web; el backend toma la identidad del
    # principal que reenvía nginx con PROXY_SECRETO. Con ENTORNO=prod la app no arranca sin
    # ello (app/config.py: validar_seguridad). Modo «ip»: sin login, la web solo admite las IPs
    # permitidas y la persona de prueba tiene todos los roles (como en local).
    AUTH_MODO              = local.con_login ? "easyauth" : "stub"
    ASIGNACIONES_INICIALES = jsonencode(local.asignaciones_iniciales)
    DEFAULT_USER           = "prueba"
    DEFAULT_GROUPS         = jsonencode(local.con_login ? [] : ["administrador", "rrhh", "finanzas", "public"])
  }
}

resource "terraform_data" "validaciones" {
  lifecycle {
    precondition {
      condition     = local.p.alcance == "completo"
      error_message = "El stack apps requiere que platform se haya desplegado con alcance=completo."
    }
    precondition {
      condition     = !local.github || (var.github_oauth_client_id != "" && var.github_oauth_client_secret != "")
      error_message = "Login con GitHub: define github_oauth_client_id y github_oauth_client_secret (OAuth App, ver docs/despliegue.md)."
    }
    precondition {
      condition     = !local.github || length(var.administradores) > 0
      error_message = "Indica al menos un administrador (usuario de GitHub) en administradores."
    }
    precondition {
      condition     = local.con_login || length(var.ips_permitidas) > 0
      error_message = "Sin login (login_proveedor=ip) la web solo puede abrirse a IPs concretas: define ips_permitidas."
    }
    precondition {
      condition     = !local.entra || try(local.id.client_id != "", false)
      error_message = "Login con Entra ID: aplica antes el stack identidad (make deploy lo hace)."
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

  # Secreto del cliente OAuth para Easy Auth, leído de Key Vault con la identidad de la web.
  dynamic "secret" {
    for_each = local.con_login ? [local.secreto_login] : []
    content {
      name                = secret.value
      key_vault_secret_id = local.entra ? "${local.p.key_vault_uri}secrets/${try(local.id.secreto_key_vault, "")}" : try(azurerm_key_vault_secret.github_oauth[0].versionless_id, "")
      identity            = local.p.web_identity_id
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8080
    transport        = "auto"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }

    # Modo «ip»: solo estas IPs llegan a la web; el resto de Internet recibe 403.
    dynamic "ip_security_restriction" {
      for_each = local.con_login ? [] : var.ips_permitidas
      content {
        name             = "permitida-${ip_security_restriction.key}"
        action           = "Allow"
        ip_address_range = ip_security_restriction.value
      }
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

# Login obligatorio en la web (Easy Auth de Container Apps con GitHub o Entra ID). Sin sesión,
# redirige al login. Con GitHub cualquiera puede iniciar sesión, pero sin roles no ve nada
# (deny by default) hasta que un administrador se los asigna desde la app.
resource "azapi_resource" "web_auth" {
  count     = local.con_login ? 1 : 0
  type      = "Microsoft.App/containerApps/authConfigs@2024-03-01"
  name      = "current"
  parent_id = azurerm_container_app.web.id

  body = {
    properties = {
      platform = { enabled = true }
      globalValidation = {
        unauthenticatedClientAction = "RedirectToLoginPage"
        redirectToProvider          = local.entra ? "azureactivedirectory" : "github"
        excludedPaths               = ["/healthz"]
      }
      # jsondecode: las dos ramas tienen formas distintas (un proveedor u otro).
      identityProviders = jsondecode(local.entra ? jsonencode({
        azureActiveDirectory = {
          enabled = true
          registration = {
            clientId                = try(local.id.client_id, "")
            clientSecretSettingName = local.secreto_login
            openIdIssuer            = "https://login.microsoftonline.com/${try(local.id.tenant_id, "")}/v2.0"
          }
          validation = {
            allowedAudiences = [try(local.id.client_id, ""), "api://${try(local.id.client_id, "")}"]
          }
        }
        }) : jsonencode({
        gitHub = {
          enabled = true
          registration = {
            clientId                = var.github_oauth_client_id
            clientSecretSettingName = local.secreto_login
          }
        }
      }))
      login = {
        preserveUrlFragmentsForLogins = false
      }
    }
  }
}

# Regla 3: el secreto de la OAuth App de GitHub vive en Key Vault (lo escribe quien despliega).
resource "azurerm_key_vault_secret" "github_oauth" {
  count        = local.github ? 1 : 0
  name         = "github-oauth-client-secret"
  value        = var.github_oauth_client_secret
  key_vault_id = local.p.key_vault_id
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
