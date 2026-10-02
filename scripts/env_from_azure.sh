#!/usr/bin/env bash
# Rellena .env para ejecutar la app en local contra lo desplegado en Azure (alcance=modelos o
# solo_modelos). Sin Key Vault (solo_modelos), la clave de OpenAI se lee con az.
# ENV_FILE=otro.env escribe en ese fichero en lugar de .env (make cloud-local).
# Requiere: az login, `terraform -chdir=infra/platform init` con el backend remoto y tu object
# id en developer_principal_ids (roles de Key Vault, OpenAI, Blob e índice).
set -euo pipefail

TF="terraform -chdir=infra/platform output -raw"
KV=$($TF key_vault_name)
secreto() { az keyvault secret show --vault-name "$KV" -n "$1" --query value -o tsv; }

ENV_FILE="${ENV_FILE:-.env}"
[[ -f "$ENV_FILE" ]] || cp .env.example "$ENV_FILE"
set_var() { # set_var NOMBRE valor  (no imprime valores)
  if grep -q "^$1=" "$ENV_FILE"; then sed -i.bak "s|^$1=.*|$1=$2|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
  else
    [[ ! -s "$ENV_FILE" || -z $(tail -c1 "$ENV_FILE") ]] || echo >> "$ENV_FILE" # sin pegarse
    echo "$1=$2" >> "$ENV_FILE"
  fi
}

set_var AZURE_OPENAI_ENDPOINT "$($TF openai_endpoint)"
if [[ -n "$KV" ]]; then
  set_var AZURE_OPENAI_API_KEY "$(secreto azure-openai-api-key)"
else
  set_var AZURE_OPENAI_API_KEY "$(az cognitiveservices account keys list \
    -g "$($TF resource_group_name)" -n "$($TF openai_name)" --query key1 -o tsv)"
fi
set_var AZURE_OPENAI_CHAT_DEPLOYMENT "$($TF chat_deployment)"
set_var AZURE_OPENAI_EMBEDDING_DEPLOYMENT "$($TF embedding_deployment)"
set_var AZURE_OPENAI_LIGERO_DEPLOYMENT "$($TF ligero_deployment)"

VS=$($TF vector_store)
[[ -z "$($TF search_endpoint)" && "$VS" == "azure_search" ]] && VS=qdrant # solo_modelos
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
[[ "$($TF alcance)" == "solo_modelos" ]] && set_var ALMACEN_DOCUMENTOS local
echo "$ENV_FILE actualizado (vector store: $VS). Para guardar originales en Blob: ALMACEN_DOCUMENTOS=blob y ejecuta el backend con 'uv run uvicorn app.main:app'."
