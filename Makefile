CONDA_ENV ?= agente-rag
BASE_URL  ?= http://localhost:8000

.PHONY: help setup sync lint fmt test integration matriz evals evals-langsmith up down ingest studio env-from-azure tf-validate

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
	INTEGRATION_BASE_URL=$(BASE_URL) uv run pytest -m integration

evals: ## Evaluaciones por capas contra BASE_URL (umbrales bloqueantes; JUEZ=1 añade gpt-4o)
	uv run python -m evals.ejecutar --base-url $(BASE_URL) $(if $(JUEZ),--juez,)

evals-langsmith: ## Igual que evals + dataset y experimento en LangSmith
	uv run python -m evals.ejecutar --base-url $(BASE_URL) --langsmith $(if $(JUEZ),--juez,)

matriz: ## Informe de cobertura de la matriz sin ejecutar nada
	uv run python -m tests.integration.evaluador

up: ## Levanta app + web + qdrant
	docker compose up --build -d

down:
	docker compose down

ingest: ## Indexa ingestor/sample_docs en el Qdrant local
	docker compose run --rm ingest

studio: ## LangGraph Studio: servidor de desarrollo del grafo en http://127.0.0.1:2024
	uv run langgraph dev --allow-blocking

env-from-azure: ## Rellena .env con endpoint y clave de Azure OpenAI (desde Key Vault)
	./scripts/env_from_azure.sh

tf-validate: ## fmt + validate de los stacks de Terraform
	terraform fmt -check -recursive infra
	for s in platform apps; do terraform -chdir=infra/$$s init -backend=false -input=false >/dev/null && terraform -chdir=infra/$$s validate; done
	terraform -chdir=infra/platform test
