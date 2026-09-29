#!/usr/bin/env bash
# Entorno de desarrollo completo: modelos en Azure (solo gpt-4o y ada-002) + app, web, Qdrant
# y Redis en Docker + LangGraph Studio en el host. Todo escucha solo en 127.0.0.1.
#
#   ./scripts/entorno_local.sh levantar   # crea/actualiza lo necesario y muestra los accesos
#   ./scripts/entorno_local.sh accesos    # estado de cada servicio y sus URLs
#   ./scripts/entorno_local.sh apagar     # para Studio y Docker, elimina los modelos de Azure
#                                         # y quita endpoint y clave de .env
#
# Requisitos: az login, Docker Desktop abierto y el estado remoto de Terraform (bootstrap).
set -euo pipefail
cd "$(dirname "$0")/.."

TFVARS="../envs/dev/solo_modelos.tfvars"
STUDIO_PID=data/.studio.pid
STUDIO_LOG=data/studio.log
export ARM_SUBSCRIPTION_ID="${ARM_SUBSCRIPTION_ID:-$(az account show --query id -o tsv 2>/dev/null || true)}"

paso() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

esperar() { # esperar URL segundos
  for _ in $(seq 1 "$2"); do curl -sf "$1" > /dev/null 2>&1 && return 0; sleep 1; done
  return 1
}

studio_activo() { [[ -f "$STUDIO_PID" ]] && kill -0 "$(cat "$STUDIO_PID")" 2> /dev/null; }

levantar() {
  docker info > /dev/null 2>&1 || { echo "Docker no está en marcha: abre Docker Desktop y repite."; exit 1; }

  paso "1/5 Modelos en Azure (gpt-4o + text-embedding-ada-002)"
  terraform -chdir=infra/platform apply -input=false -auto-approve -var-file="$TFVARS" \
    | grep -E "Apply complete|No changes|Error" || true

  paso "2/5 .env con el endpoint y la clave de los modelos"
  ./scripts/env_from_azure.sh > /dev/null && echo "listo"

  paso "3/5 App, web, Qdrant y Redis en Docker"
  docker compose up --build -d 2>&1 | grep -E "Started|Running|Error" || true
  esperar http://localhost:8000/health 90 || { echo "La app no arrancó: docker compose logs app"; exit 1; }

  paso "4/5 Documentos de ejemplo (idempotente)"
  ./scripts/sembrar_local.sh

  paso "5/5 LangGraph Studio"
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

  paso "Modelos en Azure"
  terraform -chdir=infra/platform destroy -input=false -auto-approve -var-file="$TFVARS" \
    | grep -E "Destroy complete|Error" || true

  paso ".env sin endpoint ni clave"
  sed -i.bak -E '/^(AZURE_OPENAI_(ENDPOINT|API_KEY|CHAT_DEPLOYMENT|EMBEDDING_DEPLOYMENT)|VECTOR_STORE|AZURE_SEARCH_(ENDPOINT|API_KEY)|CONTENT_SAFETY_ENDPOINT|AZURE_STORAGE_(ACCOUNT_URL|CONTAINER)|ALMACEN_DOCUMENTOS)=/d' .env
  rm -f .env.bak
  echo "listo. Sin costes en Azure; tus datos locales siguen en data/ para la próxima vez."
}

estado() { # estado URL → "✅" o "⛔"
  curl -sf "$1" > /dev/null 2>&1 && printf '✅' || printf '⛔'
}

accesos() {
  paso "Accesos (solo desde este equipo)"
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
  levantar | apagar | accesos) "$1" ;;
  *) sed -n '2,11p' "$0"; exit 1 ;;
esac
