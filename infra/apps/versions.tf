terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.7"
    }
  }

  backend "azurerm" {}
}

provider "azurerm" {
  resource_providers_to_register = ["Microsoft.App"]
  features {}
}
