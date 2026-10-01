# Comandos (`make`)

Referencia de todos los comandos: qué hacen y qué valores necesitan. Los valores van en
**`.env`** (se crea con `make instalar` a partir de [.env.example](../.env.example); nunca se
sube a git) o **en la línea de comandos** (`make evals BASE_URL=…`). `make help` los lista.

## ¿Qué quiero hacer?

| Objetivo | Comandos |
|---|---|
| Probar en mi equipo | `make instalar` → `make verificar-modelos` → `make levantar` |
| Publicar en Azure | `make desplegar` → (probar) → `make destruir-nube` |
| Mi equipo contra lo publicado en Azure | `make local-nube` |
| Comprobar que nada se rompe | `make test` · `make lint` · `make evals-simulado` |
| Medir la calidad con modelos reales | `make evals BASE_URL=…` |

---

## 1. Entorno local

### `make instalar`
Primer uso tras clonar: comprueba requisitos, instala dependencias (uv), crea `.env` e
inicializa Terraform (solo con Azure).

| Valor | Dónde | Efecto |
|---|---|---|
| `MODELOS_PROVEEDOR` | `.env` | `azure` (por defecto): exige `az`, `az login`, suscripción activa y Terraform · `openai`: solo uv y Docker |

### `make verificar-modelos`
Comprueba credenciales, saldo, que los modelos existen y sus capacidades (tool calling,
salida estructurada, embeddings y su dimensión). Hace 4–5 llamadas mínimas.

| Valor | Dónde | Ejemplo |
|---|---|---|
| `MODELOS_PROVEEDOR` | `.env` | `azure` · `openai` |
| Con `openai`: `OPENAI_API_KEY`, `OPENAI_BASE_URL` (vacío = OpenAI), `OPENAI_CHAT_MODEL`, `OPENAI_EMBEDDING_MODEL` | `.env` | `OPENAI_API_KEY=sk-…` · `OPENAI_CHAT_MODEL=gpt-4o` |
| Con `azure`: `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY` (vacío = `az login`), `AZURE_OPENAI_*_DEPLOYMENT` | `.env` | los rellena `make levantar` / `make env-from-azure` |
| `EMBEDDING_DIMENSIONS` | `.env` | `1536` (debe coincidir con el modelo de embeddings) |
| `SIN_LLAMADAS=1` | línea de comandos | solo requisitos, sin coste |

