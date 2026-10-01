#!/usr/bin/env bash
# Despliegue completo en Azure con un comando: la app queda publicada con login de Entra ID.
#
#   ./scripts/nube.sh desplegar   # platform → identidad → imágenes → apps → documentos → URL
#   ./scripts/nube.sh estado      # URL, salud de las apps y última siembra
#   ./scripts/nube.sh destruir    # elimina todo lo desplegado (pide confirmación)
#
# Requisitos: Azure CLI con `az login` (suscripción activa y permisos de Owner, o Contributor +
# User Access Administrator), Terraform ≥ 1.9, Docker con buildx y el estado remoto de
# Terraform (infra/bootstrap, una vez).
#
# Login de la web (LOGIN_PROVEEDOR):
#   github (por defecto): OAuth App de GitHub en GH_OAUTH_CLIENT_ID y GH_OAUTH_CLIENT_SECRET
#     (variables de entorno, nunca en el repo); ADMINISTRADORES_GITHUB='["usuario"]'.
#   entra: app registration creada aquí (stack identidad); tu cuenta debe poder registrar apps.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

TFVARS="../envs/dev/nube.tfvars"
BACKEND="../envs/dev/backend.hcl"
LOG_DIR="data/nube"
source scripts/comun.sh
LOGIN_PROVEEDOR="${LOGIN_PROVIDER:-${LOGIN_PROVEEDOR:-}}" # vacío: github si hay OAuth App en .env; si no, ip

paso() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
falla() { printf '\n⛔ %s\n' "$1"; exit 1; }

leer_backend() { grep -E "^$1 " infra/envs/dev/backend.hcl | sed -E 's/.*= *"(.*)"/\1/'; }
estado_de() { # ubicación del estado de un stack, como objeto HCL para -var
  printf '{resource_group_name="%s",storage_account_name="%s",container_name="%s",key="dev/%s.tfstate"}' \
    "$(leer_backend resource_group_name)" "$(leer_backend storage_account_name)" \
    "$(leer_backend container_name)" "$1"
}
tf() { local stack=$1; shift; terraform -chdir="infra/$stack" "$@"; }
init() { tf "$1" init -input=false -reconfigure -backend-config="$BACKEND" -backend-config="key=dev/$1.tfstate" > /dev/null; }
aplicar() { # aplicar <stack> <args...>: muestra el resumen y deja el log completo
  local stack=$1; shift
  mkdir -p "$LOG_DIR"
  if ! tf "$stack" apply -input=false -auto-approve "$@" > "$LOG_DIR/$stack.log" 2>&1; then
    grep -E "Error|│" "$LOG_DIR/$stack.log" | head -30
    return 1
  fi
  grep -E "Apply complete|No changes" "$LOG_DIR/$stack.log" | tail -1
}

requisitos() {
  paso "Requisitos"
  local falta=0 cmd
  for cmd in az terraform docker git; do
    if command -v "$cmd" > /dev/null; then echo "✅ $cmd"; else echo "⛔ $cmd no está instalado"; falta=1; fi
  done
  [[ $falta == 0 ]] || falla "Instala lo que falta (Azure CLI: https://learn.microsoft.com/cli/azure/install-azure-cli)."
  docker info > /dev/null 2>&1 && echo "✅ Docker en marcha" || falla "Abre Docker Desktop y repite."
  docker buildx version > /dev/null 2>&1 && echo "✅ docker buildx" || falla "Falta docker buildx (incluido en Docker Desktop)."
  az account show > /dev/null 2>&1 || falla "Sin sesión en Azure: ejecuta az login (y az account set -s <suscripción>)."
  local estado
  estado=$(az account show --query state -o tsv)
  echo "✅ az login: $(az account show --query user.name -o tsv)"
  [[ $estado == Enabled ]] || falla "La suscripción está en estado $estado (crédito agotado o deshabilitada)."
  echo "✅ suscripción: $(az account show --query name -o tsv) (activa)"
  [[ -f infra/envs/dev/backend.hcl ]] || falla "Falta el estado remoto: ejecuta infra/bootstrap/bootstrap.sh una vez."
  export ARM_SUBSCRIPTION_ID
  ARM_SUBSCRIPTION_ID=$(az account show --query id -o tsv)
}

