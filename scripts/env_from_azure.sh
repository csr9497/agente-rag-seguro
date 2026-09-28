#!/usr/bin/env bash
# Rellena .env para desarrollo local con los datos del stack platform desplegado.
# Requiere: az login, `terraform -chdir=infra/platform init` con el backend remoto y el rol
# "Key Vault Secrets User" (variable developer_principal_ids de Terraform).
set -euo pipefail

TF="terraform -chdir=infra/platform output -raw"
ENDPOINT=$($TF openai_endpoint)
KV=$($TF key_vault_name)
KEY=$(az keyvault secret show --vault-name "$KV" -n azure-openai-api-key --query value -o tsv)

[[ -f .env ]] || cp .env.example .env
set_var() { # set_var NOMBRE valor
  if grep -q "^$1=" .env; then sed -i.bak "s|^$1=.*|$1=$2|" .env && rm -f .env.bak
  else echo "$1=$2" >> .env; fi
}
set_var AZURE_OPENAI_ENDPOINT "$ENDPOINT"
set_var AZURE_OPENAI_API_KEY "$KEY"
set_var AZURE_OPENAI_CHAT_DEPLOYMENT "$($TF chat_deployment)"
set_var AZURE_OPENAI_EMBEDDING_DEPLOYMENT "$($TF embedding_deployment)"
echo ".env actualizado (endpoint: $ENDPOINT)"
