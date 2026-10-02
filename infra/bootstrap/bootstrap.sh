#!/usr/bin/env bash
# Paso 0, una sola vez por suscripción (lo ejecuta una persona con Owner):
#   1. Storage para el estado remoto de Terraform (solo Entra ID, sin claves) y
#      infra/envs/dev/backend.hcl apuntando a él (make deploy lo usa).
#   2. Solo con GITHUB_REPO (CI/CD con GitHub Actions): identidad de despliegue (Managed
#      Identity con federated credential OIDC, sin secretos; no necesita registrar
#      aplicaciones), sus roles y las variables del environment en GitHub.
#      Sin GITHUB_REPO se despliega desde tu equipo con make deploy.
#
# Idempotente: si ya existe una cuenta de estado en TFSTATE_RG, la reutiliza.
#
# Uso (make bootstrap hace lo mismo):
#   ./infra/bootstrap/bootstrap.sh                          # solo despliegue desde tu equipo
#   GITHUB_REPO=owner/repo ./infra/bootstrap/bootstrap.sh   # además, CI/CD con GitHub Actions
#   SUBSCRIPTION_ID=...                                     # por defecto, la de `az account show`
set -euo pipefail
cd "$(dirname "$0")/../.."

SUBSCRIPTION_ID="${SUBSCRIPTION_ID:-$(az account show --query id -o tsv 2> /dev/null || true)}"
: "${SUBSCRIPTION_ID:?sin sesión en Azure: ejecuta az login (o define SUBSCRIPTION_ID)}"
GITHUB_REPO="${GITHUB_REPO:-}"
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

echo "==> infra/envs/dev/backend.hcl"
cat > infra/envs/dev/backend.hcl << EOF
# Estado remoto de Terraform (creado por infra/bootstrap/bootstrap.sh). No contiene secretos:
# el acceso es con Entra ID (az login / OIDC) y el rol Storage Blob Data Contributor.
resource_group_name  = "${TFSTATE_RG}"
storage_account_name = "${TFSTATE_ACCOUNT}"
container_name       = "${TFSTATE_CONTAINER}"
key                  = "dev/platform.tfstate"
use_azuread_auth     = true
EOF

if [[ -z $GITHUB_REPO ]]; then
  cat << EOF

Paso 0 completado: estado remoto en ${TFSTATE_RG}/${TFSTATE_ACCOUNT} e
infra/envs/dev/backend.hcl actualizado. Siguiente paso: make deploy
(Para CI/CD con GitHub Actions, repite con GITHUB_REPO=owner/repo.)
EOF
  exit 0
fi

echo "==> Identidad de despliegue (OIDC): $IDENTITY_NAME"
az identity create -n "$IDENTITY_NAME" -g "$TFSTATE_RG" -l "$LOCATION" -o none
CLIENT_ID=$(az identity show -n "$IDENTITY_NAME" -g "$TFSTATE_RG" --query clientId -o tsv)
PRINCIPAL_ID=$(az identity show -n "$IDENTITY_NAME" -g "$TFSTATE_RG" --query principalId -o tsv)

# GitHub firma el token con «repo:owner/repo:…» o, en repositorios nuevos, con los ids
# inmutables «repo:owner@<id>/repo@<id>:…»: se registran ambos (si falta el que GitHub usa,
# azure/login falla con AADSTS700213).
federar() { # federar <nombre> <sujeto>
  az identity federated-credential create --name "$1" \
    --identity-name "$IDENTITY_NAME" --resource-group "$TFSTATE_RG" \
    --issuer "https://token.actions.githubusercontent.com" --subject "$2" \
    --audiences "api://AzureADTokenExchange" -o none
}
REPO_JSON=$(gh api "repos/${GITHUB_REPO}" 2> /dev/null || curl -fsS "https://api.github.com/repos/${GITHUB_REPO}" || true)
REPO_IDS=$(REPO_JSON="$REPO_JSON" python3 - << 'PY'
import json, os
d = json.loads(os.environ.get("REPO_JSON") or "{}")
if "id" in d:
    print("%s@%s/%s@%s" % (d["owner"]["login"], d["owner"]["id"], d["name"], d["id"]))
PY
)
for ENTORNO_GH in ${ENTORNOS_GH:-dev staging main}; do
  federar "github-${ENTORNO_GH}" "repo:${GITHUB_REPO}:environment:${ENTORNO_GH}"
  if [[ -n $REPO_IDS ]]; then
    federar "github-${ENTORNO_GH}-ids" "repo:${REPO_IDS}:environment:${ENTORNO_GH}"
  else
    echo "⚠️  No se pudieron leer los ids de ${GITHUB_REPO} (gh o api.github.com): crea a mano la credencial con el sujeto que muestre el error AADSTS700213."
  fi
done

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
