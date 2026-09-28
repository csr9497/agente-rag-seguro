import re

from azure.core.credentials import AzureKeyCredential, TokenCredential
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

from app.models.schemas import Chunk, ChunkRecuperado, DocumentoIndexado, indice_chunk

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


def _literal(valor: str) -> str:
    """Literal de cadena OData: las comillas simples se duplican."""
    return "'" + valor.replace("'", "''") + "'"


def _clave_documento(chunk_id: str) -> str:
    # Las claves de AI Search solo admiten letras, dígitos, '_', '-' y '='.
    return re.sub(r"[^A-Za-z0-9_\-=]", "_", chunk_id)


class AzureSearchRetriever:
    """Búsqueda híbrida (texto + vector) con filtro por grupos del usuario."""

    def __init__(
        self,
        endpoint: str,
        index: str,
        credential: TokenCredential | AzureKeyCredential,
        dimensions: int,
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
            SimpleField(name="doc_hash", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="indexado_en", type=SearchFieldDataType.String),
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
        self,
        consulta: str,
        vector: list[float],
        groups: list[str],
        top_k: int,
        doc_id: str | None = None,
    ) -> list[ChunkRecuperado]:
        if not groups:
            return []
        filtro = build_acl_filter(groups)
        if doc_id is not None:
            filtro += f" and doc_id eq {_literal(doc_id)}"
        try:
            return self._buscar_vectorial(consulta, vector, filtro, top_k)
        except ResourceNotFoundError:
            return []  # índice aún sin crear (despliegue nuevo): no hay nada visible

    def _buscar_vectorial(
        self, consulta: str, vector: list[float], filtro: str, top_k: int
    ) -> list[ChunkRecuperado]:
        resultados = self._search.search(
            search_text=consulta,
            vector_queries=[
                VectorizedQuery(vector=vector, k_nearest_neighbors=top_k, fields="vector")
            ],
            filter=filtro,
            top=top_k,
            select=list(Chunk.model_fields),
        )
        return [
            ChunkRecuperado(
                chunk=Chunk.model_validate({k: r[k] for k in Chunk.model_fields}),
                score=r["@search.score"],
            )
            for r in resultados
        ]

    def get_document_chunks(self, doc_id: str, groups: list[str]) -> list[Chunk]:
        if not groups:
            return []
        filtro = f"doc_id eq {_literal(doc_id)} and {build_acl_filter(groups)}"
        chunks = [
            Chunk.model_validate({k: r[k] for k in Chunk.model_fields})
            for r in self._buscar(filtro, list(Chunk.model_fields))
        ]
        return sorted(chunks, key=lambda c: indice_chunk(c.chunk_id))

    # ------------------------------------------------------------ gestión por documento
    def _buscar(self, filtro: str, campos: list[str], top: int | None = None):
        return self._search.search(search_text="*", filter=filtro, select=campos, top=top)

    def delete_document(self, doc_id: str) -> int:
        ids = [{"id": r["id"]} for r in self._buscar(f"doc_id eq {_literal(doc_id)}", ["id"])]
        for i in range(0, len(ids), 1000):
            self._search.delete_documents(ids[i : i + 1000])
        return len(ids)

    def document_hash(self, doc_id: str) -> str | None:
        for r in self._buscar(f"doc_id eq {_literal(doc_id)}", ["doc_hash"], top=1):
            return r["doc_hash"] or None
        return None

    def find_by_hash(self, doc_hash: str, group: str) -> list[str]:
        filtro = f"doc_hash eq {_literal(doc_hash)} and {build_acl_filter([group])}"
        return sorted({r["doc_id"] for r in self._buscar(filtro, ["doc_id"])})

    def list_documents(self, groups: list[str]) -> list[DocumentoIndexado]:
        if not groups:
            return []
        campos = ["doc_id", "acl_groups", "doc_hash", "indexado_en"]
        docs: dict[str, DocumentoIndexado] = {}
        for r in self._buscar(build_acl_filter(groups), campos):
            if actual := docs.get(r["doc_id"]):
                actual.chunks += 1
            else:
                docs[r["doc_id"]] = DocumentoIndexado(chunks=1, **{c: r[c] for c in campos})
        return sorted(docs.values(), key=lambda d: d.doc_id)
