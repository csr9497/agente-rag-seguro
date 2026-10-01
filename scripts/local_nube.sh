#!/usr/bin/env bash
# App en tu equipo contra los recursos de la nube (lo creado por make desplegar o la etapa A):
# modelos de Azure OpenAI, AI Search, Blob (originales) y Content Safety. Base de datos local
# (SQLite en data/nube-local.db): PostgreSQL de la nube solo admite servicios de Azure.
#
#   ./scripts/local_nube.sh      →  http://localhost:8090  (PUERTO=… para cambiarlo)
#
# Requisitos: az login (con los permisos de desarrollador que asigna make desplegar) y uv.
set -euo pipefail
cd "$(dirname "$0")/.."

PUERTO="${PUERTO:-8090}"
paso() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
falla() { printf '\n⛔ %s\n' "$1"; exit 1; }

paso "Requisitos"
az account show > /dev/null 2>&1 || falla "Sin sesión en Azure: ejecuta az login."
echo "✅ az login: $(az account show --query user.name -o tsv)"
terraform -chdir=infra/platform init -input=false -reconfigure \
  -backend-config=../envs/dev/backend.hcl > /dev/null
alcance=$(terraform -chdir=infra/platform output -raw alcance 2> /dev/null || true)
[[ $alcance == completo || $alcance == modelos ]] \
  || falla "No hay recursos en la nube (alcance='${alcance:-ninguno}'): ejecuta antes make desplegar."
echo "✅ recursos en la nube (alcance=$alcance)"

paso "1/3 .env con los endpoints y claves de la nube (desde Key Vault)"
./scripts/env_from_azure.sh > /dev/null && echo "listo"

# Solo para este proceso: lo de .env que apunta a local se sustituye por la nube.
export MODELOS_PROVEEDOR=azure VECTOR_STORE=azure_search ALMACEN_DOCUMENTOS=blob \
  DATABASE_URL="sqlite:///$PWD/data/nube-local.db" CACHE_BACKEND=memoria ENTORNO=local \
  AUTH_MODO=stub SELECCION_LIBRE_DE_ROL=true GESTION_DOCUMENTOS=true \
  LANGSMITH_PROJECT="${LANGSMITH_PROJECT_LOCAL_NUBE:-agente-rag-local-nube}"
mkdir -p data

paso "2/3 Registro local con los documentos de la nube (desde Blob, sin reindexar)"
uv run python -m ingestor.ingest --source blob 2>&1 | grep -E "Ingesta completada|rechazado|Error" || true

paso "3/3 App en http://localhost:$PUERTO (Ctrl+C para parar)"
echo "   Elige un rol y pregunta: modelos, búsqueda y documentos son los de Azure."
exec uv run uvicorn scripts.local_nube:app --host 127.0.0.1 --port "$PUERTO"
