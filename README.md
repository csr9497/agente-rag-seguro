# Asistente RAG seguro — Fase 2 (agente LangGraph)

Asistente interno que responde preguntas sobre documentos de la empresa **con citas** y
**solo con lo que el grupo del usuario puede ver**. Contexto completo y reglas en
[CLAUDE.md](CLAUDE.md).

```
            ┌──────────── Azure Container Apps (VNet) ────────────┐
 usuario ──▶│ web (nginx) ──/api──▶ backend (FastAPI) ──┬─▶ Azure OpenAI (gpt-4o, ada-002)
            │                        ingest job ────────┼─▶ Azure AI Search (filtro por ACL)
            └───────────────────────────────────────────┴─▶ Storage (documentos) · Key Vault
```

El backend es un grafo LangGraph ([app/graph/agente.py](app/graph/agente.py)):

```
authorize → input_guardrail → supervisor ⇄ tools → generate → output_guardrail → audit
    └──────────────┴── (sin grupos / bloqueada) ──────────────────────────────────▶ audit
```

- **supervisor**: gpt-4o con tool-calling; decide cuántas búsquedas hacer (una por tema).
- **tools**: `rag_retrieve`. Los grupos del usuario los inyecta el grafo; el LLM no puede
  elegirlos (el esquema de argumentos solo admite `consulta`).
- **generate**: respuesta con citas `[n]` y salida estructurada; sin contexto, no llama al LLM.
- **guardrails**: interfaz lista; implementación real (Content Safety + PII) en la Fase 3.
- **audit**: toda consulta que entra al grafo se audita, también las rechazadas.

- **Local**: backend + web + Qdrant con `docker compose`, o sin Docker con Qdrant embebido
  (`QDRANT_PATH`); embeddings y LLM en Azure OpenAI.
- **Azure**: el mismo código con `VECTOR_STORE=azure_search` y Managed Identity (sin claves).

## Puesta en marcha (anaconda + uv)

Anaconda aporta el intérprete (Python 3.12) y uv gestiona las dependencias en `.venv` sobre
ese intérprete, con versiones fijadas en `uv.lock`.

```bash
make setup           # conda env create -f environment.yml + uv sync
make test            # tests unitarios (sin Azure)
make lint
```

Para usar otro intérprete 3.12: `uv sync --python /ruta/a/python3.12`.

## Ejecución local

1. Despliega la infraestructura (ver abajo) o usa un Azure OpenAI existente.
2. Rellena `.env`: `make env-from-azure` (lee endpoint y clave desde Key Vault), o bien
   copia `.env.example` y complétalo a mano.
3. Arranca e indexa los documentos de ejemplo:

```bash
make up              # web en http://localhost:8080, API en http://localhost:8000/docs
make ingest
curl -s localhost:8000/consultar -H 'content-type: application/json' \
  -d '{"pregunta": "¿Cuántos días de vacaciones tengo?"}'
```

Sin Docker:

```bash
QDRANT_PATH=.qdrant uv run uvicorn app.main:app --reload
```

Sin `AZURE_OPENAI_ENDPOINT` la app arranca igualmente y `/consultar` responde **503**
indicando qué falta; el resto del grafo (permisos, auditoría, validación) funciona.

**Permisos**: el primer nivel de carpeta de cada documento es su grupo
(`public/…`, `rrhh/…`). En la Fase 1 no hay autenticación: el usuario tiene
`DEFAULT_GROUPS=["public"]`. En local (`IDENTIDAD_DEBUG=true`) se puede simular otro grupo
con la cabecera `X-Usuario-Grupos: rrhh`. En Azure esa cabecera se ignora.

## Pruebas de integración (matriz de escenarios)

Los escenarios están en [tests/integration/escenarios.yaml](tests/integration/escenarios.yaml).
Cada uno declara las **capacidades** que cubre (respuesta citada, salida de escape, permisos,
fuga de datos, prompt injection, validación…) y sus expectativas. El
[evaluador](tests/integration/evaluador.py) valida cada respuesta con Pydantic: primero el
contrato `RespuestaConsulta` y sus invariantes, después las expectativas del escenario.

```bash
make matriz                                   # cobertura declarada, sin ejecutar
make integration                              # contra http://localhost:8000
make integration BASE_URL=https://<web>/api INTEGRATION_IDENTIDAD_DEBUG=false   # contra Azure
```

El informe (`reports/integracion.md`) muestra, por capacidad, los escenarios ok, fallidos y
omitidos. Marca como **⚠️ sin escenarios** las capacidades de la fase actual que no cubre
ningún escenario, y como *planificada* las de fases futuras. Para ampliar la cobertura basta
con añadir escenarios al YAML.

## Despliegue en Azure

Terraform en dos stacks con estado remoto:

| Stack | Contenido |
|---|---|
| [infra/platform](infra/platform) | RG, VNet + subnets + NSG, Log Analytics, ACR, Key Vault, Storage (documentos), Azure OpenAI (`gpt-4o` 2024-11-20 y `text-embedding-ada-002` v2), AI Search, entorno de Container Apps, identidades gestionadas + RBAC, private endpoints (opcional) |
| [infra/apps](infra/apps) | Container Apps `backend` (ingress interno) y `web` (pública), job de ingesta manual |

El workflow [deploy.yml](.github/workflows/deploy.yml) aplica `platform`, construye y sube
las imágenes al ACR (tag = commit) y aplica `apps`. Se lanza con cada push a `main` o a mano;
el lanzamiento manual permite además ejecutar la ingesta.

**Primera vez:**

```bash
# 1. Estado remoto + identidad OIDC para GitHub (requiere Owner en la suscripción)
SUBSCRIPTION_ID=<id> GITHUB_REPO=<owner/repo> ./infra/bootstrap/bootstrap.sh
# 2. Añade tu object id a developer_principal_ids en infra/envs/dev/platform.tfvars
# 3. Push a main → despliegue. Sube documentos y lanza la ingesta:
az storage blob upload-batch --auth-mode login --account-name <storage> \
  -d documentos -s ingestor/sample_docs
# Actions → Deploy → Run workflow → run_ingest ✔
```

### Modelos

| Modelo | Versión | Estado (28/09/2026) | Retirada |
|---|---|---|---|
| gpt-4o | 2024-11-20 | Legacy | 2027-04-14 (reemplazo oficial: gpt-5.1) |
| text-embedding-ada-002 | 2 | GA | 2028-02-09 |

Las versiones de gpt-4o 2024-05-13 y 2024-08-06 ya no admiten despliegues en suscripciones
nuevas. Modelo y versión son variables (`chat_model`, `embedding_model`), así que migrar es
un cambio de tfvars.

### Seguridad en esta fase

- Sin claves en Azure: Managed Identity con roles mínimos por componente (backend solo lee
  el índice; la ingesta escribe; la web solo hace pull de imágenes).
- La clave de OpenAI existe solo para desarrollo local y vive en Key Vault. En prod:
  `openai_local_auth_enabled = false` y `private_endpoints_enabled = true`.
- ⚠️ **La web es pública y aún no tiene autenticación** (llega con Entra ID en la Fase 3).
  Cualquiera con la URL puede consultar los documentos `public` y consumir cuota de OpenAI.
