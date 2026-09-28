from typing import Any, Protocol

from pydantic import BaseModel

from app.models.schemas import ChunkRecuperado, Usuario


class Herramienta(Protocol):
    """Herramienta invocable por el supervisor.

    El `usuario` lo inyecta el grafo desde el estado autenticado: nunca forma parte de los
    argumentos que elige el LLM (regla 1: permisos en el dato, no en el prompt).
    """

    nombre: str
    descripcion: str
    args_model: type[BaseModel]

    def ejecutar(self, args: BaseModel, usuario: Usuario, top_k: int) -> list[ChunkRecuperado]: ...


def schema_openai(herramienta: Herramienta) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": herramienta.nombre,
            "description": herramienta.descripcion,
            "parameters": herramienta.args_model.model_json_schema(),
        },
    }
