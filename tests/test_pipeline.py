from app.models.schemas import RespuestaLLM, Usuario
from app.rag.pipeline import RAGPipeline
from app.rag.prompts import SIN_CONTEXTO
from tests.fakes import FakeLLM

PUBLIC = Usuario(id="u1", groups=["public"])


def _pipeline(retriever, embedder, llm, **kw) -> RAGPipeline:
    return RAGPipeline(embedder, retriever, llm, **kw)


def test_respuesta_con_citas(retriever_con_docs, embedder, llm) -> None:
    r = _pipeline(retriever_con_docs, embedder, llm).consultar("días de vacaciones", PUBLIC)
    assert not r.sin_contexto
    assert [c.numero for c in r.citas] == [1]
    assert r.citas[0].doc_id.startswith("public/")


def test_prompt_numera_contexto_y_lleva_reglas(retriever_con_docs, embedder, llm) -> None:
    _pipeline(retriever_con_docs, embedder, llm).consultar("vacaciones", PUBLIC)
    system, user = llm.llamadas[0]
    assert "Cita cada afirmación" in system
    assert "[1] (fuente: public/" in user
    assert "salarial" not in user.lower(), "contexto de rrhh filtrado a un usuario public"


def test_sin_resultados_no_llama_al_llm(retriever_con_docs, embedder, llm) -> None:
    sin_grupos = Usuario(id="u2", groups=[])
    r = _pipeline(retriever_con_docs, embedder, llm).consultar("vacaciones", sin_grupos)
    assert r.sin_contexto and r.respuesta == SIN_CONTEXTO and r.citas == []
    assert llm.llamadas == []


def test_llm_indica_no_encontrado(retriever_con_docs, embedder) -> None:
    llm = FakeLLM(RespuestaLLM(respuesta="No lo sé", citas_usadas=[], encontrado=False))
    r = _pipeline(retriever_con_docs, embedder, llm).consultar("capital de Francia", PUBLIC)
    assert r.sin_contexto and r.citas == []


def test_respuesta_sin_citas_validas_se_trata_como_sin_contexto(
    retriever_con_docs, embedder
) -> None:
    llm = FakeLLM(RespuestaLLM(respuesta="Inventado [99]", citas_usadas=[99], encontrado=True))
    r = _pipeline(retriever_con_docs, embedder, llm).consultar("vacaciones", PUBLIC)
    assert r.sin_contexto and r.respuesta == SIN_CONTEXTO


def test_min_score_filtra_resultados(retriever_con_docs, embedder, llm) -> None:
    r = _pipeline(retriever_con_docs, embedder, llm, min_score=1.01).consultar("vacaciones", PUBLIC)
    assert r.sin_contexto and llm.llamadas == []
