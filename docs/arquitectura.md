# Arquitectura y referencia técnica

Detalle del agente, la seguridad, la API, las pruebas y la infraestructura. Para empezar,
el [README](../README.md).

## Arquitectura

```
            ┌──────────────────── Azure Container Apps (VNet) ───────────────────┐
 usuario ──▶│ web (nginx, Easy Auth) ──/api──▶ backend (FastAPI + LangGraph) ────┼─▶ Azure OpenAI u OpenAI/compatible
            │                                    │   │   │                       ├─▶ AI Search / Qdrant (filtro por rol)
            │                        ingest job ─┘   │   └─▶ PostgreSQL          ├─▶ Blob (originales + roles)
            └────────────────────────────────────────┴─▶ Managed Redis (caché) ──┴─▶ Key Vault · Log Analytics · LangSmith
```

El asistente es un **orquestador multiagente** en LangGraph
([app/agents/orquestador.py](../app/agents/orquestador.py)); la topología se ve en
http://localhost:8000/grafo (local, con `EXPONER_TOPOLOGIA=true`) o en LangGraph Studio
(grafo `multiagente`):

```
inicio → authorize → input_guardrail → cache_lookup ─(acierto)──────────────────────┐
                                           └─(fallo)→ supervisor ─→ [agentes en paralelo]
                                                          │         rag_agent · hr_agent · support_agent
                                                          │                  ↓
                                                          │         sintetizar ⇄ verifier ─(3 fallos)→ escalate_human
                                                          ↓                  ↓                                ↓
                                    (conversación / aclaración) → output_guardrail → cache_store → audit
authorize / input_guardrail ──(sin rol / bloqueada)──────────→ output_guardrail → … → audit
```

| Nodo | Qué hace |
|---|---|
| `inicio` / `authorize` | Estado limpio por mensaje. Deny by default: sin rol no hay contexto ni llamadas a modelos; solo `authorize` escribe el usuario (roles, scopes y grupos ACL) |
| `input_guardrail` | Bloquea inyección de prompt, texto oculto y políticas de uso; enmascara PII (email, teléfono, IBAN, tarjeta con Luhn, DNI/NIE). Con `CONTENT_SAFETY_ENDPOINT`, además **Prompt Shields** |
| `cache_lookup` / `cache_store` | Caché semántica (memoria o Redis); clave = consulta + alcance de permisos (documentos visibles + modelo/versión). Solo respuestas verificadas, con fuentes y solo de `rag_agent`; nunca escrituras, datos internos, «no lo encuentro» ni preguntas con historial |
| `supervisor` | Tool-calling: delega en uno o varios agentes (`delegar_<agente>`, cada uno solo con su parte del mensaje), responde con plantilla u orientación (`conversacion`) o pide aclaración. Recibe el **catálogo del rol** |
| Agentes | Cada uno en su subgrafo `agent → policy_gate → execute_tool / human_approval → sanitize_output` ([app/agents/subgraph.py](../app/agents/subgraph.py)) |
| `sintetizar` | Con un agente usa su respuesta; con varios (o tras una corrección) un LLM las integra |
| `verifier` | Determinista: citas solo a lo recuperado, ningún identificador inventado, sin PII, sin números de cita; hasta 3 reescrituras y después `escalate_human` (rol `administrador`) |
| `output_guardrail` | Bloquea fugas de **cualquier** prompt de sistema registrado y enmascara PII sensible |
| `audit` | Registra toda consulta (también bloqueadas o con error del proveedor), con hallazgos y versiones de guardrails |

### Agentes y tools

El modo de cada tool lo decide `policy_gate` en código (scope del rol + argumentos), nunca el
prompt: `auto` se ejecuta, `confirm_user` lo confirma la persona, `approve_staff` lo aprueba
otra persona con el rol indicado, y lo que no tiene scope se deniega. Ninguna escritura es
`auto`; las aprobaciones caducan a las 24 h.

