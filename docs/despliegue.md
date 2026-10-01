# Despliegue en Azure

La aplicación se publica en Azure Container Apps con **login obligatorio** (Easy Auth). Por
defecto se inicia sesión con **GitHub**: no hace falta registrar aplicaciones en Entra ID (en
tenants universitarios los estudiantes no pueden). Los roles se asignan desde la propia app.

## Una vez

1. **Estado remoto e identidad de despliegue** (ya hecho en este repo: `infra/envs/dev/backend.hcl`):
   `SUBSCRIPTION_ID=<id> GITHUB_REPO=<owner/repo> ./infra/bootstrap/bootstrap.sh`. Crea las
   variables `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `TFSTATE_RG`,
   `TFSTATE_ACCOUNT` y `TFSTATE_CONTAINER` del environment `dev` de GitHub.
2. **OAuth App de GitHub** (login de la web): GitHub → *Settings → Developer settings → OAuth
   Apps → New OAuth App*. Nombre: «Asistente RAG»; *Homepage URL* y *Authorization callback
   URL*: la URL del repositorio por ahora (se cambian tras el primer despliegue). Genera un
   *client secret*.
3. **GitHub → Settings → Environments → dev**: variable `GH_OAUTH_CLIENT_ID` y secret
   `GH_OAUTH_CLIENT_SECRET`. Opcional: `ADMINISTRADORES_GITHUB` (JSON, p. ej. `["ana","luis"]`);
   por defecto, el dueño del repositorio.
4. **GitHub → Settings → Variables → Repository**: `DEPLOY_AZURE=true`.

## Desplegar

Cada push a `main` (o *Actions → Deploy → Run workflow*) ejecuta
[deploy.yml](../.github/workflows/deploy.yml) con OIDC, sin claves:

1. **platform** (`nube.tfvars`): Azure OpenAI (gpt-4o, ada-002), AI Search Free, Storage, Key
   Vault, Content Safety, VNet, PostgreSQL, Container Registry y entorno de Container Apps.
2. **Imágenes** del backend y la web en el registro.
3. **apps**: web pública con Easy Auth (GitHub), backend con ingress interno y Managed
   Identity, jobs de ingesta; el secreto de la OAuth App va a Key Vault.
4. **Documentos de ejemplo** (registro + Blob + índice; idempotente).
5. **Comprobación**: la web responde y exige inicio de sesión.

El resumen del run muestra la URL y el **callback** que hay que poner en la OAuth App
(`https://<web>/.auth/login/github/callback`); pon también la URL de la web como *Homepage URL*.

Desde tu equipo, con `az login`, Terraform y Docker, lo mismo con un comando:

```bash
export GH_OAUTH_CLIENT_ID=... GH_OAUTH_CLIENT_SECRET=...   # nunca en el repositorio
make desplegar      # make estado-nube · make destruir-nube
```

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
| `Login with OIDC` falla | Faltan las variables del environment `dev` (bootstrap) |
| `InsufficientQuota` | Sin cuota del modelo en la región: cambia `location` o `capacity` en `nube.tfvars` |
| GitHub dice «redirect_uri is not associated» | Pon el callback del resumen del run en la OAuth App |
| Tras entrar: «Aún no tienes acceso» | Pide a un administrador que te asigne un rol (tu usuario aparece en pantalla) |
| Respuestas `503` con `codigo` | Error del proveedor de modelos: [modelos.md](modelos.md#errores-del-proveedor) |
| `make levantar` / `make ciclo` se niegan | Comparten el estado con la nube: `make destruir-nube` antes |
