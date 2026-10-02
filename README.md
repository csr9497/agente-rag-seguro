# Asistente RAG agéntico seguro

Asistente interno en el que cada empleado pregunta en lenguaje natural por los documentos y
datos de la empresa y **solo obtiene respuestas de lo que su rol permite ver**, siempre con
citas.

- **Permisos en el dato**: filtro por rol en el índice y verificación de cada fragmento contra
  el registro antes de que el modelo lo vea.
- **Guardrails** de entrada y salida (inyección, PII, fugas) y caché que respeta los permisos.
- **Orquestador multiagente LangGraph**: documentos (`rag_agent`), RR.HH. (`hr_agent`) y soporte
  (`support_agent`); lo que escribe (casos, tickets) lo confirma la persona o lo aprueba el rol que toca.
- **Auditoría** de toda consulta, trazas en LangSmith y evaluaciones por capas en CI.
- **Modelos** de Azure OpenAI u OpenAI (o cualquier endpoint compatible).

![Respuesta con cita](docs/ui/2-respuesta-citada.png)

| Sin acceso: no se filtra nada | Recursos Humanos ve sus documentos | Nada se escribe sin confirmación |
|---|---|---|
| ![](docs/ui/3-sin-acceso.png) | ![](docs/ui/4-rrhh.png) | ![](docs/ui/5-confirmacion.png) |

## Cómo levantarlo: elige un camino

| Camino | Qué necesitas | Coste | Dónde corre |
|---|---|---|---|
| **A. Local con OpenAI** (el más rápido) | uv, Docker Desktop y una clave de OpenAI (o de un endpoint compatible) | Solo los tokens de OpenAI | Todo en tu equipo |
| **B. Local con Azure OpenAI** | Lo de A (sin clave) + suscripción de Azure, Azure CLI (`az login`) y Terraform | Tokens de Azure OpenAI | App en tu equipo; solo los modelos en Azure |
| **C. Nube (Azure)** | Lo de B + permisos de Owner en la suscripción | PostgreSQL, Container Apps, registro… mientras exista | Todo en Azure (modelos: Azure OpenAI) |

Ninguno necesita GitHub: basta con clonar el repo. GitHub Actions solo hace falta si quieres el
CI/CD automático ([docs/despliegue.md](docs/despliegue.md#pipeline-de-github-actions-entornos-efímeros)).

### A. Local con OpenAI

```bash
git clone https://github.com/csr9497/agente-rag-seguro.git && cd agente-rag-seguro
make install        # comprueba uv y Docker, instala dependencias y crea .env
```

En `.env` pon `MODELOS_PROVEEDOR=openai` y `OPENAI_API_KEY=sk-…` (para otro endpoint
compatible, también `OPENAI_BASE_URL`). Después:

```bash
make check-models   # comprueba la clave, el saldo y los modelos
make up             # app + base de datos + documentos de ejemplo + Studio
```

Abre http://localhost:8080, elige un rol (Empleado general, Recursos Humanos, Finanzas,
Administrador) y pregunta. `make status` muestra todas las URLs y `make down` lo apaga.
No se toca Azure en ningún momento.

### B. Local con Azure OpenAI

```bash
az login                         # y az account set -s <suscripción> si tienes varias
make bootstrap                   # una sola vez por suscripción: estado remoto de Terraform
make install                     # con MODELOS_PROVEEDOR=azure en .env (es el valor por defecto)
make up                          # crea gpt-4o y ada-002 en Azure, rellena .env y arranca todo
```

`make down` apaga lo local **y borra los modelos de Azure** (sin coste mientras está apagado).

### C. Nube (Azure)

```bash
az login
make bootstrap                   # una sola vez por suscripción (si ya lo hiciste en B, no hace falta)
make deploy                      # crea todo en Azure y muestra la URL
make cloud-status                # URL y salud
make cloud-destroy               # borra todo lo desplegado (hazlo al terminar: tiene coste fijo)
```

Sin más configuración la web queda en **modo prueba**: solo se abre desde tu IP y sin login.
Para abrirla a otras personas con login (GitHub o Entra ID) y para el detalle de costes y
problemas frecuentes: [docs/despliegue.md](docs/despliegue.md). `make deploy` deja además en tu
equipo la app (http://localhost:8090) y Studio conectados a los recursos de la nube.

### Extras

- **LangSmith** (opcional): con `LANGSMITH_API_KEY` en `.env`, trazas de cada consulta y los
  prompts de `app/prompts/` publicados en LangSmith (`make prompts`).
- **En un servidor remoto** los comandos de A y B funcionan igual. La app solo escucha en
  `127.0.0.1`: ábrela desde tu equipo con un túnel SSH (`make status` imprime el comando).
- **Sin modelos**, `make docker-up` arranca la interfaz y los permisos (las respuestas dan 503).

## Comandos

| Comando | Qué hace |
|---|---|
| `make install` · `make check-models` | Primer uso · comprobar los modelos |
| `make up` · `make down` · `make status` | Entorno local completo · pararlo · URLs |
| `make bootstrap` | Paso 0 en Azure (una vez por suscripción): estado remoto de Terraform |
| `make deploy` · `make cloud-status` · `make cloud-destroy` | Publicar en Azure (deja también app y Studio locales contra la nube) · estado · borrarlo |
| `make prompts` | Prompts de `app/prompts/` a LangSmith |
| `make test` · `make evals-mock` | Tests · gate de evaluaciones con modelos simulados |
| `make evals BASE_URL=…` | Evaluaciones con modelos reales contra una app en marcha |

Guía de todos los comandos y los valores que necesita cada uno: [docs/comandos.md](docs/comandos.md).

## Documentación

| | |
|---|---|
| [docs/comandos.md](docs/comandos.md) | Guía de los `make` y los valores a configurar en cada caso |
| [docs/modelos.md](docs/modelos.md) | Proveedores de modelos, setup de cada uno y errores (saldo, credenciales, capacidades) |
| [docs/despliegue.md](docs/despliegue.md) | Despliegue en Azure paso a paso (desde tu equipo o con GitHub Actions) |
| [docs/arquitectura.md](docs/arquitectura.md) | Orquestador y agentes, seguridad, API, pruebas, observabilidad e infraestructura |
| [docs/herramientas.md](docs/herramientas.md) · [docs/studio.md](docs/studio.md) | Herramientas del agente y LangGraph Studio |
| [CLAUDE.md](CLAUDE.md) | Reglas del proyecto |

Stack: Python 3.12 · LangGraph · FastAPI · Azure OpenAI / OpenAI · AI Search / Qdrant ·
PostgreSQL (RLS) · Redis · Terraform · GitHub Actions.
