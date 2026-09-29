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
- **tools** ([app/tools/](app/tools/)): los grupos del usuario los inyecta el grafo; ningún
  esquema de argumentos admite grupos ni usuario.

  | Tool | Para qué |
  |---|---|
  | `rag_retrieve` | Búsqueda semántica en todos los documentos visibles |
  | `listar_documentos` | Catálogo de documentos visibles (citable como "catálogo de documentos") |
  | `buscar_en_documento` | Búsqueda semántica dentro de un documento concreto |
  | `leer_documento` | Fragmentos consecutivos de un documento (hasta 8 por llamada) |

  Un documento ajeno y uno inexistente reciben la misma respuesta (no se revela qué existe).
  El contexto acumulado tiene un tope (`MAX_FRAGMENTOS_CONTEXTO`, 12 por defecto).
- **generate**: respuesta con citas `[n]` y salida estructurada; sin contexto, no llama al LLM.
- **caché semántica** ([app/cache/semantica.py](app/cache/semantica.py)): antes del
  supervisor. La clave incluye el rol y una huella de los documentos visibles para ese rol
  (ids, hashes, estados) más modelo/versión: nunca se sirve a otro rol y cualquier cambio de
  documentos la invalida. No se usa con historial ni para consultas bloqueadas; los aciertos
  pasan igualmente por el guardrail de salida y la auditoría.
- **guardrails** ([app/security/guardrails.py](app/security/guardrails.py)): devuelven un
  `Veredicto` estructurado (permitido, texto saneado, hallazgos con tipo/detalle/acción).
  - entrada: bloquea inyección de prompt y texto oculto; enmascara PII (email, teléfono,
    IBAN, tarjeta con Luhn, DNI/NIE con letra de control) antes del LLM y de la auditoría.
  - salida: bloquea fugas de los prompts de sistema, elimina etiquetas estructurales y
    enmascara PII sensible (tarjeta, IBAN, DNI).
  - Heurísticos y locales; en Azure se añadirá Content Safety (Prompt Shields) detrás de la
    misma interfaz.
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

**Ingesta segura** ([ingestor/validacion.py](ingestor/validacion.py)): solo `.md`/`.txt`
de hasta 1 MB en UTF-8, sin symlinks, rutas ocultas ni `..`; rechaza documentos con texto
invisible o instrucciones dirigidas al modelo. La ingesta informa de los rechazados y sale
con código 1 si hay alguno.

**Gestión de documentos** ([ingestor/gestor.py](ingestor/gestor.py),
[app/api/documentos.py](app/api/documentos.py)): cada chunk guarda el hash SHA-256 del
documento y su fecha de indexación.

| Operación | CLI / API | Comportamiento |
|---|---|---|
| Indexar | `make ingest` · `POST /documentos` (multipart: `grupo`, `archivo`) | `indexado` / `actualizado` (borra antes los chunks antiguos) / `sin_cambios` / `duplicado` (409, mismo contenido en el mismo grupo) / `rechazado` (422, con motivos) |
| Listar | `GET /documentos` | Solo documentos visibles para los grupos del usuario |
| Eliminar | `DELETE /documentos/{grupo}/{archivo}` | Borra todos sus chunks (404 si no existe) |
| Sincronizar | `python -m ingestor.ingest --borrar-huerfanos` | Además borra del índice lo que ya no está en el origen (solo en los grupos del origen) |

Subir o borrar exige pertenecer al grupo `editores` **y** al grupo destino. La API de
escritura está desactivada por defecto (`GESTION_DOCUMENTOS=true` en docker-compose) hasta
tener Entra ID. En local:

```bash
curl -F grupo=public -F archivo=@politica.md localhost:8000/documentos \
  -H 'X-Usuario-Grupos: editores,public'
```

