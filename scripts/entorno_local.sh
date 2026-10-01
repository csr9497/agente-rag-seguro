#!/usr/bin/env bash
# Entorno de desarrollo completo: modelos (Azure OpenAI u OpenAI/compatible, según
# MODELOS_PROVEEDOR en .env) + app, web, Qdrant y Redis en Docker + LangGraph Studio en el
# host. Todo escucha solo en 127.0.0.1.
#
#   ./scripts/entorno_local.sh instalar   # comprueba requisitos, dependencias, .env y Terraform
#   ./scripts/entorno_local.sh levantar   # crea/actualiza lo necesario y muestra los accesos
#   ./scripts/entorno_local.sh accesos    # estado de cada servicio y sus URLs
#   ./scripts/entorno_local.sh apagar     # para Studio y Docker, elimina los modelos de Azure
#                                         # y quita endpoint y clave de .env
#
# Requisitos comunes: uv y Docker Desktop abierto.
#   MODELOS_PROVEEDOR=azure (por defecto): Azure CLI con `az login`, suscripción activa,
#     Terraform y el estado remoto (bootstrap). levantar/apagar crean y borran los modelos.
#   MODELOS_PROVEEDOR=openai: OPENAI_API_KEY (y OPENAI_BASE_URL si no es OpenAI) en .env.
#     No se toca Azure.
set -euo pipefail
cd "$(dirname "$0")/.."

# Variable de entorno > .env > azure.
PROVEEDOR="${MODELOS_PROVEEDOR:-$(grep -E '^MODELOS_PROVEEDOR=' .env 2> /dev/null | tail -1 | cut -d= -f2- | tr -d '"' || true)}"
PROVEEDOR="${PROVEEDOR:-azure}"

TFVARS="../envs/dev/solo_modelos.tfvars"
STUDIO_PID=data/.studio.pid
STUDIO_LOG=data/studio.log
if [[ $PROVEEDOR == azure ]]; then
  export ARM_SUBSCRIPTION_ID="${ARM_SUBSCRIPTION_ID:-$(az account show --query id -o tsv 2>/dev/null || true)}"
fi

source scripts/comun.sh
paso() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

terraform_init() { # idempotente; el backend remoto está en infra/envs/dev/backend.hcl
  terraform -chdir=infra/platform init -input=false -reconfigure \
    -backend-config=../envs/dev/backend.hcl > /dev/null
}

yo() { az ad signed-in-user show --query id -o tsv 2>/dev/null; }

requisitos_azure() { # devuelve 1 si falta algo
  local falta=0 estado
  for cmd in az terraform; do
    if command -v "$cmd" > /dev/null; then echo "✅ $cmd"; else echo "⛔ $cmd no está instalado"; falta=1; fi
  done
  command -v az > /dev/null || { echo "   → https://learn.microsoft.com/cli/azure/install-azure-cli"; return 1; }
  if ! az account show > /dev/null 2>&1; then
    echo "⛔ sin sesión en Azure: ejecuta az login (y az account set -s <suscripción>)"; return 1
  fi
  estado=$(az account show --query state -o tsv)
  echo "✅ az login: $(az account show --query user.name -o tsv)"
  if [[ $estado == Enabled ]]; then echo "✅ suscripción: $(az account show --query name -o tsv) (activa)"
  else echo "⛔ suscripción $(az account show --query name -o tsv) en estado $estado: crédito agotado o deshabilitada"; falta=1; fi
  return $falta
}

instalar() {
  paso "Requisitos (MODELOS_PROVEEDOR=$PROVEEDOR)"
  local falta=0
  for cmd in uv docker; do
    if command -v "$cmd" > /dev/null; then echo "✅ $cmd"; else echo "⛔ $cmd no está instalado"; falta=1; fi
  done
  docker info > /dev/null 2>&1 && echo "✅ Docker en marcha" || { echo "⛔ abre Docker Desktop"; falta=1; }
  if [[ $PROVEEDOR == azure ]]; then requisitos_azure || falta=1; fi
  [[ $falta == 0 ]] || { echo; echo "Resuelve lo marcado con ⛔ y repite: make instalar"; exit 1; }

  paso "Dependencias de Python (uv, Python 3.12)"
  uv sync --frozen 2>&1 | tail -1

  paso ".env"
  if [[ -f .env ]]; then echo "ya existe"; else cp .env.example .env && echo "creado desde .env.example"; fi
  if ! grep -q '^POSTGRES_PASSWORD=.\+' .env; then # la pide docker-compose (perfil postgres)
    sed -i.bak '/^POSTGRES_PASSWORD=/d' .env && rm -f .env.bak
    echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)" >> .env
    echo "✅ POSTGRES_PASSWORD local generada"
  fi
  grep -q '^LANGSMITH_API_KEY=.\+' .env && echo "✅ LANGSMITH_API_KEY definida" \
    || echo "ℹ️  opcional: añade LANGSMITH_API_KEY en .env para trazas y evaluaciones en LangSmith"

  if [[ $PROVEEDOR == azure ]]; then
    paso "Terraform (estado remoto)"
    terraform_init && echo "listo"
  else
    paso "Modelos (OpenAI o endpoint compatible)"
    grep -qE '^OPENAI_(API_KEY|BASE_URL)=.+' .env && echo "✅ OPENAI_API_KEY / OPENAI_BASE_URL en .env" \
      || echo "⛔ define OPENAI_API_KEY (y OPENAI_BASE_URL si no es OpenAI) en .env"
  fi
  mkdir -p data
  echo; echo "Instalación completa. Siguiente paso: make verificar-modelos y make levantar"
}

