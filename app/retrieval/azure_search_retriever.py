import re

from azure.core.credentials import TokenCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizedQuery

from app.models.schemas import Chunk, ChunkRecuperado

# IDs de grupo permitidos en el filtro OData (nombres simples o GUIDs de Entra ID).
_GRUPO_VALIDO = re.compile(r"^[A-Za-z0-9_.\-]+$")


def build_acl_filter(groups: list[str]) -> str:
    """Filtro OData de security trimming. Rechaza valores que puedan alterar el filtro."""
    if not groups:
        raise ValueError("Se requiere al menos un grupo")
    for g in groups:
        if not _GRUPO_VALIDO.match(g):
            raise ValueError(f"Identificador de grupo no válido: {g!r}")
    return f"acl_groups/any(g: search.in(g, '{','.join(groups)}', ','))"


def _clave_documento(chunk_id: str) -> str:
    # Las claves de AI Search solo admiten letras, dígitos, '_', '-' y '='.
    return re.sub(r"[^A-Za-z0-9_\-=]", "_", chunk_id)


class AzureSearchRetriever:
    """Búsqueda híbrida (texto + vector) con filtro por grupos del usuario."""

    def __init__(
        self, endpoint: str, index: str, credential: TokenCredential, dimensions: int
    ) -> None:
        self._index = index
        self._dimensions = dimensions
        self._search = SearchClient(endpoint, index, credential)
        self._indexes = SearchIndexClient(endpoint, credential)

    def ensure_index(self) -> None:
        try:
            self._indexes.get_index(self._index)
            return
        except ResourceNotFoundError:
            pass
        campos = [
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SimpleField(name="chunk_id", type=SearchFieldDataType.String),
            SimpleField(name="doc_id", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="fuente", type=SearchFieldDataType.String),
            SearchableField(name="contenido", type=SearchFieldDataType.String),
            SimpleField(
                name="acl_groups",
                type=SearchFieldDataType.Collection(SearchFieldDataType.String),
                filterable=True,
            ),
            SearchField(
                name="vector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=self._dimensions,
                vector_search_profile_name="hnsw",
            ),
        ]
        self._indexes.create_index(
            SearchIndex(
                name=self._index,
                fields=campos,
                vector_search=VectorSearch(
                    algorithms=[HnswAlgorithmConfiguration(name="hnsw-algo")],
                    profiles=[
                        VectorSearchProfile(name="hnsw", algorithm_configuration_name="hnsw-algo")
                    ],
                ),
            )
        )

    def upsert(self, chunks: list[Chunk], vectores: list[list[float]]) -> None:
        documentos = [
            {"id": _clave_documento(c.chunk_id), **c.model_dump(), "vector": v}
            for c, v in zip(chunks, vectores, strict=True)
        ]
        self._search.merge_or_upload_documents(documentos)

    def search(
        self, consulta: str, vector: list[float], groups: list[str], top_k: int
    ) -> list[ChunkRecuperado]:
        if not groups:
            return []
        resultados = self._search.search(
            search_text=consulta,
            vector_queries=[
                VectorizedQuery(vector=vector, k_nearest_neighbors=top_k, fields="vector")
            ],
            filter=build_acl_filter(groups),
            top=top_k,
            select=["chunk_id", "doc_id", "fuente", "contenido", "acl_groups"],
        )
        return [
            ChunkRecuperado(
                chunk=Chunk.model_validate({k: r[k] for k in Chunk.model_fields}),
                score=r["@search.score"],
            )
            for r in resultados
        ]
