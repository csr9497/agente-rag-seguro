# Guía de comandos (`make`)

Todos los comandos, qué hacen y qué valores necesitan. Los valores van en **`.env`** (lo crea
`make install` a partir de [.env.example](../.env.example); nunca se sube a git) o **en la
línea de comandos** (`make evals BASE_URL=…`). `make help` los lista.

## ¿Qué quiero hacer?

| Objetivo | Comandos |
|---|---|
| Primer uso tras clonar | `make install` → `make check-models` |
| Probar todo en mi equipo | `make up` → (probar) → `make down` |
| Publicar en Azure | `make deploy` → (probar) → `make cloud-destroy` |
| Mi equipo contra lo publicado en Azure | lo deja arrancado `make deploy` (rearrancar: `make cloud-local`) |
| Ver qué está en marcha | `make status` (local) · `make cloud-status` (Azure) |
| Subir los prompts a LangSmith | `make prompts` (también lo hacen `up`, `studio`, `deploy`) |
| Comprobar que nada se rompe | `make test` · `make lint` · `make evals-mock` |
| Medir la calidad con modelos reales | `make evals BASE_URL=…` |

### Puertos

| | Local (`make up`) | Contra la nube (`make deploy` / `make cloud-local`) |
|---|---|---|
| Web | http://localhost:8080 | http://localhost:8090 |
| API | http://localhost:8000/docs | http://localhost:8090/api/docs |
| Studio | https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024 | https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2025 |

Los dos pueden estar en marcha a la vez.

---

## 1. Local

### `make install`
Primer uso tras clonar: comprueba requisitos, instala dependencias (uv), crea `.env` e
inicializa Terraform (solo con Azure).

| Valor | Dónde | Efecto |
|---|---|---|
| `MODELOS_PROVEEDOR` | `.env` | `azure` (defecto): exige `az`, `az login`, suscripción activa y Terraform · `openai`: solo uv y Docker |

### `make check-models`
Comprueba credenciales, saldo, que los modelos existen y sus capacidades (tool calling,
salida estructurada, embeddings y su dimensión). Hace 4–5 llamadas mínimas.

| Valor | Dónde | Ejemplo |
|---|---|---|
| `MODELOS_PROVEEDOR` | `.env` | `azure` · `openai` |
| Con `openai`: `OPENAI_API_KEY`, `OPENAI_BASE_URL` (vacío = OpenAI), `OPENAI_CHAT_MODEL`, `OPENAI_EMBEDDING_MODEL` | `.env` | `OPENAI_API_KEY=sk-…` · `OPENAI_CHAT_MODEL=gpt-4o` |
| Con `azure`: `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY` (vacío = `az login`), `AZURE_OPENAI_*_DEPLOYMENT` | `.env` | los rellena `make up` |
| `EMBEDDING_DIMENSIONS` | `.env` | `1536` (debe coincidir con el modelo de embeddings) |
| `NO_CALLS=1` | línea de comandos | solo requisitos, sin coste |

