from app.models.schemas import ChunkRecuperado, Cita, RespuestaConsulta
from app.rag.prompts import SIN_CONTEXTO, SYSTEM_PROMPT, build_user_prompt
from app.retrieval.base import LLM


def respuesta_sin_contexto() -> RespuestaConsulta:
    return RespuestaConsulta(respuesta=SIN_CONTEXTO, citas=[], sin_contexto=True)


def generar_respuesta(
    llm: LLM, pregunta: str, recuperados: list[ChunkRecuperado]
) -> RespuestaConsulta:
    """Respuesta fundamentada con citas. Sin contexto visible, no se llama al LLM."""
    if not recuperados:
        return respuesta_sin_contexto()
    salida = llm.responder(SYSTEM_PROMPT, build_user_prompt(pregunta, recuperados))
    citas = _mapear_citas(salida.citas_usadas, recuperados)
    if not salida.encontrado or not citas:
        # Sin citas válidas no damos la respuesta por fundamentada.
        return respuesta_sin_contexto()
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
