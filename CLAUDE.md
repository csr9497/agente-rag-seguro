# CLAUDE.md — Asistente RAG agéntico seguro

Contexto del proyecto para Claude Code. Léelo al inicio de cada sesión.

## Qué construimos
Un asistente interno donde cada empleado consulta documentos de la empresa en
lenguaje natural y **solo obtiene respuestas de lo que su rol/grupo permite ver**.
Agente LangGraph + RAG con permisos + guardrails, en Azure, con Terraform y CI/CD.

## Stack (decisiones cerradas — no cambiar sin avisar)
- Lenguaje: **Python 3.12**
- Orquestación del agente: **LangGraph**
- LLM + embeddings: **proveedor configurable** (`MODELOS_PROVEEDOR`): **Azure OpenAI** por
  defecto y en el despliegue, u **OpenAI / endpoint compatible** con clave directa. Mismo SDK
  `openai` (`AzureOpenAI` / `OpenAI`) en `app/modelos/`; errores tipificados (`ModeloError`)
  y diagnóstico con `make verificar-modelos` (ver docs/modelos.md)
- Retrieval / vector store: **Azure AI Search** (híbrido + security trimming)
- API: **FastAPI**
- Estado + auditoría: **PostgreSQL** (sin checkpointer de LangGraph: ver docs/diseno-fase-3.md §4)
- Caché semántica: **Azure Cache for Redis** (permission-aware)
- Identidad: login con **Easy Auth** de Container Apps: **GitHub** por defecto (roles
  asignados a cada persona desde la app, tabla `usuario_roles`) o **Entra ID** (app roles en
  el token) si el tenant permite registrar aplicaciones
- Guardrails: **Azure AI Content Safety** (prompt shields) + detección de PII
- Secretos: **Azure Key Vault** + Managed Identity
- Contenedores: **Docker** (multi-stage)
- Hosting: **Azure Container Apps**
- IaC: **Terraform**
- CI/CD: **GitHub Actions**
- Validación de datos: **Pydantic v2**
- Gestión de dependencias: **uv**

## Arquitectura (resumen)
El agente es un grafo LangGraph:

`authorize → input_guardrail → supervisor ⇄ tools → generate → output_guardrail → audit`

- **supervisor**: decide y delega en la herramienta correcta (tool-calling, estilo
  ReAct); itera hasta poder responder.
- **tools**: `rag_retrieve` (AI Search filtrado por grupos), `data_query` (API/BD
  interna), `action_tool` (acciones con efecto, con aprobación humana).

## Reglas NO negociables (seguridad)
1. **Permisos en el dato, no en el prompt.** Todo retrieval de AI Search DEBE
   filtrar por los grupos del usuario (campo ACL en cada chunk). Nunca confiar en
   el prompt para restringir acceso.
2. **Caché permission-aware.** La clave de la caché semántica DEBE incluir el scope
   de permisos del usuario. Nunca servir una respuesta cacheada construida con
   datos que el usuario no puede ver.
3. **Secretos solo en Key Vault** (o `.env` en local, nunca commiteado). Nunca
   hardcodear claves ni connection strings.
4. **Toda consulta se audita**: usuario, pregunta, fuentes citadas y respuesta.
5. **Salida estructurada con Pydantic** para todo lo que consume otro componente.
6. El LLM siempre responde con **citación** y con **salida de escape** ("si no está
   en el contexto, dilo").

## Estructura del repo
```
.
├── app/                # FastAPI + grafo LangGraph
│   ├── graph/          # nodos y edges del agente
│   ├── tools/          # rag_retrieve, data_query, action_tool
│   ├── security/       # permisos, guardrails, PII, auditoría
│   ├── cache/          # caché semántica (permission-aware)
│   └── models/         # esquemas Pydantic
├── ingestor/           # pipeline de ingesta (chunk → embed → index)
├── infra/              # Terraform (módulos: search, openai, containerapps, postgres, keyvault)
├── .github/workflows/  # CI/CD
├── tests/
├── Dockerfile
└── docker-compose.yml  # entorno local (app + Postgres + vector store)
```

## Convenciones
- Type hints + Pydantic v2 en todo el I/O.
- Tests con `pytest`; el **filtro de permisos** y cada **tool** llevan test propio.
- Commits pequeños y descriptivos.
- No añadir dependencias nuevas sin justificarlas.
- Interfaces para las piezas de infra (retriever, cache, llm) para poder pasar de
  local (Qdrant/FAISS) a Azure (AI Search) sin reescribir la lógica.

## Comandos
- Local: `docker compose up`
- Tests: `pytest`
- Lint / formato: `ruff check` · `ruff format`

## Fase actual
**Local completo; pendiente despliegue en Azure.** Hecho: agente LangGraph con roles y
permisos gestionados desde la UI, access_guardrail contra el registro, integridad
índice↔registro, guardrails, caché semántica con permisos (memoria/Redis), memoria de
conversación, feedback, Prompt Shields (Content Safety), data_query, acciones con aprobación humana, Entra ID (JWT), trazas
LangSmith, evaluaciones por capas y CI (GitHub Actions). Terraform listo (`alcance`,
`vector_store`, Managed Redis, PostgreSQL; stacks platform, identidad y apps) pero **no
aplicado**: no tocar Azure sin indicación explícita del usuario. Despliegue completo con
GitHub Actions (deploy.yml, OIDC) o `make desplegar`, con login de GitHub vía Easy Auth (ver
docs/despliegue.md); el deploy de CI está desactivado hasta `DEPLOY_AZURE=true`. Proveedor de modelos configurable
(docs/modelos.md). Siguiente: primer `make desplegar` y pruebas en la nube.

## Cómo trabajar en este repo
- Antes de codear una feature, confirma en qué fase estamos.
- Cada camino nuevo mantiene el filtrado por permisos y la auditoría.
- Prefiere algo simple y funcionando end-to-end antes de optimizar.
- Cuando toques infra de Azure, primero la interfaz local, luego la implementación real.
