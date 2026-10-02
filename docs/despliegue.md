# Despliegue en Azure

La aplicación se publica en Azure Container Apps con **login obligatorio** (Easy Auth). Por
defecto se inicia sesión con **GitHub**: no hace falta registrar aplicaciones en Entra ID (en
tenants universitarios los estudiantes no pueden). Los roles se asignan desde la propia app.

## Desde tu equipo (lo más rápido, sin GitHub)

Con `az login`, Terraform y Docker:

```bash
make bootstrap   # paso 0, una sola vez por suscripción (ver abajo)
make deploy      # crea todo y muestra la URL · make cloud-status · make cloud-destroy
```

**Paso 0 (`make bootstrap`)**: crea en tu suscripción el almacén del estado de Terraform
(grupo `rg-ragseg-tfstate`, una cuenta de almacenamiento sin claves) y escribe
[infra/envs/dev/backend.hcl](../infra/envs/dev/backend.hcl) apuntando a él. Requiere Owner en
la suscripción (asigna roles). Es idempotente. Si clonas el repo de otra persona,
`backend.hcl` apunta a su estado: `make deploy` lo detecta y te pide ejecutar `make bootstrap`.
No lo borres mientras tengas algo desplegado: es donde Terraform sabe qué existe.

Los modelos en la nube son siempre **Azure OpenAI** (los crea Terraform). OpenAI con clave
directa se usa en local (camino A del [README](../README.md#a-local-con-openai)).

Sin más configuración se despliega en **modo prueba**: la web solo es accesible desde tu IP
pública (el resto de Internet recibe 403), sin login y con todos los roles, como en local.
Para abrirla a otras personas con login de GitHub, añade a `.env` `GH_OAUTH_CLIENT_ID` y
`GH_OAUTH_CLIENT_SECRET` (OAuth App, paso 2 de abajo) y repite `make deploy`.

## App en tu equipo contra los recursos de la nube

`make deploy` la deja arrancada al terminar, en segundo plano (`make cloud-local` la
rearranca, `make cloud-local-stop` la detiene): app en http://localhost:8090 y Studio en
`https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2025`.

Escribe en `data/nube.env` (no en `.env`) los endpoints y claves de la nube (Key Vault),
registra en una base local (`data/nube-local.db`) los documentos que hay en Blob —sin volver a
calcular embeddings— y arranca web y API en un proceso de tu equipo, más LangGraph Studio
contra la nube (`baseUrl=http://127.0.0.1:2025`). Usa los modelos, AI Search, Blob y Content
Safety de Azure con tu `az login`; la base de datos es local porque PostgreSQL de la nube solo
admite servicios de Azure (conversaciones y roles asignados no se comparten con la nube).
Trazas en el proyecto de LangSmith `agente-rag-local-nube`.

## Pipeline de GitHub Actions: entornos efímeros

Cada cambio se prueba en Azure en entornos que **se crean, se prueban y se apagan** solos
([pipeline.yml](../.github/workflows/pipeline.yml)):

| Evento | Qué corre | Resultado |
|---|---|---|
| PR hacia `main` (cada push) | CI → **dev** → **staging** (este, solo si dev pasa) | Checks obligatorios para el merge |
| Merge a `main` | CI → **main** | Todos los tests sobre lo integrado |
| *Actions → Pipeline → Run workflow* | CI → el entorno elegido | Prueba manual |

**CI** ([ci.yml](../.github/workflows/ci.yml)) va antes y sin Azure: lint, tests, PostgreSQL,
gate de evaluaciones con modelos simulados, Terraform y build de imágenes. Si falla, no se
despliega nada.

**Cada entorno** ([nube.yml](../.github/workflows/nube.yml)) es un solo job:

1. `scripts/nube.sh desplegar` con `ENTORNO=<entorno>`: recursos propios (`rg-ragseg-<entorno>`)
   y estado propio (`<entorno>/*.tfstate`). La web va en **modo `ip`**: solo la IP del runner
   puede abrirla, sin login.
2. **Tests contra la web desplegada**: matriz de escenarios (permisos por rol) y evaluaciones
   por capas, con dataset y experimento en LangSmith. Trazas en el proyecto
   `agente-rag-ragseg-<entorno>`; prompts con la etiqueta del entorno (solo `main` mueve `prod`).
3. **Apagado siempre** (`make cloud-destroy`), también si los tests fallan o se cancela el run.

El resumen del run muestra la URL y los informes; los informes y logs quedan como artefactos.

Los entornos van **de uno en uno** en toda la suscripción (grupo de concurrencia
`azure-suscripcion`): AI Search Free y Content Safety F0 admiten uno por suscripción. Un ciclo
completo tarda unos 40–60 min.

**Limpieza nocturna** ([limpieza.yml](../.github/workflows/limpieza.yml), 06:00 UTC): borra
cualquier `rg-ragseg-<entorno>` que siga encendido, su estado y lo que quede en borrado suave
(`./scripts/nube.sh limpiar`). Ojo: también apaga un `make deploy` hecho desde tu equipo.

### Configuración (una vez)

1. **Identidad y estado**: `make bootstrap GITHUB_REPO=<owner>/<repo>` (responde `s` para
   crear las variables del environment `dev`). Con `GITHUB_REPO`, además del estado remoto crea
   la identidad de despliegue (Managed Identity con OIDC, sin secretos) y sus roles. Crea las credenciales federadas de los environments
   `dev`, `staging` y `main` con los dos formatos de sujeto que usa GitHub: `repo:<owner>/<repo>:…`
   y, en repositorios nuevos, el de ids inmutables `repo:<owner>@<id>/<repo>@<id>:…`. Si
   `azure/login` falla con **AADSTS700213**, el error muestra el sujeto que GitHub envió;
   regístralo así:
   ```bash
   for env in dev staging main; do
     az identity federated-credential create --name "github-$env-ids" \
       --identity-name id-gh-ragseg-deployer-dev --resource-group rg-ragseg-tfstate \
       --issuer https://token.actions.githubusercontent.com \
       --subject "repo:<owner>@<id>/<repo>@<id>:environment:$env" --audiences api://AzureADTokenExchange
   done
   ```
2. **Variables de repositorio** `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` y
   `AZURE_SUBSCRIPTION_ID` (las mismas del environment `dev`, para que staging y main las vean).
3. **Secret** `LANGSMITH_API_KEY` (opcional: trazas y experimentos).
4. **Activar**: variable `DEPLOY_AZURE=true`. Sin ella solo corre el CI.
5. **Protección de `main`**: exigir los checks de CI, dev y staging antes del merge.

### Login con GitHub (despliegue permanente)

Los entornos del pipeline no usan login. Para una web pública con login, desde tu equipo:
crea una OAuth App (GitHub → *Settings → Developer settings → OAuth Apps*), pon
`GH_OAUTH_CLIENT_ID` y `GH_OAUTH_CLIENT_SECRET` en `.env` (opcional `ADMINISTRADORES_GITHUB`,
JSON) y `make deploy`. El comando imprime el **callback**
(`https://<web>/.auth/login/github/callback`) que hay que poner en la OAuth App. Los valores
de cada modo (IP, GitHub, Entra ID) están en [comandos.md](comandos.md#make-deploy).

## Acceso y roles

- Cualquiera puede iniciar sesión con GitHub, pero **sin roles no ve nada** (deny by default).
  Quien no tiene roles ve su usuario (`github:nombre`) para pedir acceso.
- Los administradores (`ADMINISTRADORES_GITHUB`) arrancan con todos los roles y asignan roles
  a otras personas en *Roles y permisos → Personas y sus roles*. Cada cambio se audita.
- El backend no es accesible desde Internet y solo acepta la identidad de Easy Auth si llega
  desde nginx con un secreto compartido (`AUTH_MODO=easyauth`, `PROXY_SECRETO`).
- Con `ENTORNO=prod` la API no arranca sin login ni con modos de depuración.

### Alternativa: login con Entra ID

Si tu cuenta puede registrar aplicaciones en el tenant: `make deploy LOGIN_PROVIDER=entra`.
El stack [infra/identidad](../infra/identidad) crea el app registration con los app roles
`administrador`, `rrhh`, `finanzas` y `public` (asignación obligatoria; tú recibes los cuatro;
más personas en [identidad.tfvars](../infra/envs/dev/identidad.tfvars)).

## Costes

Coste fijo mientras exista: PostgreSQL Flexible (B1ms), Container Apps (1 réplica mínima de
backend y web), Container Registry Basic y Log Analytics. AI Search y Content Safety en tier
gratuito; Azure OpenAI por token. Al terminar las pruebas: `make cloud-destroy`.

## Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| Los jobs dev/staging/main se omiten | Falta la variable `DEPLOY_AZURE=true` |
| `staging` aparece cancelado | Otro entorno ocupaba la suscripción y llegó un tercero a la cola: vuelve a ejecutar el run |
| `az login` en el pipeline: `AADSTS70021` (no matching federated identity) | Falta la credencial federada del environment (paso 1 de la configuración) |
| `AuthorizationFailed` al leer el estado | El service principal necesita *Storage Blob Data Contributor* en la cuenta del estado (lo asigna el script) |
| `InsufficientQuota` | Sin cuota del modelo en la región: cambia `location` o `capacity` en `nube.tfvars` |
| GitHub dice «redirect_uri is not associated» | Pon el callback del resumen del run en la OAuth App |
| Tras entrar: «Aún no tienes acceso» | Pide a un administrador que te asigne un rol (tu usuario aparece en pantalla) |
| Respuestas `503` con `codigo` | Error del proveedor de modelos: [modelos.md](modelos.md#errores-del-proveedor) |
| `make cycle` se niega | Comparte el estado con la nube: `make cloud-destroy` antes (`make up` sí funciona: usa los modelos de la nube) |
