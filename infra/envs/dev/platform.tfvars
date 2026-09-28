project     = "ragseg"
environment = "dev"
location    = "eastus2"

# dev: acceso público (con RBAC) para poder desarrollar en local contra Azure OpenAI.
# prod: true → OpenAI, AI Search y Storage solo por private endpoint desde la VNet.
private_endpoints_enabled = false
openai_local_auth_enabled = true

# Object IDs de tu usuario o grupo de desarrollo (az ad signed-in-user show --query id -o tsv)
developer_principal_ids = []
