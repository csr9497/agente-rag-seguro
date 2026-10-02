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
  y diagnóstico con `make check-models` (ver docs/modelos.md)
- Retrieval / vector store: **Azure AI Search** (híbrido + security trimming)
- API: **FastAPI**
- Estado + auditoría: **PostgreSQL** (checkpointer de LangGraph cifrado, `CHECKPOINT_CLAVE`)
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
El asistente es un orquestador multiagente en LangGraph (`app/agents/orquestador.py`):

`authorize → input_guardrail → cache_lookup → supervisor → [agentes en paralelo] → sintetizar ⇄ verifier → output_guardrail → cache_store → audit`
(con `escalate_human` si el verifier falla 3 veces)

- **supervisor**: delega cada parte del mensaje en el agente que toca (tool-calling, `Send`),
  responde con plantilla/orientación o pide aclaración.
- **agentes** (`app/agents/`): `rag_agent` (documentos filtrados por grupos + re-chequeo
  contra el registro, `data_query`), `hr_agent` (casos de RR.HH. con RLS) y `support_agent`
  (tickets con RLS). Cada uno corre su subgrafo con `policy_gate`: las escrituras requieren
  confirmación de la persona o aprobación del rol que toca (human-in-the-loop, tabla `approvals`).
- Estado de los hilos en PostgreSQL con checkpointer cifrado (`CHECKPOINT_CLAVE`).

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
├── app/                # FastAPI + orquestador multiagente (LangGraph)
│   ├── agents/         # orquestador, subgrafo, policy_gate, rag/hr/support, aprobaciones
│   ├── graph/          # topología (/grafo) y grafo de Studio
│   ├── tools/          # documentos (listar/leer/buscar), data_query
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
- Local: `make up` (guía de todos los make: docs/comandos.md)
- Tests: `pytest`
- Lint / formato: `ruff check` · `ruff format`

## Fase actual
**Local completo; pendiente despliegue en Azure.** Hecho: orquestador multiagente
(rag_agent, hr_agent, support_agent) con aprobaciones humanas, roles y
permisos gestionados desde la UI, re-chequeo contra el registro, integridad
índice↔registro, guardrails, caché semántica con permisos (memoria/Redis), memoria de
conversación, feedback, Prompt Shields (Content Safety), data_query, acciones con aprobación humana, Entra ID (JWT), trazas
LangSmith, evaluaciones por capas y CI (GitHub Actions). Terraform listo (`alcance`,
`vector_store`, Managed Redis, PostgreSQL; stacks platform, identidad y apps) pero **no
aplicado**: no tocar Azure sin indicación explícita del usuario. Pipeline de GitHub
Actions con entornos efímeros (pipeline.yml → nube.yml, OIDC): PR → CI → dev → staging;
merge → main; cada entorno se despliega, se prueba y se apaga (activo con `DEPLOY_AZURE=true`;
ver docs/despliegue.md). `make deploy` desde el equipo, con login de GitHub vía Easy Auth. Proveedor de modelos configurable
(docs/modelos.md). Siguiente: primer `make deploy` y pruebas en la nube.

## Cómo trabajar en este repo
- Antes de codear una feature, confirma en qué fase estamos.
- Cada camino nuevo mantiene el filtrado por permisos y la auditoría.
- Prefiere algo simple y funcionando end-to-end antes de optimizar.
- Cuando toques infra de Azure, primero la interfaz local, luego la implementación real.
