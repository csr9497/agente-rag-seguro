# Despliegue en Azure

`make desplegar` publica la aplicación en Azure Container Apps con **login obligatorio de
Entra ID** y deja cargados los documentos de ejemplo. Al terminar muestra la URL.

## Requisitos (una vez)

| Qué | Cómo comprobarlo |
|---|---|
| Suscripción de Azure **activa** | `az account show --query state` → `Enabled` |
| Azure CLI con sesión iniciada | `az login` · `az account set -s <suscripción>` si tienes varias |
| Permisos en la suscripción: Owner (o Contributor + User Access Administrator) | Terraform crea recursos y asigna roles a las identidades |
| Poder **registrar aplicaciones** en Entra ID | Con una cuenta personal de Azure eres administrador de tu tenant; en un tenant corporativo, rol *Application Developer* |
| Cuota de gpt-4o en la región (`infra/envs/dev/nube.tfvars`) | Portal → Azure OpenAI → Quotas |
| Terraform ≥ 1.9, Docker Desktop (con buildx) y git | `make desplegar` lo comprueba |
| Estado remoto de Terraform | `SUBSCRIPTION_ID=<id> GITHUB_REPO=<owner/repo> ./infra/bootstrap/bootstrap.sh` (ya hecho en este repo: `infra/envs/dev/backend.hcl`) |

## Comandos

```bash
make desplegar      # todo (la primera vez tarda: PostgreSQL y Container Apps); repetirlo actualiza
make estado-nube    # URL y salud de las apps
make destruir-nube  # elimina todo (pide escribir «destruir»)
```

`make desplegar` hace, en orden:

1. **platform** (`nube.tfvars`): Azure OpenAI (gpt-4o, ada-002), AI Search Free, Storage,
   Key Vault, Content Safety, VNet, PostgreSQL, Container Registry y el entorno de Container Apps.
2. **identidad**: app registration con los roles `administrador`, `rrhh`, `finanzas` y
   `public`; solo entran usuarios con algún rol; tú recibes los cuatro. El secreto del
   cliente va a Key Vault.
3. **Imágenes** `linux/amd64` (también desde Mac con Apple Silicon) en el registro.
4. **apps**: web pública con Easy Auth (redirige al login), backend con ingress interno y
   Managed Identity (sin claves), jobs de ingesta.
5. **Documentos de ejemplo** (registro + Blob + índice).
6. **Comprobación**: salud de las revisiones y que la web exige login.

Los logs de cada paso quedan en `data/nube/`.

## Acceso de otras personas

Añade su Object ID en [infra/envs/dev/identidad.tfvars](../infra/envs/dev/identidad.tfvars)
y repite `make desplegar`, o asígnalo en el portal: *Entra ID → Enterprise applications →
Asistente RAG → Users and groups → Add assignment* (elige el rol).

## Cómo queda la seguridad

- Nadie llega a la web sin iniciar sesión; los roles de la app salen de los app roles de Entra ID.
- El backend no es accesible desde Internet; solo acepta la identidad de Easy Auth si llega
  desde nginx con un secreto compartido (`AUTH_MODO=easyauth`, `PROXY_SECRETO`).
- Con `ENTORNO=prod` la API no arranca sin login ni con modos de depuración.
- Secretos en Key Vault, leídos con Managed Identity.

## Actualizaciones con GitHub Actions

Tras el primer `make desplegar`, [deploy.yml](../.github/workflows/deploy.yml) actualiza
platform, imágenes y apps en cada push a `main` (OIDC, sin secretos) si la variable de
repositorio `DEPLOY_AZURE=true`. La identidad de GitHub no puede crear app registrations: el
stack identidad solo se aplica con `make desplegar`.

## Costes

Coste fijo mientras exista: PostgreSQL Flexible (B1ms), Container Apps (1 réplica mínima de
backend y web), Container Registry Basic y Log Analytics. AI Search y Content Safety en tier
gratuito; Azure OpenAI por token. Usa `make destruir-nube` al terminar las pruebas.

## Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| `Authorization_RequestDenied` en el paso 2 | Tu cuenta no puede registrar aplicaciones: pide el rol *Application Developer* |
| `InsufficientQuota` en el paso 1 | Sin cuota del modelo en la región: cambia `location` o `capacity` en `nube.tfvars` |
| La web pide login pero da error tras entrar | Tu usuario no tiene rol asignado (`app_role_assignment_required`) |
| Respuestas `503` con `codigo` | Error del proveedor de modelos: [docs/modelos.md](modelos.md#errores-del-proveedor) |
| `make levantar` / `make ciclo` se niegan a ejecutar | Comparten el estado con la nube: elimínala antes con `make destruir-nube` |