Errores y qué revisar: [modelos.md](modelos.md#errores-del-proveedor).

### `make levantar` · `make accesos` · `make apagar`
`levantar`: con `azure`, crea los modelos en Azure y rellena `.env`; verifica los modelos;
arranca app, web, Qdrant y Redis en Docker; carga los documentos de ejemplo; arranca
LangGraph Studio y muestra las URLs. `accesos` repite las URLs con su estado. `apagar` para
todo y, con `azure`, borra los modelos (sin costes).

| Valor | Dónde | Efecto |
|---|---|---|
| `MODELOS_PROVEEDOR` y sus claves | `.env` | ver `verificar-modelos` |
| `LANGSMITH_API_KEY` | `.env` | trazas y evaluaciones en LangSmith (opcional) |
| `TRAZAS_MODO` | `.env` | `apagado` (defecto) · `completo` (local) · `enmascarado` |
| `LANGSMITH_PROJECT` | `.env` | proyecto de las trazas locales (defecto `agente-rag-dev`) |

URLs (solo en `127.0.0.1`): web http://localhost:8080 · API http://localhost:8000/docs ·
Studio, Qdrant y LangSmith los muestra `make accesos`. En un servidor remoto, `make accesos`
imprime el túnel SSH para abrirlas desde tu equipo.

### `make up` · `make down` · `make ingest` · `make studio`
Piezas sueltas: `up`/`down` arrancan o paran los contenedores (sin modelos, las respuestas
dan 503); `ingest` indexa `ingestor/sample_docs` con registro (como `levantar`); `studio`
arranca solo LangGraph Studio (http://127.0.0.1:2024). Usan los mismos valores de `.env`.

### `make setup` · `make sync`
Alternativa con conda (`environment.yml`): `CONDA_ENV=agente-rag` (defecto) en la línea de
comandos. Con uv basta `make instalar`.

---

## 2. Calidad

| Comando | Qué hace | Valores |
|---|---|---|
| `make test` | Tests unitarios (sin red ni Azure) | — |
| `make lint` · `make fmt` | Ruff: comprobar · aplicar formato | — |
| `make test-postgres` | Paridad con PostgreSQL 16 (perfil `postgres` de docker-compose) | `POSTGRES_PASSWORD` en `.env` (lo genera `make instalar`) |
| `make evals-simulado` | Gate de CI: app con modelos simulados + evaluaciones (cero fugas entre roles, contrato) | — |
| `make evals` | Evaluaciones por capas contra una app en marcha | `BASE_URL` (ver abajo) · `JUEZ=1` añade el juez LLM (usa los modelos de `.env`) |
| `make evals-langsmith` | Igual que `evals`, como dataset y experimento en LangSmith | `BASE_URL`, `LANGSMITH_API_KEY` en `.env`, `JUEZ=1` opcional |
| `make integration` | Matriz de escenarios por HTTP (informe en `reports/integracion.md`) | `BASE_URL` |
| `make matriz` | Cobertura de la matriz sin ejecutar nada | — |
| `make tf-validate` | `terraform fmt`, `validate` y tests de los stacks (sin Azure) | Terraform instalado |

`BASE_URL` según dónde corre la app:

| App | `BASE_URL` |
|---|---|
| `make levantar` / `make up` (Docker) | `http://localhost:8000` (defecto) |
| `make local-nube` | `http://localhost:8090/api` |
| Desplegada en Azure (modo prueba, desde tu IP) | `https://<url-de-la-web>/api` |

---

## 3. Azure

Requisitos comunes: suscripción activa, `az login` (Owner o Contributor + User Access
Administrator), Terraform ≥ 1.9 y Docker con buildx. Detalle: [despliegue.md](despliegue.md).

### `make desplegar`
Infraestructura → imágenes `linux/amd64` → apps → documentos de ejemplo → comprobación. Al
terminar muestra la URL de la web y el enlace de LangSmith. Repetirlo actualiza lo cambiado.

| Valor | Dónde | Efecto |
|---|---|---|
| *(nada)* | — | **Modo prueba**: web solo accesible desde tu IP pública, sin login, con todos los roles |
| `GH_OAUTH_CLIENT_ID`, `GH_OAUTH_CLIENT_SECRET` | `.env` | Web pública con **login de GitHub** (OAuth App: https://github.com/settings/applications/new; callback: el que imprime el comando) |
| `ADMINISTRADORES_GITHUB` | `.env` | Primeros administradores con login de GitHub, JSON: `["ana","luis"]` (defecto: dueño del repo) |
| `LANGSMITH_API_KEY` | `.env` | Trazas en LangSmith (enmascaradas) en el proyecto `agente-rag-ragseg-dev` |
| `LOGIN_PROVEEDOR` | línea de comandos | Forzar el modo: `ip` · `github` · `entra` (esta última requiere poder registrar apps en Entra ID) |
| `IPS_PERMITIDAS` | línea de comandos | Modo prueba con varias IPs, JSON: `IPS_PERMITIDAS='["1.2.3.4/32","5.6.7.8/32"]'` |
| `POSTGRES_LOCATION` | línea de comandos | Región de PostgreSQL si la autodetección no encuentra ninguna (p. ej. `southcentralus`) |

Región, tamaños y modelos: [infra/envs/dev/nube.tfvars](../infra/envs/dev/nube.tfvars).
Logs de cada paso: `data/nube/`.

### `make estado-nube` · `make destruir-nube`
`estado-nube`: URL de la web, salud de las apps y enlace de LangSmith. `destruir-nube`: borra
todo lo desplegado (pide escribir «destruir»; `CONFIRMAR=si` lo omite).

### `make local-nube`
La app en tu equipo (http://localhost:8090) con los modelos, AI Search, Blob y Content Safety
de la nube y base de datos local (`data/nube-local.db`). Rellena `.env` desde Key Vault.

| Valor | Dónde | Defecto |
|---|---|---|
| `PUERTO` | línea de comandos | `8090` |
| `LANGSMITH_API_KEY` | `.env` | sin ella, trazas apagadas |
| `LANGSMITH_PROJECT_LOCAL_NUBE` | línea de comandos | `agente-rag-local-nube` |
| `TRAZAS_MODO_LOCAL_NUBE` | línea de comandos | `completo` |

### Etapa A (modelos en Azure, app en local) y utilidades

| Comando | Qué hace | Valores |
|---|---|---|
| `make env-from-azure` | Rellena `.env` con endpoints y claves de lo desplegado (Key Vault) | `az login` |
| `make validar-infra` | Comprueba cada servicio desplegado (informe en `reports/infra/`) | `az login` |
| `make ciclo` | Ciclo completo contra Azure con informe en `reports/ciclos/<fecha>/` | `PASO=prender\|probar\|guardar\|apagar\|informe\|todo` (defecto `todo`) |

`make levantar`, `make apagar` y `make ciclo` se niegan a ejecutarse si hay un despliegue
completo en la nube (comparten el estado de Terraform): `make destruir-nube` antes.
