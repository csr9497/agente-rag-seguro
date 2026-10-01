output "web_url" { value = "https://${azurerm_container_app.web.ingress[0].fqdn}" }
output "backend_internal_fqdn" { value = azurerm_container_app.backend.ingress[0].fqdn }
output "ingest_job_name" { value = azurerm_container_app_job.ingest["ingest"].name }
output "sembrar_job_name" { value = azurerm_container_app_job.ingest["sembrar"].name }
output "resource_group_name" { value = local.p.resource_group_name }
output "backend_app_name" { value = azurerm_container_app.backend.name }
output "web_app_name" { value = azurerm_container_app.web.name }
