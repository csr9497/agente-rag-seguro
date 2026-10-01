output "resource_group_name" { value = azurerm_resource_group.this.name }
output "location" { value = var.location }
output "name" { value = local.name }
output "tags" { value = local.tags }
output "alcance" { value = var.alcance }

output "openai_endpoint" { value = module.openai.endpoint }
output "chat_deployment" { value = module.openai.chat_deployment }
output "embedding_deployment" { value = module.openai.embedding_deployment }
output "ligero_deployment" { value = module.openai.ligero_deployment }
output "embedding_dimensions" { value = var.embedding_dimensions }

output "vector_store" { value = var.vector_store }
output "qdrant_modo" { value = var.qdrant_modo }
output "qdrant_url" { value = var.qdrant_modo == "cloud" ? var.qdrant_cloud_url : "" }
output "search_endpoint" { value = local.search ? module.search[0].endpoint : "" }
output "search_auth" { value = var.search_auth }

output "content_safety_endpoint" { value = var.content_safety ? module.content_safety[0].endpoint : "" }

output "openai_name" { value = module.openai.name }
output "storage_blob_endpoint" { value = local.base ? module.storage[0].blob_endpoint : "" }
output "storage_account_name" { value = local.base ? module.storage[0].name : "" }
output "storage_container" { value = local.base ? module.storage[0].container_name : "" }
output "key_vault_name" { value = local.base ? module.keyvault[0].name : "" }
output "secretos_en_key_vault" { value = nonsensitive(sort(keys(local.secretos))) }

# Solo alcance=completo (vacíos en alcance=modelos).
output "acr_name" { value = local.completo ? module.registry[0].name : "" }
output "acr_login_server" { value = local.completo ? module.registry[0].login_server : "" }
output "container_app_environment_id" { value = local.completo ? azurerm_container_app_environment.this[0].id : "" }
output "backend_identity_id" { value = local.completo ? azurerm_user_assigned_identity.backend[0].id : "" }
output "backend_identity_client_id" { value = local.completo ? azurerm_user_assigned_identity.backend[0].client_id : "" }
output "web_identity_id" { value = local.completo ? azurerm_user_assigned_identity.web[0].id : "" }
output "ingest_identity_id" { value = local.completo ? azurerm_user_assigned_identity.ingest[0].id : "" }
output "ingest_identity_client_id" { value = local.completo ? azurerm_user_assigned_identity.ingest[0].client_id : "" }
output "key_vault_uri" { value = local.base ? "https://${module.keyvault[0].name}.vault.azure.net/" : "" }
