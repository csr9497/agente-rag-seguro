from datetime import date

from app.models.schemas import ChunkRecuperado, Cita, RespuestaConsulta, Turno
from app.rag.prompts import SIN_CONTEXTO, SYSTEM_PROMPT, build_user_prompt
from app.retrieval.base import LLM


def respuesta_sin_contexto() -> RespuestaConsulta:
    return RespuestaConsulta(respuesta=SIN_CONTEXTO, citas=[], sin_contexto=True)


def generar_respuesta(
    llm: LLM,
    pregunta: str,
    recuperados: list[ChunkRecuperado],
    historial: list[Turno] | None = None,
    system_prompt: str = SYSTEM_PROMPT,
) -> RespuestaConsulta:
    """Respuesta fundamentada con citas. Sin contexto visible, no se llama al LLM."""
    if not recuperados:
        return respuesta_sin_contexto()
    # Con la fecha, «este año» o «el próximo festivo» se resuelven contra los datos.
    system = f"{system_prompt}\nFecha de hoy: {date.today()}."
    salida = llm.responder(system, build_user_prompt(pregunta, recuperados, historial))
    citas = _mapear_citas(salida.citas_usadas, recuperados)
    if not salida.encontrado or not citas:
        # Sin citas válidas no damos la respuesta por fundamentada.
        return respuesta_sin_contexto()
    return RespuestaConsulta(
        respuesta=_con_marcas(salida.respuesta, citas), citas=citas, sin_contexto=False
    )


def _con_marcas(texto: str, citas: list[Cita]) -> str:
    """Regla 6 (citación en el texto): si el modelo dejó las marcas solo en citas_usadas
    (pasa con salida estructurada), se añaden al final."""
    if any(f"[{c.numero}]" in texto for c in citas):
        return texto
    return f"{texto.rstrip()} " + "".join(f"[{c.numero}]" for c in citas)


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
                # Completo (un chunk son ~1000 caracteres): es lo que vio el modelo y lo que
                # necesita el juez de groundedness; la UI lo recorta a dos líneas.
                fragmento=r.chunk.contenido,
                score=r.score,
            )
        )
    return citas
