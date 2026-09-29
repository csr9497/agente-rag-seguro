"""Pedir aclaración: cuando el mensaje no permite formular una consulta precisa, el supervisor
rebota al usuario con una pregunta (y opciones) en lugar de buscar a ciegas.

La pregunta la redacta el modelo, pero acotada: debe ser una pregunta, corta y con pocas
opciones; no aporta datos (no hay contexto recuperado) y pasa por el guardrail de salida.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.models.schemas import Aclaracion, Usuario
from app.tools.base import ResultadoHerramienta

Opcion = Annotated[str, Field(min_length=1, max_length=80)]


class AclaracionArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pregunta: str = Field(
        min_length=3,
        max_length=300,
        pattern=r"\?\s*$",
        description="Pregunta breve al usuario para concretar qué necesita (termina en '?')",
    )
    opciones: list[Opcion] = Field(
        default_factory=list,
        max_length=4,
        description="Hasta 4 reformulaciones concretas que el usuario puede elegir",
    )


class PedirAclaracion:
    nombre = "pedir_aclaracion"
    descripcion = (
        "Pregunta al usuario para concretar su consulta cuando es ambigua o incompleta (no se "
        "sabe el tema, el periodo o a qué se refiere). No busca ni responde nada."
    )
    args_model = AclaracionArgs

    def ejecutar(self, args: AclaracionArgs, usuario: Usuario, top_k: int) -> ResultadoHerramienta:
        return ResultadoHerramienta(
            aclaracion=Aclaracion(pregunta=args.pregunta, opciones=args.opciones),
            nota="Aclaración preparada para el usuario. Contesta LISTO.",
        )
