# Asistente RAG agéntico seguro

Asistente interno en el que cada empleado consulta documentos y datos de la empresa en
lenguaje natural y **solo obtiene respuestas de lo que su rol permite ver**, siempre con
citas. Agente LangGraph + RAG con permisos en el dato, guardrails, caché con permisos,
auditoría, trazas en LangSmith y evaluaciones por capas. Reglas del proyecto en
[CLAUDE.md](CLAUDE.md); diseño detallado en [docs/diseno-fase-3.md](docs/diseno-fase-3.md).

| | |
|---|---|
| Estado | Funcional en local (351 tests). Terraform para Azure listo y probado con mocks, **sin aplicar** |
| Stack | Python 3.12 · LangGraph · FastAPI · Azure OpenAI · AI Search / Qdrant · SQLite / PostgreSQL · Redis · Terraform · GitHub Actions |
| Pendiente | Primer despliegue en Azure (etapa A: modelos + Storage + AI Search) y respuestas reales del LLM |

![Interfaz](docs/ui/2-public.png)

---

## Arquitectura

```
            ┌──────────────────── Azure Container Apps (VNet) ───────────────────┐
 usuario ──▶│ web (nginx, Easy Auth) ──/api──▶ backend (FastAPI + LangGraph) ────┼─▶ Azure OpenAI (gpt-4o, ada-002)
            │                                    │   │   │                       ├─▶ AI Search / Qdrant (filtro por rol)
            │                        ingest job ─┘   │   └─▶ PostgreSQL          ├─▶ Blob (originales + roles)
            └────────────────────────────────────────┴─▶ Managed Redis (caché) ──┴─▶ Key Vault · Log Analytics · LangSmith
```

El agente es un grafo LangGraph ([app/graph/agente.py](app/graph/agente.py)); la topología
se ve en http://localhost:8000/grafo o en LangGraph Studio:

```
authorize → input_guardrail → cache_lookup ─(acierto)──────────────────────────┐
                                  │                                             ▼
                                  └─(fallo)→ supervisor ⇄ tools → access_guardrail   output_guardrail → cache_store → audit
                                                 └────────→ generate ─────────────────────▲
authorize / input_guardrail ──(sin rol / bloqueada)───────────────────────────────────────────────────────────→ audit
```

| Nodo | Qué hace |
|---|---|
| `authorize` | Deny by default: sin rol no hay contexto |
| `input_guardrail` | Bloquea inyección de prompt y texto oculto; enmascara PII (email, teléfono, IBAN, tarjeta con Luhn, DNI/NIE). Con `CONTENT_SAFETY_ENDPOINT`, además **Prompt Shields** sobre el texto ya enmascarado |
| `cache_lookup` / `cache_store` | Caché semántica; clave = rol + huella de sus documentos visibles + modelo/versión |
| `supervisor` | gpt-4o con tool-calling; decide qué tools usar (hasta 3 iteraciones) |
| `tools` | Ejecuta las tools (abajo); los roles salen del estado, nunca de los argumentos del LLM |
| `access_guardrail` | Contrasta **cada** fragmento con el registro de documentos (roles, hash, estado) antes de que el LLM lo vea |
| `generate` | Respuesta con citas `[n]` y salida estructurada Pydantic; sin contexto, no llama al LLM |
| `output_guardrail` | Bloquea fugas del prompt de sistema y enmascara PII sensible |
| `audit` | Registra toda consulta, también las bloqueadas, con sus hallazgos |

### Tools

| Tool | Para qué | Control de acceso |
|---|---|---|
| `rag_retrieve` | Búsqueda semántica en los documentos del rol | Filtro por rol en el índice + `access_guardrail` |
| `listar_documentos` | Catálogo de documentos visibles | Construido desde el registro (fuente de verdad) |
| `buscar_en_documento` / `leer_documento` | Buscar dentro de un documento / leer fragmentos consecutivos | Ajeno e inexistente responden igual (no se revela qué existe) |
| `data_query` | Datos internos estructurados (festivos, plantilla, presupuestos) | **Catálogo de consultas parametrizadas**: el LLM nunca escribe SQL; cada consulta declara sus roles |
| `proponer_accion` | Abrir ticket, solicitar vacaciones | Solo **propone**: nada se ejecuta sin que la persona pulse *Aprobar* |

