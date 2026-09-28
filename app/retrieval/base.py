"""Interfaces de infraestructura: permiten pasar de local (Qdrant) a Azure (AI Search)
y cambiar de proveedor de modelos sin tocar el grafo del agente."""

from typing import Any, Protocol

from app.models.schemas import (
    Chunk,
    ChunkRecuperado,
    DecisionSupervisor,
    DocumentoIndexado,
    RespuestaLLM,
)


class Embedder(Protocol):
    def embed(self, textos: list[str]) -> list[list[float]]: ...


class LLM(Protocol):
    def responder(self, system: str, user: str) -> RespuestaLLM: ...


class Supervisor(Protocol):
    """LLM con tool-calling que decide qué herramientas invocar."""

    def decidir(
        self, mensajes: list[dict[str, Any]], herramientas: list[dict[str, Any]]
    ) -> DecisionSupervisor: ...


class Retriever(Protocol):
    def ensure_index(self) -> None: ...

    def upsert(self, chunks: list[Chunk], vectores: list[list[float]]) -> None: ...

    def search(
        self,
        consulta: str,
        vector: list[float],
        groups: list[str],
        top_k: int,
        doc_id: str | None = None,
    ) -> list[ChunkRecuperado]:
        """Devuelve solo chunks cuya ACL intersecta `groups` (y del documento `doc_id`, si se
        indica). Sin grupos, no devuelve nada."""
        ...

    def get_document_chunks(self, doc_id: str, groups: list[str]) -> list[Chunk]:
        """Chunks del documento en orden, solo si es visible para `groups`; si no, []."""
        ...

    # ------------------------------------------------------------ gestión por documento
    def delete_document(self, doc_id: str) -> int:
        """Borra todos los chunks del documento. Devuelve cuántos había."""
        ...

    def document_hash(self, doc_id: str) -> str | None:
        """Hash del documento indexado, o None si no existe."""
        ...

    def find_by_hash(self, doc_hash: str, group: str) -> list[str]:
        """doc_ids con ese hash dentro del grupo (detección de duplicados)."""
        ...

    def list_documents(self, groups: list[str]) -> list[DocumentoIndexado]:
        """Documentos visibles para `groups`. Sin grupos, lista vacía."""
        ...
