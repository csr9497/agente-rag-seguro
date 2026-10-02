# Despliegue completo en Azure (make deploy): app pública con login de Entra ID.
# Mismo estado que la etapa A (dev/platform.tfstate): amplía los recursos, no los duplica.
project     = "ragseg"
environment = "dev"
location    = "eastus2"

alcance      = "completo"
vector_store = "azure_search"
search_sku   = "free" # 0 €, 50 MB
search_auth  = "api_key"
# eastus2 sin capacidad para nuevos servicios de AI Search (InsufficientResourcesAvailable).
search_location = "southcentralus"

private_endpoints_enabled = false
openai_local_auth_enabled = true # también sirve para desarrollar en local contra estos modelos

# Caché en memoria (una réplica basta en dev): Managed Redis tiene coste fijo alto.
cache_redis = false

chat_model = {
  deployment_name = "gpt-4o"
  model_name      = "gpt-4o"
  model_version   = "2024-11-20"
  sku_name        = "Standard"
  capacity        = 30
}

content_safety     = true
content_safety_sku = "F0"

# Usuario con acceso de desarrollo (modelos, blobs, índice, secretos). make deploy lo
# sustituye por el de tu `az login`; GitHub Actions usa este valor (mantenlo igual que el tuyo
# para que ambos caminos no se pisen).
developer_principal_ids = ["772b9ea7-22d9-47ae-a974-5a297aea7ee0"]
