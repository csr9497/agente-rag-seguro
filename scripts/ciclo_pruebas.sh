#!/usr/bin/env bash
# Ciclo de pruebas contra Azure (etapa A). Cada paso deja evidencias en reports/ciclos/<id>/.
#
#   ./scripts/ciclo_pruebas.sh prender   # terraform apply + .env desde Azure + validar infra
#   ./scripts/ciclo_pruebas.sh probar    # siembra documentos, arranca la app (fuera de Docker,
#                                        # con az login para Prompt Shields) y evalúa en LangSmith
#   ./scripts/ciclo_pruebas.sh guardar   # lo que no queda en LangSmith: auditoría, logs, BD…
#   ./scripts/ciclo_pruebas.sh apagar    # para la app y terraform destroy
#   ./scripts/ciclo_pruebas.sh informe   # informe final (Markdown) del ciclo
#   ./scripts/ciclo_pruebas.sh todo      # los cinco pasos en orden (apaga aunque algo falle)
#
# Requisitos: az login (Owner o roles de desarrollador), estado remoto (bootstrap) y
# LANGSMITH_API_KEY en .env. La app de pruebas escucha solo en 127.0.0.1.
set -euo pipefail
cd "$(dirname "$0")/.."

export ARM_SUBSCRIPTION_ID="${ARM_SUBSCRIPTION_ID:-$(az account show --query id -o tsv)}"
TFVARS="../envs/dev/platform.tfvars"
source scripts/comun.sh
PUERTO="${PUERTO:-8010}"
PAUSA="${PAUSA:-2}" # segundos entre escenarios (cuota TPM baja)
CICLO_FILE=reports/ciclos/.actual
if [[ "${1:-}" == "prender" || "${1:-}" == "todo" || ! -f "$CICLO_FILE" ]]; then
  mkdir -p reports/ciclos && date -u +%Y%m%dT%H%M%SZ > "$CICLO_FILE"
fi
RUN="reports/ciclos/$(cat "$CICLO_FILE")"
mkdir -p "$RUN"
paso() { echo "$(date -u +%FT%TZ) $1" | tee -a "$RUN/pasos.log"; }

# Entorno de la app de pruebas: .env (claves desde Key Vault) + estos ajustes.
entorno_app() {
  set -a; source .env; set +a
  export VECTOR_STORE=azure_search ALMACEN_DOCUMENTOS=blob CACHE_BACKEND=memoria \
    DATABASE_URL="sqlite:///$PWD/$RUN/app.db" SELECCION_LIBRE_DE_ROL=true \
    GESTION_DOCUMENTOS=true EXPONER_TOPOLOGIA=true ENTORNO=local TRAZAS_MODO=completo \
    LANGSMITH_PROJECT="${LANGSMITH_PROJECT_CICLO:-agente-rag-etapa-a}" \
    APP_VERSION="$(git rev-parse --short HEAD)"
}

prender() {
  proteger_nube
  paso "prender: terraform apply"
  terraform -chdir=infra/platform apply -input=false -auto-approve -var-file="$TFVARS" \
    > "$RUN/terraform-apply.log" 2>&1
  paso "prender: .env desde Azure"
  ./scripts/env_from_azure.sh > /dev/null
  paso "prender: validación de la infraestructura"
  uv run python scripts/validar_infra.py | tee "$RUN/validacion-infra.txt"
  cp "$(ls -t reports/infra/validacion-*.json | head -1)" "$RUN/validacion-infra.json"
}

