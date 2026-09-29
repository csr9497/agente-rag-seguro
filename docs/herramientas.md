# Herramientas

Dos grupos: las **herramientas del agente** (lo que el supervisor puede usar para responder)
y las **herramientas de desarrollo** (lo que usas tú para probar, depurar y evaluar).

## Herramientas del agente

El supervisor (gpt-4o con tool-calling) interpreta cada mensaje y elige una o varias. En el
primer turno siempre usa alguna; nunca responde sin haber consultado. Los permisos no dependen
del modelo: el usuario (y su rol) lo inyecta el grafo, nunca los argumentos que elige el LLM.

| Herramienta | Para qué | Argumentos | Permisos |
|---|---|---|---|
| `rag_retrieve` | Búsqueda semántica en los documentos visibles | `consulta` (obligatorio, ≤ 500): consulta curada, autocontenida y sin datos personales | Filtro por rol en el índice + `access_guardrail` contra el registro |
| `listar_documentos` | Qué documentos puede consultar el rol | `grupo` (opcional) | Solo documentos activos del rol |
| `buscar_en_documento` | Búsqueda dentro de un documento ya identificado | `doc_id` (obligatorio), `consulta` (obligatorio) | Igual que `rag_retrieve` |
| `leer_documento` | Fragmentos consecutivos de un documento | `doc_id` (obligatorio), `desde` (≥ 0, por defecto 0), `cantidad` (1–8, por defecto 3) | Igual que `rag_retrieve` |
| `data_query` | Datos internos estructurados (catálogo cerrado, sin SQL generado por el LLM) | `consulta`: `festivos` · `plantilla_por_departamento` · `presupuesto_formacion`; `anio`, `departamento` (opcionales) | `festivos`: Empleado general y RRHH · las otras dos: RRHH |
| `proponer_accion` | Prepara una acción que la persona aprueba en la interfaz | `accion`: `abrir_ticket` (todos) · `solicitar_vacaciones` (Empleado general, RRHH); `asunto`, `descripcion`, `prioridad`, `desde`, `hasta`, `comentario` | Nada se ejecuta sin aprobación humana |
| `conversacion` | Respuesta fija, sin buscar | `tipo`: `saludo` · `agradecimiento` · `despedida` · `ayuda` · `fuera_de_ambito` | — |
| `pedir_aclaracion` | Rebota al usuario cuando el mensaje es impreciso | `pregunta` (≤ 300, termina en «?»), `opciones` (≤ 4) | — |

Además del supervisor, el grafo aplica en cada consulta los **guardrails** (entrada y salida,
por versiones: ver [docs/studio.md](studio.md)), la **caché semántica con permisos** y la
**auditoría**.

## Herramientas de desarrollo

Con el entorno levantado (`make levantar`), `make accesos` muestra el estado y la URL de cada
una. Todo escucha solo en `127.0.0.1`.

| Herramienta | URL | Para qué |
|---|---|---|
| Aplicación web | http://localhost:8080 | Probar como usuario final: elegir rol, conversar, subir documentos, valorar, aprobar acciones |
| API | http://localhost:8000/docs | Probar los endpoints (Swagger). En local, la cabecera `X-Rol` elige el rol |
| Estado | http://localhost:8000/ready | Base de datos, modelos e índice; `/health` solo indica que el proceso vive |
| Topología | http://localhost:8000/grafo | Diagrama del grafo del agente |
| LangGraph Studio | https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024 | Ejecutar el grafo paso a paso, ver el estado de cada nodo y elegir versiones de guardrails. Guía: [docs/studio.md](studio.md) |
| LangSmith · trazas | la muestra `make accesos` | Depurar y monitorizar: cada consulta es una traza con sus nodos, llamadas a gpt-4o (tokens, latencia) y guardrails. Filtra por etiquetas `rol:*`, `guardrail`, `guardrail_entrada:v3-politicas` o por feedback `valoracion_usuario` |
| LangSmith · evaluaciones | la muestra `make accesos` | Dataset `matriz-escenarios` y experimentos: compara versiones del agente y de los guardrails |
| Qdrant | http://localhost:6333/dashboard | Ver las colecciones y los fragmentos indexados (con su campo `acl_groups`) |

### Comandos

| Comando | Qué hace |
|---|---|
| `make instalar` | Primer uso tras clonar: requisitos, dependencias, `.env` y Terraform |
| `make levantar` | Modelos en Azure + Docker + documentos de ejemplo + Studio, y muestra los accesos |
| `make accesos` | Estado y URL de cada herramienta |
| `make apagar` | Para Studio y Docker, elimina los modelos de Azure y limpia `.env` (sin costes) |
| `make test` | Tests unitarios (sin red ni Azure) |
| `make evals-simulado` | Gate de CI en local: matriz de escenarios con modelos simulados |
| `make evals-langsmith BASE_URL=http://localhost:8000` | Matriz contra tu entorno, como experimento en LangSmith (evaluadores por capas + prebuilt) |
| `make studio` | Solo LangGraph Studio |
| `make lint` · `make fmt` | Ruff |

### Ciclo recomendado para mejorar el agente o un guardrail

1. Detecta el problema (conversación, 👎 de un usuario o traza en LangSmith).
2. Reprodúcelo en Studio y mira en qué nodo falla.
3. Si es un guardrail, crea una versión nueva (ver `app/security/versiones.py`) y compárala
   con la actual en Studio, cambiando el contexto.
4. Añade el caso a `tests/integration/escenarios.yaml`.
5. `make evals-langsmith BASE_URL=http://localhost:8000` y compara el experimento con el
   anterior antes de adoptar el cambio.
