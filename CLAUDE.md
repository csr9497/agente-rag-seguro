# CLAUDE.md — Asistente RAG agéntico seguro

Contexto del proyecto para Claude Code. Léelo al inicio de cada sesión.

## Qué construimos
Un asistente interno donde cada empleado consulta documentos de la empresa en
lenguaje natural y **solo obtiene respuestas de lo que su rol/grupo permite ver**.
Agente LangGraph + RAG con permisos + guardrails, en Azure, con Terraform y CI/CD.

## Stack (decisiones cerradas — no cambiar sin avisar)
- Lenguaje: **Python 3.12**
- Orquestación del agente: **LangGraph**
- LLM + embeddings: **Azure OpenAI** (SDK `openai` con `AzureOpenAI`)
- Retrieval / vector store: **Azure AI Search** (híbrido + security trimming)
- API: **FastAPI**
- Estado + auditoría + checkpointer del agente: **PostgreSQL**
- Caché semántica: **Azure Cache for Redis** (permission-aware)
- Identidad: **Entra ID** (roles/grupos en el token)
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
**Fase 2 — Agente LangGraph local** (Fase 1 completada): grafo
`authorize → input_guardrail → supervisor ⇄ tools → generate → output_guardrail → audit`
con `rag_retrieve`. Siguiente: desplegar en Azure solo gpt-4o + embeddings y validar con la
matriz de integración; después empaquetar y desplegar el resto. El plan completo son
6 fases (RAG → agente → permisos/guardrails → Azure → automatización/HA → pulido).

## Cómo trabajar en este repo
- Antes de codear una feature, confirma en qué fase estamos.
- Cada camino nuevo mantiene el filtrado por permisos y la auditoría.
- Prefiere algo simple y funcionando end-to-end antes de optimizar.
- Cuando toques infra de Azure, primero la interfaz local, luego la implementación real.
