# Identidad de la aplicación en Entra ID (login de la web pública con Easy Auth):
# app registration con los roles de la app, service principal que exige asignación (solo
# entran usuarios con algún rol) y secreto del cliente guardado en Key Vault.
#
# Se aplica con TU sesión (`make desplegar`): necesita permisos de directorio para registrar
# aplicaciones, que la identidad de GitHub Actions no tiene.

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

data "azuread_client_config" "actual" {}

locals {
  p       = data.terraform_remote_state.platform.outputs
  web_url = "https://ca-web-${local.p.name}.${local.p.container_app_environment_default_domain}"
  # Deben coincidir con los ids de rol de la app (app/persistencia/repositorios.py).
  roles = {
    administrador = "Administrador: gestiona roles y permisos"
    rrhh          = "Recursos Humanos"
    finanzas      = "Finanzas"
    public        = "Empleado general"
  }
  asignaciones = merge(
    { for rol in var.roles_desplegador : "${data.azuread_client_config.actual.object_id}/${rol}" => {
      principal = data.azuread_client_config.actual.object_id
      rol       = rol
    } },
    merge([for oid, roles in var.asignaciones : {
      for rol in roles : "${oid}/${rol}" => { principal = oid, rol = rol }
    }]...)
  )
}

resource "terraform_data" "validaciones" {
  lifecycle {
    precondition {
      condition     = local.p.alcance == "completo" && local.p.container_app_environment_default_domain != ""
      error_message = "Aplica antes platform con alcance=completo (make desplegar lo hace)."
    }
    precondition {
      condition     = alltrue([for a in values(local.asignaciones) : contains(keys(local.roles), a.rol)])
      error_message = "Rol desconocido en roles_desplegador/asignaciones: usa administrador, rrhh, finanzas o public."
    }
  }
}

resource "random_uuid" "rol" {
  for_each = local.roles
}

resource "azuread_application" "app" {
  display_name     = "Asistente RAG (${local.p.name})"
  sign_in_audience = "AzureADMyOrg"
  owners           = [data.azuread_client_config.actual.object_id]

  dynamic "app_role" {
    for_each = local.roles
    content {
      allowed_member_types = ["User"]
      description          = app_role.value
      display_name         = app_role.value
      enabled              = true
      id                   = random_uuid.rol[app_role.key].result
      value                = app_role.key
    }
  }

  web {
    homepage_url  = local.web_url
    redirect_uris = ["${local.web_url}/.auth/login/aad/callback"]
    implicit_grant {
      id_token_issuance_enabled = true
    }
  }

  # Inicio de sesión y perfil básico (openid, profile, email).
  required_resource_access {
    resource_app_id = "00000003-0000-0000-c000-000000000000" # Microsoft Graph
    resource_access {
      id   = "e1fe6dd8-ba31-4d61-89e7-88639da4683d" # User.Read (delegado)
      type = "Scope"
    }
  }
}

resource "azuread_service_principal" "app" {
  client_id = azuread_application.app.client_id
  owners    = [data.azuread_client_config.actual.object_id]
  # Solo pueden iniciar sesión los usuarios con algún rol asignado.
  app_role_assignment_required = true
}

resource "azuread_app_role_assignment" "this" {
  for_each            = local.asignaciones
  app_role_id         = azuread_service_principal.app.app_role_ids[each.value.rol]
  principal_object_id = each.value.principal
  resource_object_id  = azuread_service_principal.app.object_id
}

resource "azuread_application_password" "easyauth" {
  application_id = azuread_application.app.id
  display_name   = "easy-auth-container-apps"
}

# Regla 3: el secreto solo vive en Key Vault (la web lo lee con su Managed Identity).
resource "azurerm_key_vault_secret" "cliente" {
  name         = "entra-client-secret"
  value        = azuread_application_password.easyauth.value
  key_vault_id = local.p.key_vault_id
}
