import uuid
from collections.abc import Iterator

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    Record,
    VectorParams,
)

from app.models.schemas import Chunk, ChunkRecuperado, DocumentoIndexado, indice_chunk

_CAMPOS_INDEXADOS = ("acl_groups", "doc_id", "doc_hash")


def _es(campo: str, valor: str) -> FieldCondition:
    return FieldCondition(key=campo, match=MatchValue(value=valor))


class QdrantRetriever:
    def __init__(self, client: QdrantClient, collection: str, dimensions: int) -> None:
        self._client = client
        self._collection = collection
        self._dimensions = dimensions

    def ensure_index(self) -> None:
        """Idempotente: crea la colección si falta y los índices de payload que falten
        (también en colecciones creadas por versiones anteriores)."""
        if not self._client.collection_exists(self._collection):
            self._client.create_collection(
                self._collection,
                vectors_config=VectorParams(size=self._dimensions, distance=Distance.COSINE),
            )
        existentes = self._client.get_collection(self._collection).payload_schema or {}
        for campo in _CAMPOS_INDEXADOS:
            if campo not in existentes:
                self._client.create_payload_index(
                    self._collection, field_name=campo, field_schema=PayloadSchemaType.KEYWORD
                )

    def upsert(self, chunks: list[Chunk], vectores: list[list[float]]) -> None:
        puntos = [
            PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, chunk.chunk_id)),
                vector=vector,
                payload=chunk.model_dump(),
            )
            for chunk, vector in zip(chunks, vectores, strict=True)
        ]
        self._client.upsert(self._collection, points=puntos)

    def search(
        self,
        consulta: str,
        vector: list[float],
        groups: list[str],
        top_k: int,
        doc_id: str | None = None,
    ) -> list[ChunkRecuperado]:
        if not groups or not self._client.collection_exists(self._collection):
            return []  # índice aún sin crear (despliegue nuevo): no hay nada visible
        condiciones = [FieldCondition(key="acl_groups", match=MatchAny(any=groups))]
        if doc_id is not None:
            condiciones.append(_es("doc_id", doc_id))
        resultado = self._client.query_points(
            self._collection,
            query=vector,
            query_filter=Filter(must=condiciones),
            limit=top_k,
            with_payload=True,
        )
        return [
            ChunkRecuperado(chunk=Chunk.model_validate(p.payload), score=p.score)
            for p in resultado.points
        ]

    def get_document_chunks(self, doc_id: str, groups: list[str]) -> list[Chunk]:
        if not groups:
            return []
        filtro = Filter(
            must=[
                _es("doc_id", doc_id),
                FieldCondition(key="acl_groups", match=MatchAny(any=groups)),
            ]
        )
        chunks = [Chunk.model_validate(p.payload) for p in self._scroll(filtro)]
        return sorted(chunks, key=lambda c: indice_chunk(c.chunk_id))

    # ------------------------------------------------------------ gestión por documento
    def delete_document(self, doc_id: str) -> int:
        if not self._client.collection_exists(self._collection):
            return 0
        filtro = Filter(must=[_es("doc_id", doc_id)])
        total = self._client.count(self._collection, count_filter=filtro, exact=True).count
        if total:
            self._client.delete(self._collection, points_selector=FilterSelector(filter=filtro))
        return total

    def document_hash(self, doc_id: str) -> str | None:
        for punto in self._scroll(Filter(must=[_es("doc_id", doc_id)]), limite=1):
            return (punto.payload or {}).get("doc_hash") or None
        return None

    def find_by_hash(self, doc_hash: str, group: str) -> list[str]:
        filtro = Filter(
            must=[
                _es("doc_hash", doc_hash),
                FieldCondition(key="acl_groups", match=MatchAny(any=[group])),
            ]
        )
        return sorted({(p.payload or {})["doc_id"] for p in self._scroll(filtro)})

    def list_documents(self, groups: list[str]) -> list[DocumentoIndexado]:
        if not groups:
            return []
        filtro = Filter(must=[FieldCondition(key="acl_groups", match=MatchAny(any=groups))])
        docs: dict[str, DocumentoIndexado] = {}
        for punto in self._scroll(filtro):
            chunk = Chunk.model_validate(punto.payload)
            if actual := docs.get(chunk.doc_id):
                actual.chunks += 1
            else:
                docs[chunk.doc_id] = DocumentoIndexado(
                    doc_id=chunk.doc_id,
                    acl_groups=chunk.acl_groups,
                    doc_hash=chunk.doc_hash,
                    chunks=1,
                    indexado_en=chunk.indexado_en,
                )
        return sorted(docs.values(), key=lambda d: d.doc_id)

    def _scroll(self, filtro: Filter, limite: int | None = None) -> Iterator[Record]:
        if not self._client.collection_exists(self._collection):
            return
        offset = None
        devueltos = 0
        while True:
            puntos, offset = self._client.scroll(
                self._collection,
                scroll_filter=filtro,
                limit=min(256, limite or 256),
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for punto in puntos:
                yield punto
                devueltos += 1
                if limite and devueltos >= limite:
                    return
            if offset is None:
                return
