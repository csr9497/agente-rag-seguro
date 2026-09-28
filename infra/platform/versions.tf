terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.7"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.14"
    }
  }

  # Configuración parcial: se completa con -backend-config (ver infra/README y el workflow).
  backend "azurerm" {}
}

provider "azurerm" {
  # subscription_id / tenant_id / client_id llegan por ARM_* (OIDC en CI, az login en local).
  resource_providers_to_register = [
    "Microsoft.App",
    "Microsoft.CognitiveServices",
    "Microsoft.ContainerRegistry",
    "Microsoft.KeyVault",
    "Microsoft.ManagedIdentity",
    "Microsoft.Network",
    "Microsoft.OperationalInsights",
    "Microsoft.Search",
    "Microsoft.Storage",
  ]
  storage_use_azuread = true
  features {}
}
