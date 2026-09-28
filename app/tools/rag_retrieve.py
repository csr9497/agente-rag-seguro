from pydantic import BaseModel, ConfigDict, Field

from app.models.schemas import ChunkRecuperado, Usuario
from app.retrieval.base import Embedder, Retriever


class RagRetrieveArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consulta: str = Field(
        min_length=1,
        max_length=500,
        description="Consulta de búsqueda autocontenida sobre los documentos de la empresa",
    )


class RagRetrieve:
    nombre = "rag_retrieve"
    descripcion = (
        "Busca fragmentos relevantes en los documentos internos de la empresa a los que el "
        "usuario tiene acceso. Úsala una vez por cada tema distinto de la pregunta."
    )
    args_model = RagRetrieveArgs

    def __init__(
        self, embedder: Embedder, retriever: Retriever, min_score: float | None = None
    ) -> None:
        self._embedder = embedder
        self._retriever = retriever
        self._min_score = min_score

    def ejecutar(
        self, args: RagRetrieveArgs, usuario: Usuario, top_k: int
    ) -> list[ChunkRecuperado]:
        [vector] = self._embedder.embed([args.consulta])
        recuperados = self._retriever.search(
            args.consulta, vector, groups=usuario.groups, top_k=top_k
        )
        if self._min_score is not None:
            recuperados = [r for r in recuperados if r.score >= self._min_score]
        return recuperados
