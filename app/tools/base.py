from typing import Annotated, Any, Protocol

from pydantic import AfterValidator, BaseModel, Field

from app.models.schemas import ChunkRecuperado, Usuario

PATRON_GRUPO = r"^[a-z0-9][a-z0-9_\-]{0,63}$"
PATRON_DOC_ID = r"^[a-z0-9][a-z0-9_\-]{0,63}/[\w\-. /]+$"


def _sin_saltos(doc_id: str) -> str:
    if ".." in doc_id:
        raise ValueError("el identificador no puede contener '..'")
    return doc_id


# Identificador de documento que puede pedir el LLM: <grupo>/<ruta>, sin '..'.
DocId = Annotated[
    str,
    Field(pattern=PATRON_DOC_ID, max_length=365, description="Identificador, p. ej. public/x.md"),
    AfterValidator(_sin_saltos),
]

# Misma respuesta para "no existe" y "no tienes acceso": no se revela qué existe.
SIN_ACCESO = "El documento no existe o no tienes acceso a él."


class ResultadoHerramienta(BaseModel):
    chunks: list[ChunkRecuperado] = Field(
        default_factory=list, description="Fragmentos citables que pasan al contexto"
    )
    nota: str | None = Field(default=None, description="Mensaje para el supervisor")


class Herramienta(Protocol):
    """Herramienta invocable por el supervisor.

    El `usuario` lo inyecta el grafo desde el estado autenticado: nunca forma parte de los
    argumentos que elige el LLM (regla 1: permisos en el dato, no en el prompt).
    """

    nombre: str
    descripcion: str
    args_model: type[BaseModel]

    def ejecutar(self, args: BaseModel, usuario: Usuario, top_k: int) -> ResultadoHerramienta: ...


def schema_openai(herramienta: Herramienta) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": herramienta.nombre,
            "description": herramienta.descripcion,
            "parameters": herramienta.args_model.model_json_schema(),
        },
    }
