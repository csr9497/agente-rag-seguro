# Quién puede entrar en la app desplegada y con qué roles (además de quien despliega, que
# recibe roles_desplegador). Clave: Object ID del usuario o grupo de Entra ID
# (az ad user show --id <correo> --query id -o tsv). Aplicar: make desplegar.
asignaciones = {
  # "00000000-0000-0000-0000-000000000000" = ["public"]
  # "11111111-1111-1111-1111-111111111111" = ["rrhh", "public"]
}
