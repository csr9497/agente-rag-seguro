# Funciones compartidas por los scripts de Azure (se cargan con `source`).

# La etapa A (make levantar / make ciclo) y la nube (make desplegar) comparten el estado de
# platform: aplicar o destruir la etapa A con la nube desplegada la desmontaría.
proteger_nube() {
  local alcance
  alcance=$(terraform -chdir=infra/platform output -raw alcance 2> /dev/null || true)
  if [[ $alcance == completo ]]; then
    echo "⛔ Hay un despliegue en la nube (alcance=completo) en este estado de Terraform."
    echo "   Esta operación lo desmontaría. Usa la nube (make estado-nube) o elimínala antes"
    echo "   con make destruir-nube."
    exit 1
  fi
}
