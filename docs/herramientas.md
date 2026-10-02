# Herramientas

Dos grupos: las **herramientas del agente** (lo que el supervisor puede usar para responder)
y las **herramientas de desarrollo** (lo que usas tú para probar, depurar y evaluar).

## Herramientas del agente

Un **supervisor** interpreta cada mensaje y lo reparte entre agentes especializados; cada
agente tiene sus propias tools. Los permisos no dependen del modelo: el usuario (roles, scopes
y grupos) lo fija `authorize` desde la identidad, y `policy_gate` decide en código si cada
llamada se ejecuta, necesita confirmación o aprobación, o se deniega. Detalle de modos y
scopes: [arquitectura.md](arquitectura.md#agentes-y-tools).

**Supervisor**

| Herramienta | Para qué |
|---|---|
| `delegar_rag_agent` / `delegar_hr_agent` / `delegar_support_agent` | Encarga a ese agente su parte del mensaje (varios a la vez se ejecutan en paralelo) |
| `conversacion` | Plantilla u orientación sin buscar: `saludo` · `agradecimiento` · `despedida` · `ayuda` · `fuera_de_ambito` |
| `pedir_aclaracion` | Rebota al usuario cuando el mensaje es impreciso (`pregunta`, ≤ 4 `opciones`) |

**rag_agent** (documentos; siempre usa una tool antes de responder)

| Herramienta | Para qué | Argumentos | Permisos |
|---|---|---|---|
| `search_documents` | Búsqueda semántica en los documentos visibles | `consulta` | Filtro ACL en el índice + re-chequeo de cada fragmento contra el registro |
| `listar_documentos` | Qué documentos puede consultar el usuario | `grupo` (opcional) | Solo documentos activos de sus grupos |
| `buscar_en_documento` / `leer_documento` | Dentro de un documento ya identificado | `doc_id`, `consulta` / `desde`, `cantidad` | Ajeno e inexistente responden igual |
| `get_document_metadata` | Título, dueño, clasificación y versión | `doc_id` | Igual que inexistente si no es visible |
| `data_query` | Datos internos (catálogo cerrado, sin SQL del LLM) | `consulta`: `festivos` · `plantilla_por_departamento` · `presupuesto_formacion` | `festivos`: Empleado general y RRHH · las otras dos: RRHH |
| `request_document_access` | Pedir acceso a un documento que no ve | `titulo`, `motivo` | La persona lo confirma; la respuesta no revela si existe |

**hr_agent**: `search_hr_policies`, `get_my_hr_cases`, `create_hr_case` y `add_hr_case_note`
(las dos últimas las confirma la persona). **support_agent**: `search_it_kb`,
`search_my_tickets`, `create_ticket` (un P1 lo aprueba `it_support`) y `add_ticket_comment`.

Además, el orquestador aplica en cada consulta los **guardrails** de entrada y salida, el
**verifier** de citas, la **caché semántica con permisos** y la **auditoría**.

## Herramientas de desarrollo

Con el entorno levantado (`make up`), `make status` muestra el estado y la URL de cada
una. Todo escucha solo en `127.0.0.1`.

| Herramienta | URL | Para qué |
|---|---|---|
| Aplicación web | http://localhost:8080 | Probar como usuario final: elegir rol, conversar, subir documentos, valorar, confirmar y aprobar |
| API | http://localhost:8000/docs | Probar los endpoints (Swagger). En local, la cabecera `X-Rol` elige el rol |
| Estado | http://localhost:8000/ready | Base de datos, modelos e índice; `/health` solo indica que el proceso vive |
| Topología | http://localhost:8000/grafo | Diagrama del orquestador (con `EXPONER_TOPOLOGIA=true`) |
| LangGraph Studio | https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024 | Ejecutar el orquestador paso a paso, ver el estado de cada nodo y resolver aprobaciones. Guía: [docs/studio.md](studio.md) |
| LangSmith · trazas | la muestra `make status` | Depurar y monitorizar: cada consulta es una traza con sus nodos, llamadas al modelo (tokens, latencia) y guardrails. Filtra por etiquetas `rol:*`, `guardrail`, `guardrail_entrada:v3-politicas` o por feedback `valoracion_usuario` |
| LangSmith · evaluaciones | la muestra `make status` | Dataset `matriz-escenarios` y experimentos: compara versiones del agente y de los guardrails |
| Qdrant | http://localhost:6333/dashboard | Ver las colecciones y los fragmentos indexados (con su campo `acl_groups`) |

### Comandos

Todos los `make`, con sus valores y ejemplos: [docs/comandos.md](comandos.md).

### Ciclo recomendado para mejorar el agente o un guardrail

1. Detecta el problema (conversación, 👎 de un usuario o traza en LangSmith).
2. Reprodúcelo en Studio y mira en qué nodo falla.
3. Si es un guardrail, crea una versión nueva (ver `app/security/versiones.py`) y compárala
   con la actual cambiando `GUARDRAIL_ENTRADA` / `GUARDRAIL_SALIDA`.
4. Añade el caso a `tests/integration/escenarios.yaml`.
5. `make evals-langsmith BASE_URL=http://localhost:8000` y compara el experimento con el
   anterior antes de adoptar el cambio.