## Seguridad

Permisos en el dato, no en el prompt (regla 1 del CLAUDE.md), en cuatro barreras:

1. **Al indexar**: un documento necesita ≥ 1 rol existente y activo.
2. **Al empezar**: la conversación queda fijada a un rol que el usuario puede usar (Entra ID
   en Azure; selección libre solo en local).
3. **En el índice**: filtro por rol en Qdrant / AI Search (filtro OData validado contra inyección).
4. **`access_guardrail`**: cada fragmento se contrasta con el registro. Una prueba de mutación
   confirma que, sin esta barrera, un índice manipulado filtraría datos de RRHH.

Además:

- **Integridad** índice ↔ registro al arrancar y en `GET /admin/integridad`: cuarentena de
  lo inconsistente y reactivación automática.
- **Ingesta segura** ([ingestor/validacion.py](ingestor/validacion.py)): solo `.md`/`.txt`
  UTF-8 ≤ 1 MB, sin symlinks ni rutas ocultas; rechaza texto invisible e instrucciones
  dirigidas al modelo (también dentro de comentarios HTML) y, con Content Safety, ataques
  indirectos detectados por Prompt Shields.
- **Prompt Shields** ([app/security/content_safety.py](app/security/content_safety.py)): se suma
  a las heurísticas locales, no las sustituye. Sin claves (Managed Identity). Si el servicio
  falla, `CONTENT_SAFETY_FALLO=cerrado` (defecto) bloquea; `abierto` deja pasar y lo audita.
- **Prompts delimitados**: `<historial>`, `<contexto>` con `<fragmento>` y `<pregunta>`;
  cualquier intento de cerrar esas etiquetas desde un documento se neutraliza.
- **Caché con permisos** (regla 2): nunca se sirve a otro rol; subir, borrar o poner en
  cuarentena un documento la invalida; no se usa con historial, datos internos ni acciones.
- **Entra ID** (`AUTH_MODO=entra`): JWT RS256 validado con JWKS, emisor, audiencia y
  caducidad; se rechazan `alg: none` y HS256. Roles = app roles del token.
- **Secretos** (regla 3): solo en Key Vault o `.env` local (ignorado por git). Managed
  Identity con roles mínimos en Azure.
- **Red local**: web, API, Qdrant, Redis y PostgreSQL solo escuchan en `127.0.0.1`.
- Tests de regresión de configuración: los modos de depuración nunca se activan en Terraform
  y el compose no contiene contraseñas.

## Puesta en marcha

