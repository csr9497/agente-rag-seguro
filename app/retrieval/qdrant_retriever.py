import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from app.models.schemas import Chunk, ChunkRecuperado


class QdrantRetriever:
    def __init__(self, client: QdrantClient, collection: str, dimensions: int) -> None:
        self._client = client
        self._collection = collection
        self._dimensions = dimensions

    def ensure_index(self) -> None:
        if self._client.collection_exists(self._collection):
            return
        self._client.create_collection(
            self._collection,
            vectors_config=VectorParams(size=self._dimensions, distance=Distance.COSINE),
        )
        self._client.create_payload_index(
            self._collection, field_name="acl_groups", field_schema=PayloadSchemaType.KEYWORD
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
        self, consulta: str, vector: list[float], groups: list[str], top_k: int
    ) -> list[ChunkRecuperado]:
        if not groups:
            return []
        resultado = self._client.query_points(
            self._collection,
            query=vector,
            query_filter=Filter(
                must=[FieldCondition(key="acl_groups", match=MatchAny(any=groups))]
            ),
            limit=top_k,
            with_payload=True,
        )
        return [
            ChunkRecuperado(chunk=Chunk.model_validate(p.payload), score=p.score)
            for p in resultado.points
        ]