esperar() { # esperar URL segundos
  for _ in $(seq 1 "$2"); do curl -sf "$1" > /dev/null 2>&1 && return 0; sleep 1; done
  return 1
}

# Con la nube desplegada, local usa sus modelos (Azure OpenAI) y nada más: búsqueda, Blob y
# Content Safety siguen siendo los locales (Qdrant, data/).
modelos_de_la_nube() {
  local tmp=data/.modelos-nube.env linea
  rm -f "$tmp"
  ENV_FILE="$tmp" ./scripts/env_from_azure.sh > /dev/null
  sed -i.bak -E '/^(AZURE_OPENAI_(ENDPOINT|API_KEY|CHAT_DEPLOYMENT|EMBEDDING_DEPLOYMENT|LIGERO_DEPLOYMENT)|VECTOR_STORE|AZURE_SEARCH_(ENDPOINT|API_KEY)|CONTENT_SAFETY_ENDPOINT|AZURE_STORAGE_(ACCOUNT_URL|CONTAINER)|ALMACEN_DOCUMENTOS)=/d' .env
  rm -f .env.bak
  grep -E '^AZURE_OPENAI_(ENDPOINT|API_KEY|CHAT_DEPLOYMENT|EMBEDDING_DEPLOYMENT|LIGERO_DEPLOYMENT)=' "$tmp" >> .env
  rm -f "$tmp"
}

studio_activo() { [[ -f "$STUDIO_PID" ]] && kill -0 "$(cat "$STUDIO_PID")" 2> /dev/null; }

levantar() {
  docker info > /dev/null 2>&1 || { echo "Docker no está en marcha: abre Docker Desktop y repite."; exit 1; }

  mkdir -p data
  if [[ $PROVEEDOR == azure ]]; then
    paso "1/5 Modelos en Azure (gpt-4o + text-embedding-ada-002)"
    requisitos_azure > /dev/null || { requisitos_azure; exit 1; }
    [[ -d infra/platform/.terraform ]] || terraform_init
    if hay_nube; then
      echo "Hay un despliegue en la nube (make desplegar): se usan sus modelos, no se crea nada."
      paso "2/5 .env con el endpoint y la clave de los modelos de la nube (el resto, local)"
      modelos_de_la_nube && echo "listo"
    else
      terraform -chdir=infra/platform apply -input=false -auto-approve -var-file="$TFVARS" \
        -var "developer_principal_ids=[\"$(yo)\"]" \
        | grep -E "Apply complete|No changes|Error" || true

      paso "2/5 .env con el endpoint y la clave de los modelos"
      ./scripts/env_from_azure.sh > /dev/null && echo "listo"
    fi
  else
    paso "1-2/5 Modelos: OpenAI o endpoint compatible (no se crea nada en Azure)"
  fi

  paso "Verificación de los modelos (credenciales, saldo, modelos y capacidades)"
  uv run python -m app.modelos.diagnostico \
    || { echo; echo "Los modelos no están listos: corrige lo anterior y repite make levantar."; exit 1; }

  paso "3/5 App, web, Qdrant y Redis en Docker"
  docker compose up --build -d 2>&1 | grep -E "Started|Running|Error" || true
  esperar http://localhost:8000/health 90 || { echo "La app no arrancó: docker compose logs app"; exit 1; }

  paso "4/5 Documentos de ejemplo (idempotente)"
  ./scripts/sembrar_local.sh

  paso "5/5 LangGraph Studio (y prompts en LangSmith)"
  publicar_prompts
  if studio_activo || curl -sf http://127.0.0.1:2024/ok > /dev/null 2>&1; then
    echo "ya estaba en marcha"
  else
    mkdir -p data
    nohup uv run langgraph dev --allow-blocking --no-browser --port 2024 > "$STUDIO_LOG" 2>&1 &
    echo $! > "$STUDIO_PID"
    esperar http://127.0.0.1:2024/ok 90 && echo "listo" || echo "no arrancó: revisa $STUDIO_LOG"
  fi
  accesos
}