probar() {
  entorno_app
  paso "probar: siembra de documentos de ejemplo (registro + Blob + AI Search)"
  uv run python - <<'EOF' | tee "$RUN/siembra.txt"
from pathlib import Path
from app.config import get_settings
from app.deps import build_servicios
from ingestor.sources import LocalFolderSource
s = build_servicios(get_settings())
import time
informe = s.gestor.sincronizar(LocalFolderSource(Path("ingestor/sample_docs")))
print(f"chunks={informe.chunks} estados={informe.por_estado()}")
# AI Search publica los documentos con ~1 s de retraso: se espera antes de dar por buena
# la integridad (si no, lo recién indexado aparece como "falta en el índice").
for intento in range(10):
    integridad = s.verificar_integridad()
    if integridad.ok:
        break
    time.sleep(3)
print(f"integridad_ok={integridad.ok} (intentos={intento + 1})")
EOF
  paso "probar: app en 127.0.0.1:$PUERTO"
  uv run uvicorn app.main:app --host 127.0.0.1 --port "$PUERTO" > "$RUN/app.log" 2>&1 &
  echo $! > "$RUN/app.pid"
  for _ in $(seq 1 60); do curl -sf "localhost:$PUERTO/health" > /dev/null && break; sleep 1; done
  paso "probar: evaluación en LangSmith (una pasada, pausa ${PAUSA}s)"
  uv run python -m evals.ejecutar --base-url "http://localhost:$PUERTO" --langsmith \
    --pausa "$PAUSA" --salida "$RUN/evaluacion.md" | tee "$RUN/evaluacion.txt" || true
}

guardar() {
  paso "guardar: evidencias locales"
  grep ' audit ' "$RUN/app.log" | sed 's/^INFO audit //' > "$RUN/auditoria.jsonl" || true
  git rev-parse HEAD > "$RUN/commit.txt"
  terraform -chdir=infra/platform output -json 2>/dev/null | uv run python -c '
import json, sys
salidas = json.load(sys.stdin)
json.dump({k: v["value"] for k, v in salidas.items() if not v.get("sensitive")}, sys.stdout, indent=2)
' > "$RUN/terraform-outputs.json" || true
  RG=$(uv run python -c "import json;print(json.load(open('$RUN/terraform-outputs.json')).get('resource_group_name',''))" 2>/dev/null || true)
  [[ -n "$RG" ]] && az resource list -g "$RG" \
    --query "[].{nombre:name,tipo:type,sku:sku.name,region:location}" -o json \
    > "$RUN/recursos-azure.json" || true
  [[ -f "$RUN/app.db" ]] && uv run python - "$RUN" <<'EOF'
import json, sqlite3, sys
run = sys.argv[1]
con = sqlite3.connect(f"{run}/app.db")
tablas = [t for (t,) in con.execute("select name from sqlite_master where type='table'")]
volcado = {}
for t in tablas:
    cur = con.execute(f"select * from {t}")
    columnas = [c[0] for c in cur.description]
    volcado[t] = [dict(zip(columnas, fila)) for fila in cur.fetchall()]
json.dump(volcado, open(f"{run}/base-de-datos.json", "w"), indent=2, ensure_ascii=False, default=str)
print({t: len(v) for t, v in volcado.items()})
EOF
}

apagar() {
  paso "apagar: app"
  [[ -f "$RUN/app.pid" ]] && kill "$(cat "$RUN/app.pid")" 2>/dev/null || true
  proteger_nube
  paso "apagar: terraform destroy"
  terraform -chdir=infra/platform destroy -input=false -auto-approve -var-file="$TFVARS" \
    > "$RUN/terraform-destroy.log" 2>&1
  grep -E "Destroy complete|Error" "$RUN/terraform-destroy.log" | tee -a "$RUN/pasos.log"
  paso "apagar: .env sin los valores de los recursos eliminados (endpoints y claves)"
  sed -i.bak -E '/^(AZURE_OPENAI_(ENDPOINT|API_KEY|CHAT_DEPLOYMENT|EMBEDDING_DEPLOYMENT|LIGERO_DEPLOYMENT)|VECTOR_STORE|AZURE_SEARCH_(ENDPOINT|API_KEY)|CONTENT_SAFETY_ENDPOINT|AZURE_STORAGE_(ACCOUNT_URL|CONTAINER)|QDRANT_(URL|API_KEY))=/d' .env
  rm -f .env.bak
}

informe() {
  paso "informe"
  uv run python scripts/informe_ciclo.py "$RUN"
}

case "${1:-}" in
  prender | probar | guardar | apagar | informe) "$1" ;;
  todo)
    # Se apaga aunque falle cualquier paso (también guardar): sin set -e dentro de la trampa.
    trap 'set +e; guardar; apagar; informe' EXIT
    prender
    probar
    ;;
  *) sed -n '2,12p' "$0"; exit 1 ;;
esac
