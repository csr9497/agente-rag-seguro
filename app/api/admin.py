from fastapi import APIRouter

from app.api.dependencias import Actor, ServiciosDep
from app.servicios.errores import PermisoDenegadoError
from app.servicios.integridad import InformeIntegridad

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/integridad", response_model=InformeIntegridad)
def integridad(actor: Actor, servicios: ServiciosDep) -> InformeIntegridad:
    """Contrasta índice y registro y pone en cuarentena lo inconsistente."""
    if not actor.puede("administrar_roles"):
        raise PermisoDenegadoError(f"El rol '{actor.id}' no puede ver la integridad")
    return servicios.verificar_integridad()