| Agente | Tools | Control de acceso |
|---|---|---|
| `rag_agent` | `search_documents`, `get_document_metadata`, `listar_documentos`, `leer_documento`, `buscar_en_documento`, `data_query` (auto) · `request_document_access` (confirm_user) | Filtro ACL en el índice + **re-chequeo de cada fragmento contra el registro** (revocado, caducado, ACL o hash distintos se descartan y se registran como evento de seguridad); `data_query` es un catálogo de consultas parametrizadas con sus roles |
| `hr_agent` | `search_hr_policies`, `get_my_hr_cases` (auto) · `create_hr_case`, `add_hr_case_note` (confirm_user) | Casos en PostgreSQL con **RLS**; acoso, salud, discriminación y disciplina son confidenciales |
| `support_agent` | `search_it_kb`, `search_my_tickets` (auto) · `create_ticket` (confirm_user; **P1 → approve_staff de `it_support`**), `add_ticket_comment` (confirm_user) | Tickets en PostgreSQL con **RLS** |

`hr_agent` y `support_agent` solo se registran con PostgreSQL (sin RLS no existen). Roles y
scopes: [app/agents/scopes.py](../app/agents/scopes.py).

## Seguridad

Permisos en el dato, no en el prompt (regla 1 del CLAUDE.md), en cinco barreras:

1. **Al indexar**: un documento necesita ≥ 1 rol existente y activo.
2. **Al empezar**: la conversación queda fijada a un rol que el usuario puede usar (app roles
   de Entra ID o asignados desde la app en Azure; selección libre solo en local).
3. **En el índice**: filtro por rol en Qdrant / AI Search (filtro OData validado contra inyección).
4. **Re-chequeo contra el registro** en las tools de `rag_agent`: cada fragmento se contrasta
   con el registro (roles, hash, estado, revocación, caducidad) antes de que lo vea un LLM; lo
   descartado queda como evento de seguridad (logger `seguridad`, sin el contenido).
5. **RLS en PostgreSQL** para casos de RR.HH. y tickets (`SET LOCAL ROLE agente_rls`).

Además:

- **Conversaciones con propietario**: solo quien la creó puede verla o continuarla (404 para
  el resto, aunque tenga el mismo rol). El historial (`GET /conversaciones?rol_id=`) lista las
  propias con el rol activo.
- **Integridad** índice ↔ registro al arrancar y en `GET /admin/integridad`: cuarentena de
  lo inconsistente y reactivación automática.
- **Ingesta segura** ([ingestor/validacion.py](../ingestor/validacion.py)): solo `.md`/`.txt`
  UTF-8 ≤ 1 MB, sin symlinks ni rutas ocultas; rechaza texto invisible e instrucciones
  dirigidas al modelo (también dentro de comentarios HTML) y, con Content Safety, ataques
  indirectos detectados por Prompt Shields.
- **Versiones de guardrails** ([app/security/versiones.py](../app/security/versiones.py)): entrada
  `v1-heuristico` | `v2-prompt-shields` | `sin-guardrail`, salida `v1-fuga-prompt` |
  `sin-guardrail` (desactivar solo fuera de producción). La app usa `GUARDRAIL_ENTRADA`
  (`auto`: Prompt Shields si está configurado) y `GUARDRAIL_SALIDA`; ninguna petición puede
  elegirlas, y la versión aplicada queda en la auditoría. Para una versión nueva:
  implementa `Guardrail`, regístrala en el catálogo y añade su nombre al `Literal`.
- **Filtro de contenido del proveedor**: si el modelo rechaza una petición (p. ej. jailbreak
  que pasó los guardrails), se responde como bloqueada y se audita (`filtro_contenido_azure`).
- **Prompt Shields** ([app/security/content_safety.py](../app/security/content_safety.py)): se suma
  a las heurísticas locales, no las sustituye. Sin claves (Managed Identity). Si el servicio
  falla, `CONTENT_SAFETY_FALLO=cerrado` (defecto) bloquea; `abierto` deja pasar y lo audita.
- **Prompts delimitados**: `<historial>`, `<pregunta>`, `<catalogo>` y los resultados de las
  tools dentro de `<dato_herramienta>` (son datos, nunca instrucciones); cualquier intento de
  cerrar esas etiquetas desde un documento o la pregunta se neutraliza.
- **Caché con permisos** (regla 2): nunca se sirve a otro alcance de permisos; subir, borrar,
  revocar o poner en cuarentena un documento la invalida; no se usa con historial, datos
  internos ni escrituras.
- **Estado de los hilos cifrado**: el checkpointer de LangGraph guarda en PostgreSQL con
  AES (`CHECKPOINT_CLAVE`, en Key Vault en Azure); sin la clave la app no arranca.
