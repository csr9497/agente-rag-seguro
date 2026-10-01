# Proveedor de modelos

El agente usa tres piezas de modelo detrás de interfaces ([app/retrieval/base.py](../app/retrieval/base.py)):
**supervisor** (tool calling), **generación** (salida estructurada) y **embeddings**. El
proveedor se elige con `MODELOS_PROVEEDOR` en `.env`; el grafo, los permisos, la caché y la
auditoría no cambian.

| `MODELOS_PROVEEDOR` | Para qué | Autenticación |
|---|---|---|
| `azure` (por defecto) | Azure OpenAI; es lo que usa el despliegue en Azure (Terraform) | Clave (solo local) o Entra ID: `az login` en local, Managed Identity en Azure |
| `openai` | OpenAI o cualquier endpoint compatible con su API (OpenRouter, Groq, vLLM, Ollama…) | `OPENAI_API_KEY` (y `OPENAI_BASE_URL` si no es OpenAI) |

Antes de levantar nada: **`make verificar-modelos`** comprueba los requisitos del proveedor
elegido y hace cuatro llamadas mínimas (ver [Capacidades](#capacidades-que-pide-el-código)).
`SIN_LLAMADAS=1` solo revisa requisitos, sin coste. `make levantar` lo ejecuta y se detiene
si algo falla.

## Opción A · Azure OpenAI

### Setup necesario

1. **Suscripción de Azure activa** (estado `Enabled`). Con *Azure for Students*, la
   suscripción se deshabilita al agotar el crédito: el diagnóstico lo detecta.
2. **Azure CLI** instalado ([guía](https://learn.microsoft.com/cli/azure/install-azure-cli))
   y sesión iniciada:
   ```bash
   az login
   az account set -s <id-o-nombre-de-la-suscripción>   # si tienes varias
   az account show --query "{nombre:name, estado:state}"
   ```
3. **Permisos**: para crear los recursos con Terraform, Contributor + User Access
   Administrator en la suscripción (o el bootstrap: [infra/bootstrap](../infra/bootstrap/bootstrap.sh)).
   Para usar los modelos sin clave, tu usuario necesita el rol **Cognitive Services OpenAI
   User** sobre el recurso (Terraform lo asigna al usuario de `az login`).
4. **Cuota** del modelo en la región (gpt-4o Standard en eastus2 en la suscripción actual;
   ver [infra/envs/dev](../infra/envs/dev)).
5. **Terraform** ≥ 1.9 y el estado remoto (bootstrap, una sola vez).

### Uso

```bash
make instalar          # comprueba uv, Docker, az, terraform, az login y la suscripción
make levantar          # crea gpt-4o + ada-002, rellena .env, verifica y arranca todo
make apagar            # borra los modelos (sin costes)
```

Variables (las rellena `make env-from-azure`):

```env
MODELOS_PROVEEDOR=azure
AZURE_OPENAI_ENDPOINT=https://<recurso>.openai.azure.com/
AZURE_OPENAI_API_KEY=            # vacío = Entra ID (az login / Managed Identity)
AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-4o           # nombre del deployment, no del modelo
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-ada-002
```

Prompt Shields (Azure AI Content Safety) es independiente del proveedor de modelos: es
opcional (`CONTENT_SAFETY_ENDPOINT`) y sin él se aplican los guardrails locales.

## Opción B · OpenAI o endpoint compatible (clave directa)

No necesita Azure CLI, suscripción ni Terraform: solo la clave del proveedor en `.env`
(nunca en el repositorio).

```env
MODELOS_PROVEEDOR=openai
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=                 # vacío = OpenAI; p. ej. https://openrouter.ai/api/v1
OPENAI_CHAT_MODEL=gpt-4o
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
```

```bash
make instalar && make verificar-modelos && make levantar
```

- **Ollama / vLLM local** (sin clave): `OPENAI_BASE_URL=http://host.docker.internal:11434/v1`
  para la app en Docker (`http://localhost:11434/v1` desde el host). El modelo de chat debe
  admitir tool calling y salida estructurada.
- **Embeddings y dimensión**: el índice se crea con `EMBEDDING_DIMENSIONS`. Si cambias de
  modelo de embeddings (p. ej. ada-002 → `text-embedding-3-large`, 3072), cambia la
  dimensión y **reindexa**: los vectores de modelos distintos no son comparables. La app lo
  comprueba en cada llamada y responde `capacidad_no_soportada` si no coincide.

## Capacidades que pide el código

| Pieza | Capacidad | Si el modelo no la tiene |
|---|---|---|
| Supervisor | Tool calling con `tool_choice="required"` | `capacidad_no_soportada` (tool calling) |
| Generación (y el juez de las evaluaciones) | Salida estructurada (`response_format` con JSON schema) | `capacidad_no_soportada` (salida estructurada) |
| Búsqueda e ingesta | Embeddings con `EMBEDDING_DIMENSIONS` dimensiones | `capacidad_no_soportada` (embeddings) |
| Todas | `temperature=0` (los modelos de razonamiento lo rechazan) | `capacidad_no_soportada` |

## Errores del proveedor

Cada error del SDK o de la credencial se convierte en un `ModeloError` tipificado
([app/modelos/errores.py](../app/modelos/errores.py)). El usuario recibe un mensaje sin
detalles internos; el log de la app guarda el detalle y qué revisar; la consulta queda en la
auditoría (hallazgo `servicio_no_disponible`, `modelo:<tipo>`).

| `codigo` | HTTP | Causa típica | Qué revisar |
|---|---|---|---|
| `credenciales_invalidas` | 503 | Clave incorrecta, sin `az login`, Managed Identity sin token | `AZURE_OPENAI_API_KEY` / `az login` · `OPENAI_API_KEY` |
| `sin_permiso` | 503 | Falta el rol en el recurso, red cerrada, modelo no habilitado | Rol *Cognitive Services OpenAI User*, firewall · acceso del proyecto |
| `modelo_no_encontrado` | 503 | Deployment o modelo inexistente, endpoint o versión de API incorrectos | `*_DEPLOYMENT` / `OPENAI_*_MODEL`, `AZURE_OPENAI_API_VERSION` |
| `saldo_agotado` | 503 | `insufficient_quota`, 402, suscripción deshabilitada | Facturación / crédito / estado de la suscripción |
| `limite_de_peticiones` | 429 | Límite de peticiones o tokens por minuto (el SDK ya reintentó) | Cuota TPM, `MODELOS_MAX_REINTENTOS` |
| `capacidad_no_soportada` | 503 | Ver la tabla anterior | Cambiar de modelo o de dimensión |
| `contexto_excedido` | 422 | Prompt mayor que la ventana del modelo | `RETRIEVAL_TOP_K`, `MAX_TURNOS_HISTORIAL` |
| `filtro_contenido` | — | Filtro del proveedor | Se trata como consulta bloqueada y auditada (no es un error) |
| `conexion` | 503 | URL incorrecta, red, proxy, private endpoint | `AZURE_OPENAI_ENDPOINT` / `OPENAI_BASE_URL` |
| `servicio_no_disponible` | 502 | 5xx del proveedor | Página de estado del proveedor |

Respuesta de la API: `{"detail": "<mensaje para el usuario>", "codigo": "<codigo>"}`.
`make verificar-modelos` usa la misma clasificación y omite el resto de pruebas tras un
fallo de credenciales, permisos, saldo o conexión.
