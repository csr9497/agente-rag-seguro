terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.7"
    }
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
  }

  backend "azurerm" {}
}

provider "azurerm" {
  resource_providers_to_register = ["Microsoft.App"]
  features {}
}

# Autenticación de Container Apps (authConfigs), que azurerm no expone.
provider "azapi" {}
