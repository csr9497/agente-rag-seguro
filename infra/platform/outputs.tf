output "resource_group_name" { value = azurerm_resource_group.this.name }
output "location" { value = var.location }
output "name" { value = local.name }
output "tags" { value = local.tags }

output "acr_name" { value = module.registry.name }
output "acr_login_server" { value = module.registry.login_server }
output "container_app_environment_id" { value = azurerm_container_app_environment.this.id }

output "backend_identity_id" { value = azurerm_user_assigned_identity.backend.id }
output "backend_identity_client_id" { value = azurerm_user_assigned_identity.backend.client_id }
output "web_identity_id" { value = azurerm_user_assigned_identity.web.id }
output "ingest_identity_id" { value = azurerm_user_assigned_identity.ingest.id }
output "ingest_identity_client_id" { value = azurerm_user_assigned_identity.ingest.client_id }

output "openai_endpoint" { value = module.openai.endpoint }
output "chat_deployment" { value = module.openai.chat_deployment }
output "embedding_deployment" { value = module.openai.embedding_deployment }
output "embedding_dimensions" { value = var.embedding_dimensions }

output "search_endpoint" { value = module.search.endpoint }
output "storage_blob_endpoint" { value = module.storage.blob_endpoint }
output "storage_account_name" { value = module.storage.name }
output "storage_container" { value = module.storage.container_name }
output "key_vault_name" { value = module.keyvault.name }
