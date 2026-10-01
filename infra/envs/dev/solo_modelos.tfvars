# Solo los modelos en Azure (gpt-4o + text-embedding-3-small) para desarrollar en local:
# Qdrant, SQLite y Redis corren en docker-compose. Coste fijo 0 (pago por token).
#   make up   ·   make down
project     = "ragseg"
environment = "dev"
location    = "eastus2"

alcance      = "solo_modelos"
vector_store = "qdrant"
qdrant_modo  = "local"

openai_local_auth_enabled = true # clave en .env para la app en Docker (sin az login)
content_safety            = false

# Quién puede usar los modelos con su az login: lo pasa scripts/entorno_local.sh (usuario actual).
developer_principal_ids = []

# Cuota de la suscripción: gpt-4o solo tiene cuota regional (Standard) en eastus2.
chat_model = {
  deployment_name = "gpt-4o"
  model_name      = "gpt-4o"
  model_version   = "2024-11-20"
  sku_name        = "Standard"
  capacity        = 30
}
