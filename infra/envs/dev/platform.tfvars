project     = "ragseg"
environment = "dev"
location    = "eastus2"

# Etapa A: solo modelos, almacenamiento y vector store; la app corre en local.
alcance      = "modelos"
vector_store = "azure_search" # "qdrant" + qdrant_modo = "local" usa el Qdrant de docker-compose
search_sku   = "free"         # 0 €, 50 MB
search_auth  = "api_key"      # la app en Docker no tiene az login; la clave va a Key Vault → .env

# dev: acceso público (con RBAC) para desarrollar en local contra Azure.
private_endpoints_enabled = false
openai_local_auth_enabled = true

# Object IDs de tu usuario o grupo (az ad signed-in-user show --query id -o tsv): OpenAI,
# blobs, índice y secretos para la app en local.
developer_principal_ids = ["772b9ea7-22d9-47ae-a974-5a297aea7ee0"]

# Opcional: la clave de LangSmith se pasa por TF_VAR_langsmith_api_key (nunca en este archivo).

# Cuota de la suscripción (Azure for Students, regiones permitidas por política): gpt-4o solo
# tiene cuota regional (Standard) en eastus2; GlobalStandard = 0.
chat_model = {
  deployment_name = "gpt-4o"
  model_name      = "gpt-4o"
  model_version   = "2024-11-20"
  sku_name        = "Standard"
  capacity        = 30
}

# Content Safety gratuito (5.000 registros/mes, uno por suscripción).
content_safety     = true
content_safety_sku = "F0"
