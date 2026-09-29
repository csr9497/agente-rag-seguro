"""Esquema de entrada del grafo para LangGraph Studio: el rol aparece como desplegable con
los roles activos y `top_k` trae un valor por defecto.

`usuario` es una subclase de `Usuario`, así que el estado del grafo lo acepta tal cual.
"""

from typing import Literal

from pydantic import BaseModel, Field, create_model

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
                default=[roles[0] if "public" not in roles else "public"],
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
