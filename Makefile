CONDA_ENV ?= agente-rag
BASE_URL  ?= http://localhost:8000

.PHONY: help install check-models up down status docker-up docker-down ingest studio prompts \
	deploy cloud-status cloud-destroy cloud-local cloud-local-stop env-from-azure check-infra cycle \
	setup sync lint fmt test test-postgres evals-mock evals evals-langsmith integration matrix tf-validate

help: ## Lista los comandos (guía completa con valores: docs/comandos.md)
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-17s %s\n", $$1, $$2}'

# --- Local ---------------------------------------------------------------------------------

install: ## Primer uso tras clonar: requisitos, dependencias, .env y Terraform
	./scripts/entorno_local.sh instalar

check-models: ## Credenciales, saldo, modelos y capacidades del proveedor (NO_CALLS=1: sin llamadas)
	uv run python -m app.modelos.diagnostico $(if $(NO_CALLS),--sin-llamadas,)

up: ## Entorno local completo: modelos + Docker + documentos + Studio, y muestra las URLs
	./scripts/entorno_local.sh levantar

down: ## Para el entorno local (sin nube desplegada, borra también los modelos de Azure)
	./scripts/entorno_local.sh apagar

status: ## Estado y URLs del entorno local (app, API, Studio, LangSmith)
	./scripts/entorno_local.sh accesos

docker-up: ## Solo los contenedores (app, web, Qdrant, Redis), sin preparar modelos
	docker compose up --build -d

docker-down: ## Para los contenedores
	docker compose down

ingest: ## Indexa ingestor/sample_docs en el Qdrant local
	docker compose run --rm ingest

studio: ## Solo LangGraph Studio (:2024) con .env; publica antes los prompts
	@bash -c 'source scripts/comun.sh && publicar_prompts'
	uv run langgraph dev --allow-blocking

prompts: ## Publica app/prompts/ en LangSmith (TAG=dev por defecto; TAG=prod para promover)
	uv run python -m app.prompts.publicar --etiqueta $(or $(TAG),dev)

# --- Azure ---------------------------------------------------------------------------------

# ENV=dev|staging|main (defecto dev): entorno de Azure, con sus propios recursos y estado.
deploy: ## Despliega en Azure y deja todo listo: web, prompts, app y Studio locales contra la nube
	ENTORNO=$(or $(ENV),dev) $(if $(LOGIN_PROVIDER),LOGIN_PROVIDER=$(LOGIN_PROVIDER)) $(if $(ALLOWED_IPS),ALLOWED_IPS='$(ALLOWED_IPS)') ./scripts/nube.sh desplegar

cloud-status: ## URL y salud del despliegue en Azure, y enlace de LangSmith
	ENTORNO=$(or $(ENV),dev) ./scripts/nube.sh estado

cloud-destroy: ## Elimina todo lo desplegado en Azure (pide confirmación; CONFIRM=yes la omite)
	ENTORNO=$(or $(ENV),dev) ./scripts/nube.sh destruir

cloud-local: ## (Re)arranca en segundo plano app (:8090) y Studio (:2025) contra Azure (lo hace deploy)
	./scripts/local_nube.sh

cloud-local-stop: ## Detiene la app y Studio de cloud-local
	./scripts/local_nube.sh parar

env-from-azure: ## Rellena .env con endpoints y claves de lo desplegado en Azure (Key Vault)
	./scripts/env_from_azure.sh

check-infra: ## Comprueba cada servicio desplegado (informe en reports/infra/)
	uv run python scripts/validar_infra.py

cycle: ## Ciclo de pruebas contra Azure: STEP=on|test|save|off|report|all (defecto all)
	./scripts/ciclo_pruebas.sh $(or $(STEP),all)

# --- Calidad y entorno ---------------------------------------------------------------------

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

test-postgres: ## Tests con PostgreSQL real (RLS, checkpointer, paridad) en la base agente_test
	docker compose up -d --wait postgres
	docker compose exec -T postgres psql -U agente_app -d agente -tAc \
		"SELECT 1 FROM pg_database WHERE datname='agente_test'" | grep -q 1 \
		|| docker compose exec -T postgres psql -U agente_app -d agente -c "CREATE DATABASE agente_test"
	TEST_DATABASE_URL=postgresql+psycopg://agente_app:$$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)@localhost:55432/agente_test \
		uv run pytest -m postgres -v

evals-mock: ## Gate de CI en local: app con modelos simulados + evaluaciones por capas
	uv run uvicorn tests.integration.servidor_simulado:app --port 8767 & echo $$! > .servidor.pid; \
	for i in $$(seq 1 30); do curl -sf localhost:8767/api/health >/dev/null && break; sleep 1; done; \
	uv run python -m evals.ejecutar --base-url http://localhost:8767/api; r=$$?; kill $$(cat .servidor.pid); rm -f .servidor.pid; exit $$r

integration: ## Matriz de escenarios contra BASE_URL (informe en reports/integracion.md)
	INTEGRATION_BASE_URL=$(BASE_URL) uv run pytest -m integration

evals: ## Evaluaciones por capas contra BASE_URL (umbrales bloqueantes; JUDGE=1 añade el juez LLM)
	uv run python -m evals.ejecutar --base-url $(BASE_URL) $(if $(JUDGE),--juez,)

evals-langsmith: ## Igual que evals + dataset y experimento en LangSmith (JUDGE=1 opcional)
	uv run python -m evals.ejecutar --base-url $(BASE_URL) --langsmith $(if $(JUDGE),--juez,)

matrix: ## Informe de cobertura de la matriz sin ejecutar nada
	uv run python -m tests.integration.evaluador

tf-validate: ## fmt + validate de los stacks de Terraform
	terraform fmt -check -recursive infra
	for s in platform identidad apps; do terraform -chdir=infra/$$s init -backend=false -input=false >/dev/null && terraform -chdir=infra/$$s validate; done
	terraform -chdir=infra/platform test
	terraform -chdir=infra/apps test
