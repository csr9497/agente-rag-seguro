from app.models.schemas import ChunkRecuperado, Cita, RespuestaConsulta, Usuario
from app.rag.prompts import SIN_CONTEXTO, SYSTEM_PROMPT, build_user_prompt
from app.retrieval.base import LLM, Embedder, Retriever


class RAGPipeline:
    def __init__(
        self,
        embedder: Embedder,
        retriever: Retriever,
        llm: LLM,
        top_k: int = 4,
        min_score: float | None = None,
    ) -> None:
        self._embedder = embedder
        self._retriever = retriever
        self._llm = llm
        self._top_k = top_k
        self._min_score = min_score

    def consultar(
        self, pregunta: str, usuario: Usuario, top_k: int | None = None
    ) -> RespuestaConsulta:
        [vector] = self._embedder.embed([pregunta])
        recuperados = self._retriever.search(
            pregunta, vector, groups=usuario.groups, top_k=top_k or self._top_k
        )
        if self._min_score is not None:
            recuperados = [r for r in recuperados if r.score >= self._min_score]
        if not recuperados:
            # Salida de escape sin llamar al LLM: nada visible para este usuario.
            return RespuestaConsulta(respuesta=SIN_CONTEXTO, citas=[], sin_contexto=True)

        salida = self._llm.responder(SYSTEM_PROMPT, build_user_prompt(pregunta, recuperados))
        citas = _mapear_citas(salida.citas_usadas, recuperados)
        if not salida.encontrado or not citas:
            # Sin citas válidas no damos la respuesta por fundamentada.
            return RespuestaConsulta(respuesta=SIN_CONTEXTO, citas=[], sin_contexto=True)
        return RespuestaConsulta(respuesta=salida.respuesta, citas=citas, sin_contexto=False)


def _mapear_citas(numeros: list[int], recuperados: list[ChunkRecuperado]) -> list[Cita]:
    citas: list[Cita] = []
    for n in sorted(set(numeros)):
        if not 1 <= n <= len(recuperados):
            continue  # el LLM citó un fragmento inexistente: se descarta
        r = recuperados[n - 1]
        citas.append(
            Cita(
                numero=n,
                doc_id=r.chunk.doc_id,
                chunk_id=r.chunk.chunk_id,
                fuente=r.chunk.fuente,
                fragmento=r.chunk.contenido[:300],
                score=r.score,
            )
        )
    return citas
