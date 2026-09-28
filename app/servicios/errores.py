class PermisoDenegadoError(Exception):
    """El rol de actuación no tiene el permiso necesario (HTTP 403)."""


class NoEncontradoError(Exception):
    """No existe o no es visible para el rol (HTTP 404: no se distingue para no revelar)."""


class DatosInvalidosError(Exception):
    """La operación viola una regla de negocio (HTTP 422)."""