Errores y qué revisar: [modelos.md](modelos.md#errores-del-proveedor).

### `make up` · `make down` · `make status`
`up` prepara los modelos, arranca app, web, Qdrant y Redis en Docker, carga los documentos de
ejemplo, publica los prompts en LangSmith, arranca LangGraph Studio y muestra las URLs.
`status` repite las URLs con su estado. `down` para todo lo local.

De dónde salen los modelos:

| Situación | `make up` | `make down` |
|---|---|---|
| `MODELOS_PROVEEDOR=openai` | tu clave de OpenAI (no toca Azure) | para lo local |
| `azure` sin nube desplegada | crea los modelos en Azure y rellena `.env` | para lo local y **borra los modelos** (sin costes) |
| `azure` con la nube desplegada (`make deploy`) | usa los modelos de ese despliegue (no crea nada) | para lo local; la nube sigue intacta |

| Valor | Dónde | Efecto |
|---|---|---|
| `MODELOS_PROVEEDOR` y sus claves | `.env` | ver `check-models` |
| `LANGSMITH_API_KEY` | `.env` | trazas y prompts en LangSmith (opcional) |
| `TRAZAS_MODO` | `.env` | `apagado` (defecto) · `completo` (local) · `enmascarado` |
| `LANGSMITH_PROJECT` | `.env` | proyecto de las trazas locales (defecto `agente-rag-dev`) |
| `PROMPTS_ORIGEN` | `.env` | `local` (defecto): ficheros de `app/prompts/` · `langsmith`: la etiqueta `PROMPTS_ETIQUETA` |

Todo escucha solo en `127.0.0.1`. En un servidor remoto, `make status` imprime el túnel SSH
para abrir las URLs desde tu equipo.

### `make docker-up` · `make docker-down` · `make ingest` · `make studio`
Piezas sueltas, con los valores de `.env`:

| Comando | Qué hace |
|---|---|
| `make docker-up` / `make docker-down` | Solo los contenedores (sin preparar modelos; sin ellos, las respuestas dan 503) |
| `make ingest` | Indexa `ingestor/sample_docs` con registro (como `up`) |
| `make studio` | Publica los prompts y arranca solo LangGraph Studio (:2024). Guía: [studio.md](studio.md) |

### `make prompts`
Publica `app/prompts/*.md` en LangSmith (*Prompts*: `agente-rag-supervisor`,
`agente-rag-generacion`, `agente-rag-guardian`). Si el texto no cambió no crea un commit,
solo mueve la etiqueta. En Studio eliges la versión en cada ejecución (`version_prompts`).

| Valor | Dónde | Efecto |
|---|---|---|
| `LANGSMITH_API_KEY` | `.env` | obligatoria (sin ella no publica nada) |
| `TAG` | línea de comandos | `dev` (defecto) · `prod` para promover: `make prompts TAG=prod` |

---

## 2. Azure

Requisitos: suscripción activa, `az login` (Owner o Contributor + User Access
Administrator), Terraform ≥ 1.9 y Docker con buildx. Detalle: [despliegue.md](despliegue.md).

### `make deploy`
Infraestructura → imágenes `linux/amd64` → prompts en LangSmith (`dev` y `prod`) → apps →
documentos de ejemplo → comprobación → app y Studio de tu equipo contra la nube, en segundo
plano. Al terminar muestra todas las URLs (web en Azure, app local, Studio y LangSmith).
Repetirlo actualiza lo cambiado.

| Valor | Dónde | Efecto |
|---|---|---|
| *(nada)* | — | **Modo prueba**: web solo accesible desde tu IP pública, sin login, con todos los roles |
| `GH_OAUTH_CLIENT_ID`, `GH_OAUTH_CLIENT_SECRET` | `.env` | Web pública con **login de GitHub** (OAuth App: https://github.com/settings/applications/new; callback: el que imprime el comando) |
| `ADMINISTRADORES_GITHUB` | `.env` | Primeros administradores con login de GitHub, JSON: `["ana","luis"]` (defecto: dueño del repo) |
| `LANGSMITH_API_KEY` | `.env` | Trazas (enmascaradas) en el proyecto `agente-rag-ragseg-dev` y la web usa los prompts de LangSmith con la etiqueta `prod` |
| `LOGIN_PROVIDER` | línea de comandos | Forzar el modo: `ip` · `github` · `entra` (requiere poder registrar apps en Entra ID) |
| `ALLOWED_IPS` | línea de comandos | Modo prueba con varias IPs, JSON: `make deploy ALLOWED_IPS='["1.2.3.4/32","5.6.7.8/32"]'` |
| `POSTGRES_LOCATION` | línea de comandos | Región de PostgreSQL si la autodetección no encuentra ninguna (p. ej. `southcentralus`) |

Región, tamaños y modelos: [infra/envs/dev/nube.tfvars](../infra/envs/dev/nube.tfvars).
Logs de cada paso: `data/nube/`.

### `make cloud-status` · `make cloud-destroy`
`cloud-status`: URL de la web, salud de las apps y enlace de LangSmith. `cloud-destroy`: borra
todo lo desplegado y para lo que corre en tu equipo contra la nube (pide escribir «destruir»;
`CONFIRM=yes` lo omite).

### `make cloud-local` · `make cloud-local-stop`
**Lo ejecuta `make deploy` al terminar**; a mano solo para rearrancarlo. Deja en segundo plano
la app de tu equipo (:8090) y LangGraph Studio (:2025) con los modelos, AI Search, Blob y
Content Safety de la nube y base de datos local (`data/nube-local.db`), y vuelve a la
terminal. La configuración va a `data/nube.env`: **tu `.env` no cambia**. Logs en
`data/local-nube.log` y `data/studio-nube.log`. `cloud-local-stop` los detiene.

| Valor | Dónde | Defecto |
|---|---|---|
| `PORT` | línea de comandos | `8090` |
| `STUDIO_PORT` | línea de comandos | `2025` |
| `LANGSMITH_API_KEY` | `.env` | sin ella, trazas apagadas |
| `LANGSMITH_PROJECT_LOCAL_NUBE` | línea de comandos | `agente-rag-local-nube` |
| `TRAZAS_MODO_LOCAL_NUBE` | línea de comandos | `completo` |

### Utilidades

| Comando | Qué hace | Valores |
|---|---|---|
| `make env-from-azure` | Rellena `.env` con endpoints y claves de lo desplegado (Key Vault) | `az login` |
| `make check-infra` | Comprueba cada servicio desplegado (informe en `reports/infra/`) | `az login` |
| `make cycle` | Ciclo completo contra Azure con informe en `reports/ciclos/<fecha>/`. Se niega si hay un despliegue con `make deploy` (comparten el estado de Terraform) | `STEP=on\|test\|save\|off\|report\|all` (defecto `all`) |

---

## 3. Calidad

| Comando | Qué hace | Valores |
|---|---|---|
| `make test` | Tests unitarios (sin red ni Azure) | — |
| `make lint` · `make fmt` | Ruff: comprobar · aplicar formato | — |
| `make test-postgres` | Paridad con PostgreSQL 16 (perfil `postgres` de docker-compose) | `POSTGRES_PASSWORD` en `.env` (lo genera `make install`) |
| `make evals-mock` | Gate de CI: app con modelos simulados + evaluaciones (cero fugas entre roles, contrato) | — |
| `make evals` | Evaluaciones por capas contra una app en marcha | `BASE_URL` (ver abajo) · `JUDGE=1` añade el juez LLM (usa los modelos de `.env`) |
| `make evals-langsmith` | Igual que `evals`, como dataset y experimento en LangSmith | `BASE_URL`, `LANGSMITH_API_KEY` en `.env`, `JUDGE=1` opcional |
| `make integration` | Matriz de escenarios por HTTP (informe en `reports/integracion.md`) | `BASE_URL` |
| `make matrix` | Cobertura de la matriz sin ejecutar nada | — |
| `make tf-validate` | `terraform fmt`, `validate` y tests de los stacks (sin Azure) | Terraform instalado |
| `make setup` · `make sync` | Alternativa con conda (`environment.yml`) | `CONDA_ENV=agente-rag` (defecto) |

`BASE_URL` según dónde corre la app:

| App | `BASE_URL` |
|---|---|
| `make up` / `make docker-up` | `http://localhost:8000` (defecto) |
| `make cloud-local` | `http://localhost:8090/api` |
| Desplegada en Azure (modo prueba, desde tu IP) | `https://<url-de-la-web>/api` |