Requisitos: anaconda (intérprete 3.12), [uv](https://docs.astral.sh/uv/), Docker. Terraform
y Azure CLI solo para desplegar.

```bash
make setup          # conda env (Python 3.12) + uv sync
make test           # tests unitarios, sin servicios externos
make up             # web :8080, API :8000, Qdrant, Redis (todo en 127.0.0.1)
make evals-simulado # gate de CI en local: app con modelos simulados + evaluaciones
make down
```

| Qué | Dónde |
|---|---|
| Web | http://localhost:8080 |
| API (OpenAPI) | http://localhost:8000/docs |
| Topología del grafo | http://localhost:8000/grafo |
| LangGraph Studio (`make studio`, en Chrome) | https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024 |

Sin Azure OpenAI configurado la app arranca y responde **503** en lo que necesita el LLM; los
permisos, guardrails de entrada, roles, subida (validación) y auditoría funcionan. Para
respuestas reales: `make env-from-azure` tras desplegar la etapa A (rellena `.env` desde
Key Vault) y `make ingest`.

### Uso

- **Rol**: se elige al empezar; cada conversación usa solo sus permisos. Roles iniciales:
  `administrador` (gestiona roles), `rrhh` (gestiona documentos, publica para `rrhh` y
  `public`) y `public`.
- **Historial**: por respuesta, documentos consultados, citados (✓), fragmentos descartados
  por permisos, "desde caché" y valoración 👍/👎 (se envía a LangSmith con su traza).
- **Documentos**: los roles con `gestionar_documentos` suben `.md`/`.txt` eligiendo para qué
  roles es visible; el original se guarda con sus roles (carpeta local o Blob).
- **Roles y permisos**: el administrador crea roles y asigna `gestionar_documentos`,
  `administrar_roles` y "publica para". No se puede quitar el último administrador.
- **Acciones**: "abre un ticket…" → tarjeta con *Aprobar* / *Rechazar*.

### API

| Método | Ruta | Notas |
|---|---|---|
| GET | `/roles` | Roles que el usuario puede usar |
| GET/POST/PATCH | `/roles/todos`, `/roles`, `/roles/{id}` | Solo `administrar_roles` (`X-Rol`) |
| POST | `/conversaciones` | `{"rol_id"}` |
| GET | `/conversaciones/{id}` | Historial |
| POST | `/conversaciones/{id}/mensajes` | `{"pregunta"}` → respuesta, citas, documentos consultados, acciones |
| POST | `/conversaciones/{id}/mensajes/{m}/feedback` | `{"valoracion": "positiva"\|"negativa", "comentario"?}` |
| GET/POST/DELETE | `/documentos` | Por rol (`X-Rol`); subir y borrar requieren `gestionar_documentos` |
| GET / POST | `/acciones`, `/acciones/{id}/decision` | `{"aprobar": bool}`; solo quien la propuso |
| GET | `/admin/integridad` | Solo `administrar_roles` |

`X-Rol` indica el rol con el que se actúa; el servidor valida que el usuario puede usarlo.

## Pruebas y evaluaciones

| Nivel | Comando | Qué cubre |
|---|---|---|
| Unitarios | `make test` | 348 tests: permisos, guardrails, Prompt Shields, tools, caché, Entra ID, API, persistencia |
| PostgreSQL | `make test-postgres` | 3 tests: repositorios y flujo completo contra PostgreSQL 16 |
| Matriz de integración | `make integration BASE_URL=…` | 28 escenarios por HTTP (incl. caché entre roles) ([escenarios.yaml](tests/integration/escenarios.yaml)); canarios entre roles; cobertura por capacidad con `make matriz` |
| Evaluaciones por capas | `make evals BASE_URL=…` (`JUEZ=1` con gpt-4o) | Contrato, seguridad, recuperación (recall, MRR), juez LLM; umbrales bloqueantes ([evals/](evals/)) |
| LangSmith | `make evals-langsmith BASE_URL=…` | Dataset `matriz-escenarios` + experimento |
| Terraform | `make tf-validate` | fmt, validate y 11 tests de flags con providers simulados |

Umbrales bloqueantes: contrato 100 %, **cero fugas entre roles**, inyección contenida 100 %,
recall medio ≥ 0,8 y (con juez) fidelidad media ≥ 4.

## Observabilidad

Trazas en LangSmith con el grafo completo, tools, llamadas a Azure OpenAI (tokens, latencia)
y metadata de rol, conversación y versión ([app/observabilidad.py](app/observabilidad.py)):
`TRAZAS_MODO=apagado | completo (solo dev) | enmascarado (prod: PII y fragmentos ocultos)`.
`completo` con `ENTORNO=prod` no arranca. La valoración de los usuarios llega como feedback.

## CI/CD

[ci.yml](.github/workflows/ci.yml) en cada PR y push a `main`: lint + tests, paridad con
PostgreSQL (servicio del runner), **gate de evaluaciones** (app con modelos simulados:
falla si hay fugas entre roles o contrato roto), Terraform (fmt, validate, tests) y build de
imágenes. [deploy.yml](.github/workflows/deploy.yml) (OIDC, sin secretos) está desactivado
hasta definir la variable de repositorio `DEPLOY_AZURE=true`.

## Ciclo de pruebas en Azure

`make ciclo` (o `PASO=prender|probar|guardar|apagar|informe`) ejecuta el ciclo completo y deja
las evidencias en `reports/ciclos/<fecha>/`:

1. **Prender**: `terraform apply` (etapa A) → `.env` desde Key Vault → `make validar-infra`
   (chat, embeddings, Key Vault, AI Search, Blob y Prompt Shields, con informe Pydantic).
2. **Probar**: siembra de documentos (registro + Blob + AI Search) y app local contra Azure;
   **una pasada** de la matriz como experimento de LangSmith con los evaluadores por capas y
   los prebuilt de LangSmith (`openevals`: groundedness, helpfulness, retrieval relevance).
   Las trazas de la app van al proyecto `agente-rag-etapa-a`.
3. **Guardar** lo que no queda en LangSmith: auditoría (`auditoria.jsonl`), log de la app,
   volcado de la base (conversaciones, registro, acciones), recursos creados y salidas de
   Terraform sin valores sensibles.
4. **Apagar**: para la app y `terraform destroy`. Con `todo`, se apaga aunque falle un paso.
5. **Informe**: `informe.md` con infraestructura, evaluación, auditoría, datos y apagado.

## Despliegue en Azure

Terraform en dos stacks ([infra/](infra/)) con flags:

| Variable | Valores | Efecto |
|---|---|---|
| `alcance` | `modelos` · `completo` | Etapa A (OpenAI, Storage, Key Vault, vector store; app en local) · etapa B (+ VNet, ACR, Container Apps, PostgreSQL, Managed Redis) |
| `vector_store` | `azure_search` · `qdrant` | AI Search (`search_sku`: free/basic) o Qdrant (`qdrant_modo`: local, cloud, container_efimero) |
| `search_auth` | `api_key` · `rbac` | Clave en Key Vault (dev con Docker) o Managed Identity |
| `content_safety` | bool | Azure AI Content Safety (Prompt Shields), sin claves; `true` por defecto |
| `cache_redis` | bool | Azure Managed Redis (Azure Cache for Redis no admite altas desde el 1-oct-2026) |

```bash
# Una vez: estado remoto + identidad OIDC para GitHub
SUBSCRIPTION_ID=<id> GITHUB_REPO=<owner/repo> ./infra/bootstrap/bootstrap.sh
# Etapa A (desde local, revisando el plan antes)
terraform -chdir=infra/platform init -backend-config=...   # ver deploy.yml
terraform -chdir=infra/platform plan -var-file=../envs/dev/platform.tfvars
make env-from-azure && make ingest
```

Modelos: `gpt-4o` 2024-11-20 (Legacy, retirada 2027-04-14; reemplazo gpt-5.1) y
`text-embedding-ada-002` v2 (GA hasta 2028-02-09); se cambian por tfvars.

## Estructura

```
app/            FastAPI + grafo LangGraph
  graph/        agente, estado, prompts, topología, entrada de Studio
  tools/        rag_retrieve, documentos, data_query, proponer_accion
  security/     guardrails, detección (PII/inyección), acceso, Entra ID, auditoría
  cache/        caché semántica (memoria / Redis)
  persistencia/ repositorios SQLAlchemy (SQLite / PostgreSQL), almacén de originales
  servicios/    roles, conversaciones, integridad
  acciones/     acciones con aprobación humana
  datos/        catálogo de consultas de data_query
  api/          routers HTTP
ingestor/       validación, chunking, orígenes (carpeta / Blob), gestor de documentos
evals/          evaluaciones por capas, juez LLM, LangSmith
web/            interfaz (HTML/CSS/JS sin dependencias) + nginx
infra/          Terraform: platform, apps, módulos, tests
tests/          unitarios, integración (matriz), servidor simulado
docs/           diseño y capturas
```