apagar() {
  paso "LangGraph Studio"
  if studio_activo; then kill "$(cat "$STUDIO_PID")" && echo "detenido"; else echo "no estaba en marcha"; fi
  rm -f "$STUDIO_PID"
  pkill -f "langgraph dev" 2> /dev/null || true

  paso "Docker (app, web, Qdrant, Redis)"
  docker compose down 2>&1 | grep -E "Removed|Stopped" | tail -4 || true

  if [[ $PROVEEDOR != azure ]]; then
    echo; echo "listo (MODELOS_PROVEEDOR=$PROVEEDOR: no hay recursos de Azure que borrar)."
    return
  fi
  paso "Modelos en Azure"
  [[ -d infra/platform/.terraform ]] || terraform_init
  if hay_nube; then
    echo "Son los del despliegue en la nube: se mantienen (make destruir-nube los elimina)."
    echo; echo "listo."
    return
  fi
  terraform -chdir=infra/platform destroy -input=false -auto-approve -var-file="$TFVARS" \
    -var "developer_principal_ids=[\"$(yo)\"]" \
    | grep -E "Destroy complete|Error" || true

  paso ".env sin endpoint ni clave"
  sed -i.bak -E '/^(AZURE_OPENAI_(ENDPOINT|API_KEY|CHAT_DEPLOYMENT|EMBEDDING_DEPLOYMENT|LIGERO_DEPLOYMENT)|VECTOR_STORE|AZURE_SEARCH_(ENDPOINT|API_KEY)|CONTENT_SAFETY_ENDPOINT|AZURE_STORAGE_(ACCOUNT_URL|CONTAINER)|ALMACEN_DOCUMENTOS)=/d' .env
  rm -f .env.bak
  echo "listo. Sin costes en Azure; tus datos locales siguen en data/ para la próxima vez."
}

estado() { # estado URL → "✅" o "⛔"
  curl -sf "$1" > /dev/null 2>&1 && printf '✅' || printf '⛔'
}

accesos() {
  paso "Accesos (solo desde este equipo)"
  if [[ -n ${SSH_CONNECTION:-} ]]; then # servidor remoto: túnel SSH (sin login no se expone a la red)
    printf '   Estás en un servidor remoto: abre la app desde tu equipo con un túnel SSH y usa las
'
    printf '   mismas URLs (localhost):
'
    printf '     ssh -N -L 8080:localhost:8080 -L 8000:localhost:8000 -L 2024:127.0.0.1:2024 %s@%s

' \
      "$(whoami)" "$(awk '{print $3}' <<< "$SSH_CONNECTION")"
  fi
  printf '%s  Aplicación web ............ http://localhost:8080\n' "$(estado http://localhost:8080/)"
  printf '%s  API (docs interactivos) ... http://localhost:8000/docs\n' "$(estado http://localhost:8000/health)"
  printf '%s  Estado de la app .......... http://localhost:8000/ready\n' "$(estado http://localhost:8000/ready)"
  printf '%s  Topología del grafo ....... http://localhost:8000/grafo\n' "$(estado http://localhost:8000/grafo)"
  printf '%s  LangGraph Studio (Chrome) . https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024\n' \
    "$(estado http://127.0.0.1:2024/ok)"
  printf '%s  Qdrant (índice) ........... http://localhost:6333/dashboard\n' "$(estado http://localhost:6333/)"
  uv run python - << 'PY' 2> /dev/null || echo "   LangSmith: https://smith.langchain.com (define LANGSMITH_API_KEY en .env)"
from app.config import Settings
from evals.langsmith import cliente_langsmith
s = Settings()
c = cliente_langsmith()
p = c.read_project(project_name=s.langsmith_project)
d = c.read_dataset(dataset_name="matriz-escenarios")
base = f"https://smith.langchain.com/o/{p.tenant_id}"
print(f"✅  LangSmith · trazas ....... {base}/projects/p/{p.id}  (TRAZAS_MODO={s.trazas_modo})")
print(f"✅  LangSmith · evaluaciones . {base}/datasets/{d.id}")
PY
  cat << 'EOF'

   Roles para probar: Empleado general · Recursos Humanos · Finanzas · Administrador
   Evaluación automática:  make evals-langsmith BASE_URL=http://localhost:8000
   Apagar todo:            make apagar
EOF
}

case "${1:-}" in
  instalar | levantar | apagar | accesos) "$1" ;;
  *) sed -n '2,12p' "$0"; exit 1 ;;
esac