- **Login** ([app/security/identity.py](../app/security/identity.py)): en Azure, Easy Auth de
  Container Apps en la web (GitHub por defecto, o Entra ID) y `AUTH_MODO=easyauth` en el
  backend, que solo acepta el principal si llega de nginx con `PROXY_SECRETO` (ingress
  interno). Roles = app roles del token (Entra ID) + los asignados a la persona desde la app
  (`/roles/asignaciones`, auditado); sin roles no hay acceso. `AUTH_MODO=entra` valida
  un JWT RS256 (JWKS, emisor, audiencia, caducidad; sin `alg: none` ni HS256) para clientes de
  API. Con `ENTORNO=prod` la API no arranca sin login ni con modos de depuración.
- **Secretos** (regla 3): solo en Key Vault o `.env` local (ignorado por git). Managed
  Identity con roles mínimos en Azure.
- **Red local**: web, API, Qdrant, Redis y PostgreSQL solo escuchan en `127.0.0.1`.
- Tests de regresión de configuración: los modos de depuración nunca se activan en Terraform
  y el compose no contiene contraseñas.

## API

| Método | Ruta | Notas |
|---|---|---|
| GET | `/roles` | Roles que el usuario puede usar |
| GET/POST/PATCH | `/roles/todos`, `/roles`, `/roles/{id}` | Solo `administrar_roles` (`X-Rol`) |
| GET / PUT | `/roles/asignaciones`, `/roles/asignaciones/{usuario}` | Roles de cada persona; solo `administrar_roles`; auditado |
| GET | `/yo` | Usuario de la sesión y sus roles |
| POST | `/conversaciones` | `{"rol_id"}` |
| GET | `/conversaciones/{id}` | Historial |
| POST | `/conversaciones/{id}/mensajes` | `{"pregunta"}` → respuesta, citas, documentos consultados, agentes, aprobaciones pendientes |
| POST | `/conversaciones/{id}/mensajes/{m}/feedback` | `{"valoracion": "positiva"\|"negativa", "comentario"?}` |
| GET/POST/DELETE | `/documentos` | Por rol (`X-Rol`); subir y borrar requieren `gestionar_documentos` |
| GET | `/aprobaciones` | Lo que puedo resolver: mis confirmaciones y la cola de mis roles |
| POST | `/aprobaciones/{id}/decision` | `{"aprobar", "motivo"?, "edited_args"?, "respuesta"?}`; quien decide sale del token |
| POST | `/consultar` | Consulta sin conversación (`{"pregunta"}`), por el mismo orquestador |
| GET | `/admin/integridad` | Solo `administrar_roles` |

`X-Rol` indica el rol con el que se actúa; el servidor valida que el usuario puede usarlo.

## Pruebas y evaluaciones

| Nivel | Comando | Qué cubre |
|---|---|---|
| Unitarios | `make test` | Permisos, guardrails, Prompt Shields, tools, caché, Entra ID / Easy Auth, proveedor de modelos, API, persistencia |
| PostgreSQL | `make test-postgres` | Repositorios y flujo completo contra PostgreSQL 16 |
| Matriz de integración | `make integration BASE_URL=…` | Escenarios por HTTP (incl. caché entre roles) ([escenarios.yaml](../tests/integration/escenarios.yaml)); canarios entre roles; cobertura por capacidad con `make matrix` |
| Evaluaciones por capas | `make evals BASE_URL=…` (`JUDGE=1` con el modelo de chat) | Contrato, seguridad, recuperación (recall, MRR), juez LLM; umbrales bloqueantes ([evals/](../evals/)) |
| LangSmith | `make evals-langsmith BASE_URL=…` | Dataset `matriz-escenarios` + experimento |
| Terraform | `make tf-validate` | fmt, validate y tests de flags con providers simulados |

Umbrales bloqueantes: contrato 100 %, **cero fugas entre roles**, inyección contenida 100 %,
recall medio ≥ 0,8 y (con juez) fidelidad media ≥ 4.

## Observabilidad

Trazas en LangSmith con el grafo completo, tools, llamadas al modelo (tokens, latencia)
y metadata de rol, conversación y versión ([app/observabilidad.py](../app/observabilidad.py)):
`TRAZAS_MODO=apagado | completo (solo dev) | enmascarado (prod: PII y fragmentos ocultos)`.
`completo` con `ENTORNO=prod` no arranca. La valoración de los usuarios llega como feedback.

