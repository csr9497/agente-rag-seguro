#!/usr/bin/env bash
# App en tu equipo contra los recursos de la nube (lo creado por make deploy o la etapa A):
# modelos de Azure OpenAI, AI Search, Blob (originales) y Content Safety. Base de datos local
# (SQLite en data/nube-local.db): PostgreSQL de la nube solo admite servicios de Azure.
#
#   ./scripts/local_nube.sh         arranca (o reinicia) en segundo plano y vuelve:
#                                   app http://localhost:8090 · Studio en el puerto 2025
#   ./scripts/local_nube.sh parar   los detiene
#
# Lo ejecuta make deploy al terminar: no hace falta lanzarlo a mano.
# Requisitos: az login (con los permisos de desarrollador que asigna make deploy) y uv.
set -euo pipefail
cd "$(dirname "$0")/.."

PUERTO="${PORT:-${PUERTO:-8090}}"
PUERTO_STUDIO="${STUDIO_PORT:-${PUERTO_STUDIO:-2025}}"
NUBE_ENV=data/nube.env
PROYECTO="${LANGSMITH_PROJECT_LOCAL_NUBE:-agente-rag-local-nube}"
PID_APP=data/.local-nube.pid
PID_STUDIO=data/.studio-nube.pid
source scripts/comun.sh
paso() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
falla() { printf '\n⛔ %s\n' "$1"; exit 1; }
fijar() { # fijar NOMBRE=valor en $NUBE_ENV
  local nombre=${1%%=*}
  grep -v "^$nombre=" "$NUBE_ENV" > "$NUBE_ENV.tmp" || true
  printf '%s\n' "$1" >> "$NUBE_ENV.tmp" && mv "$NUBE_ENV.tmp" "$NUBE_ENV"
}
# Versiones anteriores de make cloud-local escribían en .env la búsqueda, Blob y Content
# Safety de la nube, y make studio / make docker-up quedaban apuntando a ella con el registro local.
# Si .env tiene el AI Search de este despliegue, se devuelve a local (los modelos se quedan).
reparar_env_local() {
  local search
  search=$(terraform -chdir=infra/platform output -raw search_endpoint 2> /dev/null || true)
  [[ $alcance == completo && -n $search && -f .env ]] && grep -qx "AZURE_SEARCH_ENDPOINT=$search" .env || return 0
  sed -i.bak -E -e 's|^VECTOR_STORE=azure_search$|VECTOR_STORE=qdrant|' \
    -e '/^(AZURE_SEARCH_(ENDPOINT|API_KEY)|CONTENT_SAFETY_ENDPOINT|AZURE_STORAGE_(ACCOUNT_URL|CONTAINER))=/d' \
    .env && rm -f .env.bak
  echo "   .env vuelve a apuntar a lo local (Qdrant); la nube solo en $NUBE_ENV"
}

parar() { # detiene lo arrancado antes (si lo hay)
  local f
  for f in "$PID_APP" "$PID_STUDIO"; do
    [[ -f $f ]] && kill "$(cat "$f")" 2> /dev/null || true
    rm -f "$f"
  done
}
esperar() { # esperar <url> <segundos>
  for _ in $(seq "$2"); do curl -sf "$1" > /dev/null 2>&1 && return 0; sleep 1; done; return 1
}

if [[ ${1:-} == parar ]]; then parar; echo "App y Studio contra la nube detenidos."; exit 0; fi

paso "Requisitos"
az account show > /dev/null 2>&1 || falla "Sin sesión en Azure: ejecuta az login."
echo "✅ az login: $(az account show --query user.name -o tsv)"
terraform -chdir=infra/platform init -input=false -reconfigure \
  -backend-config=../envs/dev/backend.hcl > /dev/null
alcance=$(terraform -chdir=infra/platform output -raw alcance 2> /dev/null || true)
[[ $alcance == completo || $alcance == modelos ]] \
  || falla "No hay recursos en la nube (alcance='${alcance:-ninguno}'): ejecuta antes make deploy."
echo "✅ recursos en la nube (alcance=$alcance)"

paso "1/4 Configuración de la nube en $NUBE_ENV (tu .env no se toca)"
mkdir -p data
reparar_env_local
cp .env "$NUBE_ENV" 2> /dev/null || cp .env.example "$NUBE_ENV"
ENV_FILE="$NUBE_ENV" ./scripts/env_from_azure.sh > /dev/null
# Lo de .env que apunta a local se sustituye por la nube.
for par in MODELOS_PROVEEDOR=azure VECTOR_STORE=azure_search ALMACEN_DOCUMENTOS=blob \
  "DATABASE_URL=sqlite:///$PWD/data/nube-local.db" CACHE_BACKEND=memoria ENTORNO=local \
  AUTH_MODO=stub SELECCION_LIBRE_DE_ROL=true GESTION_DOCUMENTOS=true \
  "LANGSMITH_PROJECT=$PROYECTO"; do
  fijar "$par"
done
# Trazas completas (entorno local); sin LANGSMITH_API_KEY la app las deja apagadas.
grep -qE '^LANGSMITH_API_KEY=.+' "$NUBE_ENV" && fijar "TRAZAS_MODO=${TRAZAS_MODO_LOCAL_NUBE:-completo}"
# La app de este proceso lee estas variables (tienen prioridad sobre .env); Studio lee el
# fichero directamente (langgraph.nube.json).
while IFS= read -r linea; do
  [[ $linea =~ ^([A-Z][A-Z0-9_]*)=(.*)$ ]] || continue
  nombre=${BASH_REMATCH[1]} valor=${BASH_REMATCH[2]}
  [[ $valor =~ ^\"(.*)\"$ || $valor =~ ^\'(.*)\'$ ]] && valor=${BASH_REMATCH[1]}
  export "$nombre=$valor"
done < "$NUBE_ENV"
echo "listo"

paso "2/4 Registro local con los documentos de la nube (desde Blob, sin reindexar)"
uv run python -m ingestor.ingest --source blob 2>&1 | grep -E "Ingesta completada|rechazado|Error" || true

paso "3/4 Prompts en LangSmith"
publicar_prompts

paso "4/4 App y LangGraph Studio en segundo plano (logs en data/)"
parar
nohup uv run uvicorn scripts.local_nube:app --host 127.0.0.1 --port "$PUERTO" \
  > data/local-nube.log 2>&1 &
echo $! > "$PID_APP"
nohup uv run langgraph dev --config langgraph.nube.json --allow-blocking --no-browser \
  --port "$PUERTO_STUDIO" > data/studio-nube.log 2>&1 &
echo $! > "$PID_STUDIO"
esperar "http://127.0.0.1:$PUERTO/api/health" 90 && echo "✅ app" \
  || echo "⚠️  la app no arrancó: revisa data/local-nube.log"
esperar "http://127.0.0.1:$PUERTO_STUDIO/ok" 90 && echo "✅ Studio" \
  || echo "⚠️  Studio no arrancó: revisa data/studio-nube.log"
echo
echo "   Aplicación: http://localhost:$PUERTO  (modelos, búsqueda y documentos de Azure)"
echo "   API:        http://localhost:$PUERTO/api/docs"
echo "   Studio:     https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:$PUERTO_STUDIO"
enlace_langsmith "$PROYECTO"
echo "   Parar: make cloud-local-stop"
