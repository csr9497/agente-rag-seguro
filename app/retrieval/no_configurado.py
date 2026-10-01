"""Proveedor de modelos ausente: la app arranca y el resto del grafo funciona, pero cualquier
llamada a LLM o embeddings falla con un error explícito (la API lo traduce a 503)."""

from typing import Any, NoReturn


class ProveedorNoConfiguradoError(RuntimeError):
    pass


class ModelosNoConfigurados:
    """Implementa Embedder, LLM y Supervisor lanzando ProveedorNoConfiguradoError."""

    def __init__(self, faltan: list[str], proveedor: str = "azure") -> None:
        self._mensaje = (
            f"Modelos no configurados (MODELOS_PROVEEDOR={proveedor}): faltan {', '.join(faltan)}"
        )

    def _fallar(self) -> NoReturn:
        raise ProveedorNoConfiguradoError(self._mensaje)

    def embed(self, textos: list[str]) -> list[list[float]]:
        self._fallar()

    def responder(self, system: str, user: str) -> Any:
        self._fallar()

    def decidir(
        self,
        mensajes: list[dict[str, Any]],
        herramientas: list[dict[str, Any]],
        obligar_herramienta: bool = False,
    ) -> Any:
        self._fallar()
