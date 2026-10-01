# Funciones compartidas por los scripts de Azure (se cargan con `source`).

# La etapa A (make levantar / make ciclo) y la nube (make desplegar) comparten el estado de
# platform: aplicar o destruir la etapa A con la nube desplegada la desmontaría.
proteger_nube() {
  local alcance
  alcance=$(terraform -chdir=infra/platform output -raw alcance 2> /dev/null || true)
  if [[ $alcance == completo ]]; then
    echo "⛔ Hay un despliegue en la nube (alcance=completo) en este estado de Terraform."
    echo "   Esta operación lo desmontaría. Usa la nube (make estado-nube) o elimínala antes"
    echo "   con make destruir-nube."
    exit 1
  fi
}

# Enlace directo a un proyecto de LangSmith (lo crea si aún no existe, para que el enlace
# funcione antes de la primera traza). Sin LANGSMITH_API_KEY (entorno o .env) no imprime nada.
enlace_langsmith() { # enlace_langsmith <proyecto>
  local clave=${LANGSMITH_API_KEY:-$(grep -E '^LANGSMITH_API_KEY=.+' .env 2> /dev/null | tail -1 | cut -d= -f2- || true)}
  if [[ -z $clave ]]; then
    echo "   LangSmith: sin LANGSMITH_API_KEY en .env (trazas desactivadas)"
    return 0
  fi
  LANGSMITH_API_KEY=$clave PROYECTO=$1 uv run python - << 'PY' 2> /dev/null \
    || echo "   LangSmith: https://smith.langchain.com (proyecto $1)"
import os

from langsmith import Client
from langsmith.utils import LangSmithNotFoundError

c, nombre = Client(), os.environ["PROYECTO"]
try:
    p = c.read_project(project_name=nombre)
except LangSmithNotFoundError:
    p = c.create_project(nombre)
print(f"   LangSmith: https://smith.langchain.com/o/{p.tenant_id}/projects/p/{p.id}  ({nombre})")
PY
}

# Prompts del repositorio (app/prompts/*.md) en LangSmith (Prompt Hub), con las etiquetas
# dadas (defecto «dev»). Sin LANGSMITH_API_KEY no hace nada; si LangSmith falla solo avisa.
publicar_prompts() { # publicar_prompts [etiqueta...]
  local args=() e
  for e in "${@:-dev}"; do args+=(--etiqueta "$e"); done
  uv run python -m app.prompts.publicar "${args[@]}" 2>&1 | sed 's/^/   /' || true
}
