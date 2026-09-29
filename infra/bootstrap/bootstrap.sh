#!/usr/bin/env bash
# Bootstrap de una sola vez (lo ejecuta una persona con Owner en la suscripción):
#   1. Storage para el estado remoto de Terraform (solo Entra ID, sin claves).
#   2. Identidad de despliegue para GitHub Actions: Managed Identity (user-assigned) con
#      federated credential (OIDC, sin secretos). No necesita permisos de directorio para
#      registrar aplicaciones (en tenants como Azure for Students no se tienen).
#   3. Roles del deployer y variables del environment en GitHub.
#
# Idempotente: si ya existe una cuenta de estado en TFSTATE_RG, la reutiliza.
#
# Uso:
#   SUBSCRIPTION_ID=... GITHUB_REPO=owner/repo ./infra/bootstrap/bootstrap.sh
set -euo pipefail

: "${SUBSCRIPTION_ID:?define SUBSCRIPTION_ID}"
: "${GITHUB_REPO:?define GITHUB_REPO (owner/repo)}"
LOCATION="${LOCATION:-eastus2}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
TFSTATE_RG="${TFSTATE_RG:-rg-ragseg-tfstate}"
TFSTATE_CONTAINER="${TFSTATE_CONTAINER:-tfstate}"
IDENTITY_NAME="${IDENTITY_NAME:-id-gh-ragseg-deployer-${ENVIRONMENT}}"

az account set --subscription "$SUBSCRIPTION_ID"
TENANT_ID=$(az account show --query tenantId -o tsv)

az group create -n "$TFSTATE_RG" -l "$LOCATION" -o none
TFSTATE_ACCOUNT="${TFSTATE_ACCOUNT:-$(az storage account list -g "$TFSTATE_RG" --query "[0].name" -o tsv)}"
TFSTATE_ACCOUNT="${TFSTATE_ACCOUNT:-stragsegtf$(openssl rand -hex 3)}"

echo "==> Estado remoto: $TFSTATE_RG / $TFSTATE_ACCOUNT"
az storage account create -n "$TFSTATE_ACCOUNT" -g "$TFSTATE_RG" -l "$LOCATION" \
  --sku Standard_LRS --min-tls-version TLS1_2 --allow-blob-public-access false \
  --allow-shared-key-access false -o none
az storage account blob-service-properties update -n "$TFSTATE_ACCOUNT" -g "$TFSTATE_RG" \
  --enable-versioning true -o none
TFSTATE_ID=$(az storage account show -n "$TFSTATE_ACCOUNT" -g "$TFSTATE_RG" --query id -o tsv)

# Quien ejecuta el bootstrap (y luego Terraform en local) necesita el rol de datos.
ME=$(az ad signed-in-user show --query id -o tsv)
az role assignment create --assignee-object-id "$ME" --assignee-principal-type User \
  --role "Storage Blob Data Contributor" --scope "$TFSTATE_ID" -o none
for i in $(seq 1 12); do
  az storage container create -n "$TFSTATE_CONTAINER" --account-name "$TFSTATE_ACCOUNT" \
    --auth-mode login -o none 2>/dev/null && break
  echo "   esperando a que se propague el rol de datos ($i/12)..."; sleep 15
done

echo "==> Identidad de despliegue (OIDC): $IDENTITY_NAME"
az identity create -n "$IDENTITY_NAME" -g "$TFSTATE_RG" -l "$LOCATION" -o none
CLIENT_ID=$(az identity show -n "$IDENTITY_NAME" -g "$TFSTATE_RG" --query clientId -o tsv)
PRINCIPAL_ID=$(az identity show -n "$IDENTITY_NAME" -g "$TFSTATE_RG" --query principalId -o tsv)

az identity federated-credential create --name "github-${ENVIRONMENT}" \
  --identity-name "$IDENTITY_NAME" --resource-group "$TFSTATE_RG" \
  --issuer "https://token.actions.githubusercontent.com" \
  --subject "repo:${GITHUB_REPO}:environment:${ENVIRONMENT}" \
  --audiences "api://AzureADTokenExchange" -o none

echo "==> Roles del deployer"
SCOPE="/subscriptions/${SUBSCRIPTION_ID}"
# Contributor para crear recursos; User Access Administrator porque Terraform asigna roles a
# las Managed Identities. En prod, sustituir por "Role Based Access Control Administrator"
# con condición que limite los roles asignables.
for ROLE in "Contributor" "User Access Administrator"; do
  az role assignment create --assignee-object-id "$PRINCIPAL_ID" \
    --assignee-principal-type ServicePrincipal --role "$ROLE" --scope "$SCOPE" -o none
done
az role assignment create --assignee-object-id "$PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal --role "Storage Blob Data Contributor" \
  --scope "$TFSTATE_ID" -o none

cat <<EOF

Bootstrap completado. Variables del environment '${ENVIRONMENT}' en GitHub:

  AZURE_CLIENT_ID=${CLIENT_ID}
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
    for KV in "AZURE_CLIENT_ID=${CLIENT_ID}" "AZURE_TENANT_ID=${TENANT_ID}" \
      "AZURE_SUBSCRIPTION_ID=${SUBSCRIPTION_ID}" "TFSTATE_RG=${TFSTATE_RG}" \
      "TFSTATE_ACCOUNT=${TFSTATE_ACCOUNT}" "TFSTATE_CONTAINER=${TFSTATE_CONTAINER}"; do
      gh variable set "${KV%%=*}" --env "$ENVIRONMENT" --repo "$GITHUB_REPO" --body "${KV#*=}"
    done
  fi
fi