**Web** (http://localhost:8080), página única:
- Se elige un **rol** para empezar. Cada conversación usa solo los permisos de ese rol, y el
  historial muestra por respuesta los documentos consultados, los citados y los fragmentos
  descartados por permisos.
- **Documentos**: los visibles para el rol. Los roles con `gestionar_documentos` pueden
  subir (eligiendo para qué roles es visible, dentro de su `publica_para`) y eliminar.
- **Roles y permisos**: el rol `administrador` crea roles y asigna permisos.
- Roles iniciales: `administrador`, `rrhh` (gestiona y publica para `public` y `rrhh`) y
  `public`. En local cualquier rol es elegible (`SELECCION_LIBRE_DE_ROL=true`); en Azure
  vendrán de Entra ID.

API: `GET /roles`, `POST/PATCH /roles` (administrador), `POST /conversaciones`,
`GET /conversaciones/{id}`, `POST /conversaciones/{id}/mensajes`, `GET/POST/DELETE
/documentos` y `GET /admin/integridad`. El rol de actuación va en la cabecera `X-Rol` y el
servidor valida que se puede usar. Persistencia en SQLite (`DATABASE_URL`).

**Red local**: web (8080), app (8000) y Qdrant (6333) solo escuchan en `127.0.0.1`. Sin
autenticación, exponerlos en la red permitiría a cualquiera subir o borrar documentos o leer
Qdrant sin permisos. La web además elimina la cabecera `X-Usuario-Grupos`.

**Permisos**: el primer nivel de carpeta de cada documento es su grupo
(`public/…`, `rrhh/…`). En la Fase 1 no hay autenticación: el usuario tiene
el rol elegido al iniciar la conversación (ver "Web").

## LangGraph Studio (visualizar y depurar el grafo)

```bash
make up        # Qdrant (y la app) en Docker
make studio    # langgraph dev --allow-blocking
```

| Qué | URL |
|---|---|
| Studio (grafo interactivo, ejecución paso a paso, estado de cada nodo) | https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024 |
| API del servidor LangGraph | http://127.0.0.1:2024/docs |

Studio requiere iniciar sesión con una cuenta gratuita de LangSmith; el grafo y los datos
se ejecutan en local (el navegador se conecta a `127.0.0.1:2024`). El grafo que carga es el
mismo que usa la API ([app/graph/studio.py](app/graph/studio.py), [langgraph.json](langgraph.json)).
Entrada de ejemplo:

```json
{"pregunta": "¿Cuántos días de vacaciones tengo?", "usuario": {"id": "studio", "groups": ["public"]}, "top_k": 4}
```

Sin cuenta de LangSmith, la topología también está en http://localhost:8000/grafo
(Mermaid, solo con `EXPONER_TOPOLOGIA=true`, activo en docker-compose).

## Trazas (LangSmith)

Con `TRAZAS_MODO` y `LANGSMITH_API_KEY` en `.env`, cada consulta genera una traza con el
grafo completo (nodos, tools, llamadas a Azure OpenAI con tokens y latencia) y metadata de
rol, conversación, entorno y versión (el usuario va seudonimizado). Detalles en
[app/observabilidad.py](app/observabilidad.py).

| Modo | Contenido | Uso |
|---|---|---|
| `apagado` | Nada (por defecto; los tests siempre) | — |
| `completo` | Todo el texto | Solo dev, con datos de ejemplo |
| `enmascarado` | Estructura y `doc_id`; PII enmascarada y fragmentos ocultos | Prod (`completo` + `ENTORNO=prod` no arranca) |

## Pruebas de integración (matriz de escenarios)

Los escenarios están en [tests/integration/escenarios.yaml](tests/integration/escenarios.yaml).
Cada uno abre una conversación con su `rol` y envía la pregunta. Los **canarios** (marcadores
únicos en documentos confidenciales) hacen fallar cualquier escenario en el que lleguen a un
rol no autorizado, y `docs_relevantes` alimenta la métrica `recall_docs`.
Cada uno declara las **capacidades** que cubre (respuesta citada, salida de escape, permisos,
fuga de datos, prompt injection, validación…) y sus expectativas. El
[evaluador](tests/integration/evaluador.py) valida cada respuesta con Pydantic: primero el
contrato `RespuestaConsulta` y sus invariantes, después las expectativas del escenario.

```bash
make matriz                                   # cobertura declarada, sin ejecutar
make integration                              # contra http://localhost:8000
make integration BASE_URL=https://<web>/api                                   # contra Azure
```

El informe (`reports/integracion.md`) muestra, por capacidad, los escenarios ok, fallidos y
omitidos. Marca como **⚠️ sin escenarios** las capacidades de la fase actual que no cubre
ningún escenario, y como *planificada* las de fases futuras. Para ampliar la cobertura basta
con añadir escenarios al YAML.

## Evaluaciones (Pydantic, por capas)

[evals/](evals/) evalúa cada escenario de la matriz por capas con modelos Pydantic
([evals/modelos.py](evals/modelos.py)) y aplica umbrales:

| Capa | Métricas | Umbral |
|---|---|---|
| contrato | `contrato_ok` (esquema `MensajeGuardado` + invariantes) | 100% (bloquea) |
| seguridad | `sin_fuga` (canarios entre roles), `inyeccion_contenida` | 100% (bloquea) |
| recuperación | `recall_docs`, `precision_docs`, `mrr` | recall medio ≥ 0,8 (bloquea) |
| juez | `fidelidad`, `relevancia`, `completitud` (gpt-4o, salida `JuicioRespuesta`) | fidelidad media ≥ 4, mínima ≥ 3 |
| deterministas | `expectativas_ok` | ≥ 0,9 (informativa) |

```bash
make evals BASE_URL=http://localhost:8000          # informe en reports/evaluacion.md; sale con 1 si no aprueba
make evals BASE_URL=http://localhost:8000 JUEZ=1   # + juez LLM (requiere Azure OpenAI)
make evals-langsmith BASE_URL=http://localhost:8000  # + dataset "matriz-escenarios" y experimento en LangSmith
```

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
