"""Esquemas del grafo para LangGraph Studio: el rol aparece como desplegable con los roles
activos, `top_k` trae un valor por defecto y, si no se indica usuario, se usa uno de prueba con
el rol por defecto (el formulario de Studio no envía los valores por defecto).

`usuario` es una subclase de `Usuario`, así que el estado del grafo lo acepta tal cual.
Solo para Studio (servidor de desarrollo): la API siempre construye el usuario autenticado.
"""

from typing import Literal

from pydantic import BaseModel, Field, create_model

from app.graph.state import EstadoAgente
from app.models.schemas import Usuario


def crear_entrada_studio(roles: list[str]) -> type[BaseModel]:
    if not roles:
        raise ValueError("No hay roles activos para Studio")
    rol = Literal[tuple(roles)]  # type: ignore[valid-type]
    usuario_studio = create_model(
        "UsuarioStudio",
        __base__=Usuario,
        id=(str, Field(default="studio", description="Identificador del usuario de prueba")),
        groups=(
            list[rol],
            Field(
                default=[_rol_por_defecto(roles)],
                min_length=1,
                max_length=1,
                description="Rol de la conversación (uno)",
            ),
        ),
    )
    return create_model(
        "EntradaStudio",
        pregunta=(str, Field(min_length=1, max_length=2000, description="Mensaje del usuario")),
        usuario=(usuario_studio, Field(default_factory=usuario_studio)),
        top_k=(int, Field(default=4, ge=1, le=20, description="Fragmentos por búsqueda")),
    )


def _rol_por_defecto(roles: list[str]) -> str:
    return "public" if "public" in roles else roles[0]


def crear_estado_studio(roles: list[str]) -> type[EstadoAgente]:
    """Estado con usuario de prueba por defecto (el formulario puede no enviarlo)."""
    rol = _rol_por_defecto(roles)
    return create_model(
        "EstadoStudio",
        __base__=EstadoAgente,
        usuario=(Usuario, Field(default_factory=lambda: Usuario(id="studio", groups=[rol]))),
    )
