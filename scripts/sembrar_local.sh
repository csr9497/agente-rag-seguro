#!/usr/bin/env bash
# Carga los documentos de ejemplo en la app de docker-compose (registro + almacén + Qdrant),
# con los mismos permisos que usa la app. Idempotente: lo que no cambia queda "sin_cambios".
set -euo pipefail
cd "$(dirname "$0")/.."
for _ in $(seq 1 60); do curl -sf localhost:8000/health > /dev/null && break; sleep 1; done
docker compose exec -T app python - <<'PY'
from pathlib import Path
from app.config import get_settings
from app.deps import build_servicios
from ingestor.sources import LocalFolderSource
s = build_servicios(get_settings())
informe = s.gestor.sincronizar(LocalFolderSource(Path("ingestor/sample_docs")))
print(f"Documentos de ejemplo: {informe.por_estado()} · integridad_ok={s.verificar_integridad().ok}")
PY
