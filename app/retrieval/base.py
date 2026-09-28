"""Interfaces de infraestructura: permiten pasar de local (Qdrant) a Azure (AI Search)
sin tocar el pipeline."""

from typing import Protocol

from app.models.schemas import Chunk, ChunkRecuperado, RespuestaLLM


class Embedder(Protocol):
    def embed(self, textos: list[str]) -> list[list[float]]: ...


class LLM(Protocol):
    def responder(self, system: str, user: str) -> RespuestaLLM: ...


class Retriever(Protocol):
    def ensure_index(self) -> None: ...

    def upsert(self, chunks: list[Chunk], vectores: list[list[float]]) -> None: ...

    def search(
        self, consulta: str, vector: list[float], groups: list[str], top_k: int
    ) -> list[ChunkRecuperado]:
        """Devuelve solo chunks cuya ACL intersecta `groups`. Sin grupos, no devuelve nada."""
        ...
