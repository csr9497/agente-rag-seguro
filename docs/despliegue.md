# Despliegue en Azure

La aplicación se publica en Azure Container Apps con **login obligatorio** (Easy Auth). Por
defecto se inicia sesión con **GitHub**: no hace falta registrar aplicaciones en Entra ID (en
tenants universitarios los estudiantes no pueden). Los roles se asignan desde la propia app.

## Desde tu equipo (lo más rápido)

Con `az login`, Terraform y Docker:

```bash
make desplegar      # make estado-nube · make destruir-nube
```

Sin más configuración se despliega en **modo prueba**: la web solo es accesible desde tu IP
pública (el resto de Internet recibe 403), sin login y con todos los roles, como en local.
Para abrirla a otras personas con login de GitHub, añade a `.env` `GH_OAUTH_CLIENT_ID` y
`GH_OAUTH_CLIENT_SECRET` (OAuth App, paso 2 de abajo) y repite `make desplegar`.

## App en tu equipo contra los recursos de la nube

`make desplegar` la deja arrancada al terminar, en segundo plano (`make local-nube` la
rearranca, `make local-nube-parar` la detiene): app en http://localhost:8090 y Studio en
`https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2025`.

Escribe en `data/nube.env` (no en `.env`) los endpoints y claves de la nube (Key Vault),
registra en una base local (`data/nube-local.db`) los documentos que hay en Blob —sin volver a
calcular embeddings— y arranca web y API en un proceso de tu equipo, más LangGraph Studio
contra la nube (`baseUrl=http://127.0.0.1:2025`). Usa los modelos, AI Search, Blob y Content
Safety de Azure con tu `az login`; la base de datos es local porque PostgreSQL de la nube solo
admite servicios de Azure (conversaciones y roles asignados no se comparten con la nube).
Trazas en el proyecto de LangSmith `agente-rag-local-nube`.

## Con GitHub Actions: una vez

Todo se guarda en GitHub: repositorio → *Settings → Secrets and variables → Actions*.

1. **Credenciales de Azure** (con qué despliega GitHub). En el portal de Azure abre la Cloud
   Shell (icono `>_`, Bash) y ejecuta
   [scripts/credenciales_azure.sh](../scripts/credenciales_azure.sh):
   ```bash
   curl -sL https://raw.githubusercontent.com/csr9497/agente-rag-seguro/main/scripts/credenciales_azure.sh | bash
   ```
   Crea un service principal (rol Owner en la suscripción + acceso al estado de Terraform) y
   muestra los cuatro **secrets** que hay que crear: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
   `AZURE_SUBSCRIPTION_ID` y `AZURE_CLIENT_SECRET`. Si tu tenant no permite crear service
   principals, el script indica la alternativa sin contraseña (OIDC con `bootstrap.sh`).
2. **Login de la web** (con qué inician sesión las personas): GitHub → *Settings → Developer
   settings → OAuth Apps → New OAuth App*. Nombre «Asistente RAG»; *Homepage URL* y
   *Authorization callback URL*: la URL del repositorio por ahora (se cambian tras el primer
   despliegue). Genera un *client secret*. Guarda la **variable** `GH_OAUTH_CLIENT_ID` y el
   **secret** `GH_OAUTH_CLIENT_SECRET`. Opcional: variable `ADMINISTRADORES_GITHUB` (JSON, p. ej.
   `["ana","luis"]`); por defecto, el dueño del repositorio.
3. **Activar**: **variable** `DEPLOY_AZURE=true`.

## Desplegar

Cada push a `main` (o *Actions → Deploy → Run workflow*) ejecuta
[deploy.yml](../.github/workflows/deploy.yml) con esas credenciales:

1. **platform** (`nube.tfvars`): Azure OpenAI (gpt-4o, ada-002), AI Search Free, Storage, Key
   Vault, Content Safety, VNet, PostgreSQL, Container Registry y entorno de Container Apps.
2. **Imágenes** del backend y la web en el registro.
3. **apps**: web pública con Easy Auth (GitHub), backend con ingress interno y Managed
   Identity, jobs de ingesta; el secreto de la OAuth App va a Key Vault.
4. **Documentos de ejemplo** (registro + Blob + índice; idempotente).
5. **Comprobación**: la web responde y exige inicio de sesión.

El resumen del run muestra la URL y el **callback** que hay que poner en la OAuth App
(`https://<web>/.auth/login/github/callback`); pon también la URL de la web como *Homepage URL*.

Desde tu equipo es lo mismo con `make desplegar`; los valores de cada modo (IP, GitHub,
Entra ID) están en [comandos.md](comandos.md#make-desplegar).

## Acceso y roles

- Cualquiera puede iniciar sesión con GitHub, pero **sin roles no ve nada** (deny by default).
  Quien no tiene roles ve su usuario (`github:nombre`) para pedir acceso.
- Los administradores (`ADMINISTRADORES_GITHUB`) arrancan con todos los roles y asignan roles
  a otras personas en *Roles y permisos → Personas y sus roles*. Cada cambio se audita.
- El backend no es accesible desde Internet y solo acepta la identidad de Easy Auth si llega
  desde nginx con un secreto compartido (`AUTH_MODO=easyauth`, `PROXY_SECRETO`).
- Con `ENTORNO=prod` la API no arranca sin login ni con modos de depuración.

### Alternativa: login con Entra ID

Si tu cuenta puede registrar aplicaciones en el tenant: `LOGIN_PROVEEDOR=entra make desplegar`.
El stack [infra/identidad](../infra/identidad) crea el app registration con los app roles
`administrador`, `rrhh`, `finanzas` y `public` (asignación obligatoria; tú recibes los cuatro;
más personas en [identidad.tfvars](../infra/envs/dev/identidad.tfvars)).

## Costes

Coste fijo mientras exista: PostgreSQL Flexible (B1ms), Container Apps (1 réplica mínima de
backend y web), Container Registry Basic y Log Analytics. AI Search y Content Safety en tier
gratuito; Azure OpenAI por token. Al terminar las pruebas: `make destruir-nube`.

## Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| El job `platform` se omite | Falta `DEPLOY_AZURE=true` |
| `az login` / Terraform: credenciales no válidas | Revisa los cuatro secrets `AZURE_*`; el secreto caduca al año (vuelve a ejecutar el script) |
| `AuthorizationFailed` al leer el estado | El service principal necesita *Storage Blob Data Contributor* en la cuenta del estado (lo asigna el script) |
| `InsufficientQuota` | Sin cuota del modelo en la región: cambia `location` o `capacity` en `nube.tfvars` |
| GitHub dice «redirect_uri is not associated» | Pon el callback del resumen del run en la OAuth App |
| Tras entrar: «Aún no tienes acceso» | Pide a un administrador que te asigne un rol (tu usuario aparece en pantalla) |
| Respuestas `503` con `codigo` | Error del proveedor de modelos: [modelos.md](modelos.md#errores-del-proveedor) |
| `make levantar` / `make ciclo` se niegan | Comparten el estado con la nube: `make destruir-nube` antes |
