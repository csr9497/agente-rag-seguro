CONDA_ENV ?= agente-rag
BASE_URL  ?= http://localhost:8000

.PHONY: help setup sync lint fmt test integration matriz up down ingest env-from-azure tf-validate

help: ## Lista los comandos
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

setup: ## Crea el entorno conda (Python 3.12) y la .venv de uv sobre ese intérprete
	conda env create -f environment.yml --yes
	$(MAKE) sync

sync: ## Sincroniza dependencias con uv usando el Python del entorno conda
	uv sync --python "$$(conda run -n $(CONDA_ENV) python -c 'import sys; print(sys.executable)')"

lint: ## ruff check + formato
	uv run ruff check
	uv run ruff format --check

fmt: ## Aplica formato
	uv run ruff check --fix
	uv run ruff format

test: ## Tests unitarios
	uv run pytest

integration: ## Matriz de escenarios contra BASE_URL (informe en reports/integracion.md)
	INTEGRATION_BASE_URL=$(BASE_URL) INTEGRATION_IDENTIDAD_DEBUG=$${INTEGRATION_IDENTIDAD_DEBUG:-true} \
		uv run pytest -m integration

matriz: ## Informe de cobertura de la matriz sin ejecutar nada
	uv run python -m tests.integration.evaluador

up: ## Levanta app + web + qdrant
	docker compose up --build -d

down:
	docker compose down

ingest: ## Indexa ingestor/sample_docs en el Qdrant local
	docker compose run --rm ingest

env-from-azure: ## Rellena .env con endpoint y clave de Azure OpenAI (desde Key Vault)
	./scripts/env_from_azure.sh

tf-validate: ## fmt + validate de los stacks de Terraform
	terraform fmt -check -recursive infra
	for s in platform apps; do terraform -chdir=infra/$$s init -backend=false -input=false >/dev/null && terraform -chdir=infra/$$s validate; done
