#!/usr/bin/env bash
# Bootstrap de una sola vez (lo ejecuta una persona con Owner en la suscripción):
#   1. Storage para el estado remoto de Terraform (solo Entra ID, sin claves).
#   2. App registration + federated credential para GitHub Actions (OIDC, sin secretos).
#   3. Roles del deployer y variables del repositorio en GitHub.
#
# Uso:
#   SUBSCRIPTION_ID=... GITHUB_REPO=owner/repo ./infra/bootstrap/bootstrap.sh
set -euo pipefail

: "${SUBSCRIPTION_ID:?define SUBSCRIPTION_ID}"
: "${GITHUB_REPO:?define GITHUB_REPO (owner/repo)}"
LOCATION="${LOCATION:-eastus2}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
TFSTATE_RG="${TFSTATE_RG:-rg-ragseg-tfstate}"
TFSTATE_ACCOUNT="${TFSTATE_ACCOUNT:-stragsegtf$(openssl rand -hex 3)}"
TFSTATE_CONTAINER="${TFSTATE_CONTAINER:-tfstate}"
APP_NAME="${APP_NAME:-gh-ragseg-deployer-${ENVIRONMENT}}"

az account set --subscription "$SUBSCRIPTION_ID"
TENANT_ID=$(az account show --query tenantId -o tsv)

echo "==> Estado remoto: $TFSTATE_RG / $TFSTATE_ACCOUNT"
az group create -n "$TFSTATE_RG" -l "$LOCATION" -o none
az storage account create -n "$TFSTATE_ACCOUNT" -g "$TFSTATE_RG" -l "$LOCATION" \
  --sku Standard_LRS --min-tls-version TLS1_2 --allow-blob-public-access false \
  --allow-shared-key-access false -o none
az storage account blob-service-properties update -n "$TFSTATE_ACCOUNT" -g "$TFSTATE_RG" \
  --enable-versioning true -o none
az storage container create -n "$TFSTATE_CONTAINER" --account-name "$TFSTATE_ACCOUNT" \
  --auth-mode login -o none || {
  # Recién creada la cuenta, el rol de datos del propio usuario puede no existir aún.
  ME=$(az ad signed-in-user show --query id -o tsv)
  az role assignment create --assignee "$ME" --role "Storage Blob Data Contributor" \
    --scope "$(az storage account show -n "$TFSTATE_ACCOUNT" -g "$TFSTATE_RG" --query id -o tsv)" -o none
  sleep 60
  az storage container create -n "$TFSTATE_CONTAINER" --account-name "$TFSTATE_ACCOUNT" \
    --auth-mode login -o none
}

echo "==> Identidad de despliegue (OIDC): $APP_NAME"
APP_ID=$(az ad app list --display-name "$APP_NAME" --query "[0].appId" -o tsv)
if [[ -z "$APP_ID" ]]; then
  APP_ID=$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)
  az ad sp create --id "$APP_ID" -o none
fi
SP_ID=$(az ad sp show --id "$APP_ID" --query id -o tsv)

az ad app federated-credential create --id "$APP_ID" --parameters "{
  \"name\": \"github-${ENVIRONMENT}\",
  \"issuer\": \"https://token.actions.githubusercontent.com\",
  \"subject\": \"repo:${GITHUB_REPO}:environment:${ENVIRONMENT}\",
  \"audiences\": [\"api://AzureADTokenExchange\"]
}" -o none 2>/dev/null || echo "   (federated credential ya existía)"

echo "==> Roles del deployer"
SCOPE="/subscriptions/${SUBSCRIPTION_ID}"
# Contributor para crear recursos; User Access Administrator porque Terraform asigna roles a
# las Managed Identities. En prod, sustituir por "Role Based Access Control Administrator"
# con condición que limite los roles asignables.
for ROLE in "Contributor" "User Access Administrator"; do
  az role assignment create --assignee-object-id "$SP_ID" --assignee-principal-type ServicePrincipal \
    --role "$ROLE" --scope "$SCOPE" -o none
done
az role assignment create --assignee-object-id "$SP_ID" --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Contributor" \
  --scope "$(az storage account show -n "$TFSTATE_ACCOUNT" -g "$TFSTATE_RG" --query id -o tsv)" -o none

cat <<EOF

Bootstrap completado. Variables del environment '${ENVIRONMENT}' en GitHub:

  AZURE_CLIENT_ID=${APP_ID}
  AZURE_TENANT_ID=${TENANT_ID}
  AZURE_SUBSCRIPTION_ID=${SUBSCRIPTION_ID}
  TFSTATE_RG=${TFSTATE_RG}
  TFSTATE_ACCOUNT=${TFSTATE_ACCOUNT}
  TFSTATE_CONTAINER=${TFSTATE_CONTAINER}
EOF

if command -v gh >/dev/null; then
  read -r -p "¿Crearlas ahora con gh en ${GITHUB_REPO}? [s/N] " RESP
  if [[ "$RESP" =~ ^[sS]$ ]]; then
    gh api -X PUT "repos/${GITHUB_REPO}/environments/${ENVIRONMENT}" >/dev/null
    for KV in "AZURE_CLIENT_ID=${APP_ID}" "AZURE_TENANT_ID=${TENANT_ID}" \
      "AZURE_SUBSCRIPTION_ID=${SUBSCRIPTION_ID}" "TFSTATE_RG=${TFSTATE_RG}" \
      "TFSTATE_ACCOUNT=${TFSTATE_ACCOUNT}" "TFSTATE_CONTAINER=${TFSTATE_CONTAINER}"; do
      gh variable set "${KV%%=*}" --env "$ENVIRONMENT" --repo "$GITHUB_REPO" --body "${KV#*=}"
    done
  fi
fi
