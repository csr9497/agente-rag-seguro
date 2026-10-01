CONDA_ENV ?= agente-rag
BASE_URL  ?= http://localhost:8000

.PHONY: help setup sync lint fmt test test-postgres integration matriz evals evals-simulado evals-langsmith up down ingest studio env-from-azure tf-validate validar-infra verificar-modelos desplegar estado-nube destruir-nube ciclo modelos-up modelos-down instalar levantar apagar accesos

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

test-postgres: ## Paridad con PostgreSQL (levanta el perfil postgres de docker-compose)
	docker compose --profile postgres up -d postgres
	TEST_DATABASE_URL=postgresql+psycopg://agente_app:$$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)@localhost:55432/agente \
		uv run pytest tests/test_postgres.py -v

evals-simulado: ## Gate de CI en local: app con modelos simulados + evaluaciones por capas
	uv run uvicorn tests.integration.servidor_simulado:app --port 8767 & echo $$! > .servidor.pid; \
	for i in $$(seq 1 30); do curl -sf localhost:8767/api/health >/dev/null && break; sleep 1; done; \
	uv run python -m evals.ejecutar --base-url http://localhost:8767/api; r=$$?; kill $$(cat .servidor.pid); rm -f .servidor.pid; exit $$r

integration: ## Matriz de escenarios contra BASE_URL (informe en reports/integracion.md)
	INTEGRATION_BASE_URL=$(BASE_URL) uv run pytest -m integration

evals: ## Evaluaciones por capas contra BASE_URL (umbrales bloqueantes; JUEZ=1 añade gpt-4o)
	uv run python -m evals.ejecutar --base-url $(BASE_URL) $(if $(JUEZ),--juez,)

evals-langsmith: ## Igual que evals + dataset y experimento en LangSmith
	uv run python -m evals.ejecutar --base-url $(BASE_URL) --langsmith $(if $(JUEZ),--juez,)

matriz: ## Informe de cobertura de la matriz sin ejecutar nada
	uv run python -m tests.integration.evaluador

up: ## Levanta app + web + qdrant + redis
	docker compose up --build -d

down:
	docker compose down

ingest: ## Indexa ingestor/sample_docs en el Qdrant local
	docker compose run --rm ingest

studio: ## LangGraph Studio: servidor de desarrollo del grafo en http://127.0.0.1:2024
	uv run langgraph dev --allow-blocking

verificar-modelos: ## Requisitos y capacidades del proveedor de modelos (MODELOS_PROVEEDOR)
	uv run python -m app.modelos.diagnostico $(if $(SIN_LLAMADAS),--sin-llamadas,)

env-from-azure: ## Rellena .env con endpoint y clave de Azure OpenAI (desde Key Vault)
	./scripts/env_from_azure.sh

instalar: ## Primer uso tras clonar: requisitos, dependencias, .env y Terraform
	./scripts/entorno_local.sh instalar

levantar: ## Todo el entorno: modelos (Azure u OpenAI) + Docker + documentos + Studio, y muestra los accesos
	./scripts/entorno_local.sh levantar

apagar: ## Para Studio y Docker; con Azure, elimina los modelos y limpia .env (sin costes)
	./scripts/entorno_local.sh apagar

accesos: ## Estado de cada servicio y sus URLs (app, API, Studio, LangSmith)
	./scripts/entorno_local.sh accesos

modelos-up: levantar ## Alias de levantar
modelos-down: apagar ## Alias de apagar

desplegar: ## Despliega todo en Azure (web pública con login de Entra ID) y muestra la URL
	./scripts/nube.sh desplegar

estado-nube: ## URL y salud del despliegue en Azure
	./scripts/nube.sh estado

destruir-nube: ## Elimina todo lo desplegado en Azure (pide confirmación)
	./scripts/nube.sh destruir

validar-infra: ## Comprueba cada servicio desplegado (informe en reports/infra/)
	uv run python scripts/validar_infra.py

ciclo: ## Ciclo contra Azure: PASO=prender|probar|guardar|apagar|informe|todo
	./scripts/ciclo_pruebas.sh $(or $(PASO),todo)

tf-validate: ## fmt + validate de los stacks de Terraform
	terraform fmt -check -recursive infra
	for s in platform identidad apps; do terraform -chdir=infra/$$s init -backend=false -input=false >/dev/null && terraform -chdir=infra/$$s validate; done
	terraform -chdir=infra/platform test
	terraform -chdir=infra/apps test