- **Auditoría correlacionable**: cada registro lleva fecha, `traza_id` (el mismo run de
  LangSmith), `conversacion_id`, documentos consultados y si vino de caché.
- **Sondas**: `GET /health` (el proceso vive) y `GET /ready` (base de datos y modelos
  configurados; el índice se informa). Container Apps usa `/ready` como readiness probe.

## CI/CD

[pipeline.yml](../.github/workflows/pipeline.yml): en cada PR, [ci.yml](../.github/workflows/ci.yml)
(lint + tests, paridad con PostgreSQL, **gate de evaluaciones** con modelos simulados —falla si
hay fugas entre roles o contrato roto—, Terraform y build de imágenes) y después los entornos
efímeros **dev → staging** en Azure; tras el merge, **main**. Cada entorno
([nube.yml](../.github/workflows/nube.yml), OIDC sin secretos) se despliega, se prueba contra
la web real (matriz de permisos y evaluaciones en LangSmith) y se apaga
([despliegue.md](despliegue.md#pipeline-de-github-actions-entornos-efímeros)).

## Ciclo de pruebas en Azure

`make cycle` (o `STEP=on|test|save|off|report`) ejecuta el ciclo completo y deja
las evidencias en `reports/ciclos/<fecha>/`:

1. **Prender**: `terraform apply` (etapa A) → `.env` desde Key Vault → `make check-infra`
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

## Infraestructura (Terraform)

Tres stacks en [infra/](../infra/): **platform** (recursos), **identidad** (app registration de
Entra ID; solo con tu sesión) y **apps** (Container Apps, login, jobs). Flags de platform:

| Variable | Valores | Efecto |
|---|---|---|
| `alcance` | `modelos` · `completo` | Etapa A (OpenAI, Storage, Key Vault, vector store; app en local) · etapa B, `nube.tfvars` (+ VNet, ACR, Container Apps, PostgreSQL, Managed Redis opcional) |
| `vector_store` | `azure_search` · `qdrant` | AI Search (`search_sku`: free/basic) o Qdrant (`qdrant_modo`: local, cloud, container_efimero) |
| `search_auth` | `api_key` · `rbac` | Clave en Key Vault (dev con Docker) o Managed Identity |
| `content_safety` | bool | Azure AI Content Safety (Prompt Shields), sin claves; `true` por defecto |
| `cache_redis` | bool | Azure Managed Redis (Azure Cache for Redis no admite altas desde el 1-oct-2026) |

Despliegue completo con un comando: [despliegue.md](despliegue.md).

Modelos: `gpt-4o` 2024-11-20 (Legacy, retirada 2027-04-14; reemplazo gpt-5.1) y
`text-embedding-ada-002` v2 (GA hasta 2028-02-09); se cambian por tfvars.

## Estructura

```
app/            FastAPI + orquestador multiagente (LangGraph)
  agents/       orquestador, subgrafo genérico, policy_gate, registro, rag/hr/support, aprobaciones, checkpointer
  graph/        topología (/grafo) y grafo de Studio
  tools/        documentos (listar/leer/buscar), data_query, plantillas de conversación
  prompts/      prompts de sistema versionados (también en LangSmith)
  security/     guardrails, detección (PII/inyección), acceso, Entra ID / Easy Auth, auditoría
  modelos/      proveedor de modelos (Azure OpenAI / OpenAI), errores tipificados, diagnóstico
  cache/        caché semántica (memoria / Redis)
  persistencia/ repositorios SQLAlchemy (SQLite / PostgreSQL), almacén de originales
  servicios/    roles, conversaciones, integridad
  datos/        catálogo de consultas de data_query
  api/          routers HTTP
ingestor/       validación, chunking, orígenes (carpeta / Blob), gestor de documentos
evals/          evaluaciones por capas, juez LLM, LangSmith
web/            interfaz (HTML/CSS/JS sin dependencias) + nginx
infra/          Terraform: platform, identidad, apps, módulos, tests
tests/          unitarios, integración (matriz), servidor simulado
docs/           guías, diseño y capturas
scripts/        entorno local, despliegue en la nube, ciclo de pruebas, capturas
```
