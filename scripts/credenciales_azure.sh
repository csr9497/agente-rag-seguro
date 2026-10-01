#!/usr/bin/env bash
# Crea las credenciales con las que GitHub Actions despliega en Azure y muestra qué guardar en
# los secrets del repositorio. Se ejecuta UNA vez en Azure Cloud Shell (portal.azure.com → icono
# >_ → Bash), donde ya tienes la sesión iniciada:
#
#   curl -sL https://raw.githubusercontent.com/csr9497/agente-rag-seguro/main/scripts/credenciales_azure.sh | bash
#
# Crea un service principal con rol Owner en la suscripción (Terraform crea recursos y asigna
# roles a las identidades de la app) y acceso al estado remoto de Terraform. El secreto solo se
# muestra en tu terminal: cópialo a GitHub y no lo guardes en ningún otro sitio.
set -euo pipefail

NOMBRE="${NOMBRE:-github-agente-rag-seguro}"
TFSTATE_RG="${TFSTATE_RG:-rg-ragseg-tfstate}"
TFSTATE_ACCOUNT="${TFSTATE_ACCOUNT:-stragsegtf08894c}"

SUB=$(az account show --query id -o tsv)
TENANT=$(az account show --query tenantId -o tsv)
echo "Suscripción: $(az account show --query name -o tsv) ($SUB) · estado $(az account show --query state -o tsv)"

if ! SP=$(az ad sp create-for-rbac --name "$NOMBRE" --role Owner \
  --scopes "/subscriptions/$SUB" --query "{id:appId, secreto:password}" -o tsv 2>/tmp/sp.err); then
  echo
  echo "⛔ No se pudo crear el service principal:"
  sed 's/^/   /' /tmp/sp.err
  echo
  echo "Si dice «Insufficient privileges», tu tenant no permite registrar aplicaciones."
  echo "Alternativa sin contraseñas (OIDC), en esta misma Cloud Shell:"
  echo "   git clone https://github.com/csr9497/agente-rag-seguro && cd agente-rag-seguro"
  echo "   SUBSCRIPTION_ID=$SUB GITHUB_REPO=csr9497/agente-rag-seguro ./infra/bootstrap/bootstrap.sh"
  echo "y copia las variables que imprime en GitHub (Settings → Environments → dev)."
  exit 1
fi
CLIENT_ID=$(cut -f1 <<< "$SP")
SECRETO=$(cut -f2 <<< "$SP")

# Estado remoto de Terraform: acceso con Entra ID (sin claves de la cuenta de almacenamiento).
if ESTADO=$(az storage account show -n "$TFSTATE_ACCOUNT" -g "$TFSTATE_RG" --query id -o tsv 2> /dev/null); then
  for _ in 1 2 3 4 5 6; do # el service principal tarda unos segundos en propagarse
    az role assignment create --assignee "$CLIENT_ID" --role "Storage Blob Data Contributor" \
      --scope "$ESTADO" -o none 2> /dev/null && break
    sleep 10
  done
else
  echo "⚠️  No existe la cuenta de estado $TFSTATE_ACCOUNT en $TFSTATE_RG: ejecuta antes infra/bootstrap/bootstrap.sh"
fi

cat << EOF

✅ Listo. En GitHub: repositorio → Settings → Secrets and variables → Actions →
   pestaña «Secrets» → New repository secret, crea estos cuatro:

   AZURE_CLIENT_ID        $CLIENT_ID
   AZURE_TENANT_ID        $TENANT
   AZURE_SUBSCRIPTION_ID  $SUB
   AZURE_CLIENT_SECRET    $SECRETO

   (El secreto caduca en 1 año; para renovarlo, vuelve a ejecutar este script.)
EOF
