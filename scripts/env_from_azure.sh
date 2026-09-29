#!/usr/bin/env bash
# Rellena .env para ejecutar la app en local contra lo desplegado en Azure (alcance=modelos).
# Requiere: az login, `terraform -chdir=infra/platform init` con el backend remoto y tu object
# id en developer_principal_ids (roles de Key Vault, OpenAI, Blob e índice).
set -euo pipefail

TF="terraform -chdir=infra/platform output -raw"
KV=$($TF key_vault_name)
secreto() { az keyvault secret show --vault-name "$KV" -n "$1" --query value -o tsv; }

[[ -f .env ]] || cp .env.example .env
set_var() { # set_var NOMBRE valor  (no imprime valores)
  if grep -q "^$1=" .env; then sed -i.bak "s|^$1=.*|$1=$2|" .env && rm -f .env.bak
  else echo "$1=$2" >> .env; fi
}

set_var AZURE_OPENAI_ENDPOINT "$($TF openai_endpoint)"
set_var AZURE_OPENAI_API_KEY "$(secreto azure-openai-api-key)"
set_var AZURE_OPENAI_CHAT_DEPLOYMENT "$($TF chat_deployment)"
set_var AZURE_OPENAI_EMBEDDING_DEPLOYMENT "$($TF embedding_deployment)"

VS=$($TF vector_store)
set_var VECTOR_STORE "$VS"
if [[ "$VS" == "azure_search" ]]; then
  set_var AZURE_SEARCH_ENDPOINT "$($TF search_endpoint)"
  [[ "$($TF search_auth)" == "api_key" ]] && set_var AZURE_SEARCH_API_KEY "$(secreto azure-search-api-key)"
elif [[ "$($TF qdrant_modo)" == "cloud" ]]; then
  set_var QDRANT_URL "$($TF qdrant_url)"
  set_var QDRANT_API_KEY "$(secreto qdrant-api-key)"
fi

# Prompt Shields: sin clave (local_auth desactivado); usa tu az login fuera de Docker.
set_var CONTENT_SAFETY_ENDPOINT "$($TF content_safety_endpoint)"

# Originales en Blob: solo si la app corre fuera de Docker (usa tu az login; las claves de
# la cuenta de almacenamiento están desactivadas). En Docker se quedan en local.
set_var AZURE_STORAGE_ACCOUNT_URL "$($TF storage_blob_endpoint)"
set_var AZURE_STORAGE_CONTAINER "$($TF storage_container)"
echo ".env actualizado (vector store: $VS). Para guardar originales en Blob: ALMACEN_DOCUMENTOS=blob y ejecuta el backend con 'uv run uvicorn app.main:app'."