requisitos_login() {
  local var # también desde .env (no se sube a git): GH_OAUTH_CLIENT_ID=… / GH_OAUTH_CLIENT_SECRET=…
  for var in GH_OAUTH_CLIENT_ID GH_OAUTH_CLIENT_SECRET ADMINISTRADORES_GITHUB LANGSMITH_API_KEY; do
    if [[ -z ${!var:-} && -f .env ]] && grep -qE "^$var=.+" .env; then
      export "$var=$(grep -E "^$var=" .env | tail -1 | cut -d= -f2- | sed -E "s/^['\"]//; s/['\"]$//")"
    fi
  done
  # Trazas en LangSmith (modo enmascarado en la nube): la clave va a Key Vault.
  if [[ -n ${LANGSMITH_API_KEY:-} ]]; then
    export TF_VAR_langsmith_api_key=$LANGSMITH_API_KEY
    echo "✅ trazas en LangSmith (proyecto agente-rag-ragseg-dev, enmascaradas)"
  else
    echo "ℹ️  sin LANGSMITH_API_KEY en .env: la app en la nube no enviará trazas a LangSmith"
  fi
  if [[ -z $LOGIN_PROVEEDOR ]]; then
    if [[ -n ${GH_OAUTH_CLIENT_ID:-} ]]; then LOGIN_PROVEEDOR=github; else LOGIN_PROVEEDOR=ip; fi
  fi
  if [[ $LOGIN_PROVEEDOR == ip ]]; then
    # Prueba sin login: la web solo acepta tu IP pública (el resto de Internet recibe 403).
    local ip
    ip=$(curl -s -m 10 https://api.ipify.org || true)
    [[ $ip =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || falla "No se pudo averiguar tu IP pública (api.ipify.org)."
    export TF_VAR_ips_permitidas="${ALLOWED_IPS:-${IPS_PERMITIDAS:-[\"$ip/32\"]}}"
    echo "✅ modo prueba sin login: la web solo será accesible desde $TF_VAR_ips_permitidas"
    echo "   (para abrirla a otras personas con login de GitHub: GH_OAUTH_CLIENT_ID/SECRET en .env)"
  fi
  if [[ $LOGIN_PROVEEDOR == github ]]; then
    [[ -n ${GH_OAUTH_CLIENT_ID:-} && -n ${GH_OAUTH_CLIENT_SECRET:-} ]] \
      || falla "Login con GitHub: añade GH_OAUTH_CLIENT_ID y GH_OAUTH_CLIENT_SECRET a .env (OAuth App de GitHub, ver docs/despliegue.md)."
    export TF_VAR_github_oauth_client_id=$GH_OAUTH_CLIENT_ID TF_VAR_github_oauth_client_secret=$GH_OAUTH_CLIENT_SECRET
    export TF_VAR_administradores="${ADMINISTRADORES_GITHUB:-[\"$(git remote get-url origin | sed -E 's#.*[:/]([^/]+)/[^/]+$#\1#')\"]}"
    echo "✅ login con GitHub · administradores: $TF_VAR_administradores"
  fi
}

# Azure for Students y otras suscripciones restringen PostgreSQL Flexible por región: se busca
# la primera región que lo admita (versión 16, sin restricción). Si no es la de la VNet,
# Terraform lo crea con acceso público limitado a servicios de Azure (TLS).
region_postgres() {
  local base r
  base=$(sed -nE 's/^location *= *"(.*)"/\1/p' infra/envs/dev/nube.tfvars)
  for r in ${POSTGRES_LOCATION:-} "$base" southcentralus centralus eastus westus2 westus3 northcentralus canadacentral; do
    if az postgres flexible-server list-skus --location "$r" \
      --query "[?restricted!='Enabled'].supportedServerVersions[].name" -o tsv 2> /dev/null | grep -qx 16; then
      echo "$r"
      return 0
    fi
  done
  return 1
}

esperar_job() { # esperar_job <job> <grupo>: hasta Succeeded/Failed (10 min)
  local estado=""
  for _ in $(seq 1 60); do
    estado=$(az containerapp job execution list -n "$1" -g "$2" --query "[0].properties.status" -o tsv 2> /dev/null || true)
    [[ $estado == Succeeded || $estado == Failed ]] && break
    sleep 10
  done
  echo "$estado"
}

desplegar() {
  requisitos
  requisitos_login
  local yo plataforma identidad tag acr login
  yo=$(az ad signed-in-user show --query id -o tsv)
  plataforma=$(estado_de platform)
  identidad=$(estado_de identidad)

  paso "1/6 Infraestructura (modelos, AI Search, Storage, Key Vault, PostgreSQL, Container Apps)"
  local pg
  pg=$(region_postgres) || falla "Tu suscripción no puede crear PostgreSQL Flexible en ninguna región probada (define POSTGRES_LOCATION=<región> y repite)."
  echo "PostgreSQL en $pg"
  init platform
  aplicar platform -var-file="$TFVARS" -var "developer_principal_ids=[\"$yo\"]" -var "postgres_location=$pg" \
    || falla "Falló platform (log: $LOG_DIR/platform.log)."

  if [[ $LOGIN_PROVEEDOR == github ]]; then
    paso "2/6 Login con GitHub (OAuth App): no hace falta registrar nada en Entra ID"
  elif [[ $LOGIN_PROVEEDOR == ip ]]; then
    paso "2/6 Modo prueba sin login (solo tu IP): no hace falta configurar login"
  else
  paso "2/6 Identidad en Entra ID (app registration, roles y login)"
  init identidad
  if ! aplicar identidad -var-file=../envs/dev/identidad.tfvars -var "platform_state=$plataforma"; then
    if grep -q "Authorization_RequestDenied\|Insufficient privileges" "$LOG_DIR/identidad.log"; then
      falla "Tu cuenta no puede registrar aplicaciones en este tenant. Pide el rol «Application Developer» o usa una cuenta con permisos (ver docs/despliegue.md)."
    fi
    # Key Vault tarda en propagar los permisos recién asignados: un reintento basta.
    echo "   reintentando en 60 s…"; sleep 60
    aplicar identidad -var-file=../envs/dev/identidad.tfvars -var "platform_state=$plataforma" || falla "Falló identidad (log: $LOG_DIR/identidad.log)."
  fi
  fi

  paso "3/6 Imágenes (linux/amd64) en Azure Container Registry"
  acr=$(tf platform output -raw acr_name)
  login=$(tf platform output -raw acr_login_server)
  tag=$(git rev-parse --short HEAD)
  [[ -z $(git status --porcelain) ]] || tag="$tag-$(date +%Y%m%d%H%M%S)" # con cambios sin commit
  az acr login -n "$acr" > /dev/null
  docker buildx build --platform linux/amd64 -t "$login/backend:$tag" --push . > "$LOG_DIR/imagen-backend.log" 2>&1 \
    || falla "Falló la imagen del backend (log: $LOG_DIR/imagen-backend.log)."
  docker buildx build --platform linux/amd64 -t "$login/web:$tag" --push web > "$LOG_DIR/imagen-web.log" 2>&1 \
    || falla "Falló la imagen de la web (log: $LOG_DIR/imagen-web.log)."
  echo "backend:$tag · web:$tag"

  if [[ -n ${LANGSMITH_API_KEY:-} ]]; then
    echo "Prompts en LangSmith (la app de la nube usa la etiqueta prod):"
    publicar_prompts dev prod
  fi

  paso "4/6 Aplicaciones (web con login, backend interno, jobs de ingesta)"
  init apps
  aplicar apps -var-file=../envs/dev/apps.tfvars \
    -var "backend_image=$login/backend:$tag" -var "web_image=$login/web:$tag" \
    -var "app_version=$tag" -var "platform_state=$plataforma" -var "login_proveedor=$LOGIN_PROVEEDOR" \
    $([[ $LOGIN_PROVEEDOR == entra ]] && echo "-var identidad_state=$identidad") \
    || falla "Falló apps (log: $LOG_DIR/apps.log)."

  paso "5/6 Documentos de ejemplo (registro + Blob + índice; idempotente)"
  local grupo job resultado
  grupo=$(tf apps output -raw resource_group_name)
  job=$(tf apps output -raw sembrar_job_name)
  az containerapp job start -n "$job" -g "$grupo" -o none
  resultado=$(esperar_job "$job" "$grupo")
  [[ $resultado == Succeeded ]] && echo "✅ documentos cargados" \
    || echo "⚠️  siembra: ${resultado:-sin estado}. Logs: az containerapp job logs show -n $job -g $grupo"

  paso "6/6 Comprobación"
  comprobar

  paso "Tu equipo contra la nube: app y LangGraph Studio (make cloud-local)"
  ./scripts/local_nube.sh || echo "⚠️  no arrancó; repítelo con make cloud-local"
}

comprobar() {
  local url grupo codigo app salud
  url=$(tf apps output -raw web_url)
  grupo=$(tf apps output -raw resource_group_name)
  for app in "$(tf apps output -raw backend_app_name)" "$(tf apps output -raw web_app_name)"; do
    salud=$(az containerapp revision list -n "$app" -g "$grupo" \
      --query "[?properties.active].properties.healthState | [0]" -o tsv 2> /dev/null || true)
    [[ $salud == Healthy ]] && echo "✅ $app: Healthy" || echo "⚠️  $app: ${salud:-desconocido} (az containerapp logs show -n $app -g $grupo)"
  done
  local esperado="^(302|401)$" texto="la web pide inicio de sesión"
  if [[ $(tf apps output -raw login_proveedor 2> /dev/null) == ip ]]; then
    esperado="^200$" texto="la web responde desde tu IP"
  fi
  codigo=""
  for _ in $(seq 1 30); do
    codigo=$(curl -s -o /dev/null -w "%{http_code}" "$url/" || true)
    [[ $codigo =~ $esperado ]] && break
    sleep 10
  done
  if [[ $codigo =~ $esperado ]]; then echo "✅ $texto ($codigo)"
  else echo "⚠️  la web responde $codigo (puede tardar unos minutos en arrancar)"; fi
  echo
  echo "   Aplicación: $url"
  if [[ $(tf apps output -raw login_proveedor 2> /dev/null) == ip ]]; then
    echo "   Modo prueba: sin login, solo desde tu IP; eliges el rol en la web como en local."
  else
    echo "   Callback de la OAuth App de GitHub: $url/.auth/login/github/callback"
    echo "   Inicia sesión: los administradores tienen todos los roles y asignan roles a otras"
    echo "   personas desde la app (Roles y permisos → Personas y sus roles)."
  fi
  enlace_langsmith agente-rag-ragseg-dev
  echo "   Estado: make cloud-status · Eliminar todo: make cloud-destroy"
}

estado() {
  requisitos > /dev/null
  init apps
  paso "Despliegue en la nube"
  comprobar
}

destruir() {
  requisitos
  if [[ ${CONFIRM:-${CONFIRMAR:-}} != yes && ${CONFIRMAR:-} != si ]]; then
    read -r -p "Se eliminará TODO lo desplegado en Azure (app, datos, modelos). Escribe 'destruir': " r
    [[ $r == destruir ]] || falla "Cancelado."
  fi
  local plataforma identidad
  plataforma=$(estado_de platform)
  identidad=$(estado_de identidad)
  ./scripts/local_nube.sh parar > /dev/null 2>&1 || true
  paso "1/3 Aplicaciones"
  # Al destruir no se usan: valores de relleno para las validaciones del stack.
  export TF_VAR_github_oauth_client_id=x TF_VAR_github_oauth_client_secret=x TF_VAR_administradores='["x"]' TF_VAR_ips_permitidas='["0.0.0.0/32"]'
  LOGIN_PROVEEDOR=${LOGIN_PROVEEDOR:-github}
  init apps
  tf apps destroy -input=false -auto-approve -var-file=../envs/dev/apps.tfvars \
    -var "backend_image=x" -var "web_image=x" \
    -var "platform_state=$plataforma" -var "login_proveedor=$LOGIN_PROVEEDOR" \
    $([[ $LOGIN_PROVEEDOR == entra ]] && echo "-var identidad_state=$identidad") | grep -E "Destroy complete|Error" || true
  paso "2/3 Identidad en Entra ID"
  [[ $LOGIN_PROVEEDOR == entra ]] && init identidad && tf identidad destroy -input=false -auto-approve -var-file=../envs/dev/identidad.tfvars -var "platform_state=$plataforma" | grep -E "Destroy complete|Error" || true
  paso "3/3 Infraestructura"
  init platform
  tf platform destroy -input=false -auto-approve -var-file="$TFVARS" \
    -var "postgres_location=$(region_postgres || true)" \
    -var "developer_principal_ids=[\"$(az ad signed-in-user show --query id -o tsv)\"]" | grep -E "Destroy complete|Error" || true
  echo; echo "listo. Sin recursos en Azure."
}

# `source scripts/nube.sh --solo-funciones` (CI): carga las funciones sin ejecutar nada.
[[ ${1:-} == --solo-funciones ]] && return 0

case "${1:-}" in
  desplegar | estado | destruir) "$1" ;;
  *) sed -n '2,10p' "$0"; exit 1 ;;
esac
