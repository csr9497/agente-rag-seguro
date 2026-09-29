# Estado remoto de Terraform (creado por infra/bootstrap/bootstrap.sh). No contiene secretos:
# el acceso es con Entra ID (az login / OIDC) y el rol Storage Blob Data Contributor.
resource_group_name  = "rg-ragseg-tfstate"
storage_account_name = "stragsegtf08894c"
container_name       = "tfstate"
key                  = "dev/platform.tfstate"
use_azuread_auth     = true
