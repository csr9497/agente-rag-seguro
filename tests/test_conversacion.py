"""Conversación básica (saludos, agradecimientos, ayuda): respuesta por plantilla, sin buscar
ni llamar al LLM de generación, y sin inventar contenido (regla 1)."""

import json

import pytest

from app.models.schemas import Usuario
from app.tools.conversacion import PLANTILLAS, ResponderConversacion
from tests.fakes import FakeLLM, FakeSupervisor

PUBLIC = Usuario(id="u1", groups=["public"])


def _supervisor(tipo: str) -> FakeSupervisor:
    return FakeSupervisor([[("conversacion", json.dumps({"tipo": tipo}))]])


@pytest.mark.parametrize("tipo", sorted(PLANTILLAS))
def test_plantilla_sin_llm_ni_citas(crear_agente, tipo) -> None:
    llm = FakeLLM()
    r = crear_agente(
        supervisor=_supervisor(tipo), llm=llm, herramientas=[ResponderConversacion()]
    ).consultar("hola", PUBLIC)
    assert r.respuesta == PLANTILLAS[tipo] and r.conversacional
    assert r.citas == [] and llm.llamadas == []


def test_tipo_no_valido_no_produce_plantilla(crear_agente) -> None:
    r = crear_agente(
        supervisor=_supervisor("chiste"), herramientas=[ResponderConversacion()]
    ).consultar("cuéntame un chiste", PUBLIC)
    assert not r.conversacional


def test_si_ademas_hay_contexto_manda_el_contexto(
    crear_agente, embedder, retriever_con_docs
) -> None:
    """«Hola, ¿cuántos días de vacaciones tengo?»: saludo + búsqueda → respuesta citada."""
    from app.tools.rag_retrieve import RagRetrieve

    sup = FakeSupervisor(
        [[("conversacion", '{"tipo": "saludo"}'), ("rag_retrieve", '{"consulta": "vacaciones"}')]]
    )
    r = crear_agente(
        supervisor=sup,
        herramientas=[ResponderConversacion(), RagRetrieve(embedder, retriever_con_docs, None)],
    ).consultar("Hola, ¿vacaciones?", PUBLIC)
    assert not r.conversacional and r.citas


def test_el_supervisor_conoce_la_herramienta() -> None:
    from app.graph.prompts import SUPERVISOR_PROMPT

    assert "conversacion" in SUPERVISOR_PROMPT


def test_tras_una_herramienta_terminal_no_se_vuelve_al_supervisor(crear_agente) -> None:
    """Latencia: saludo o aclaración no necesitan un segundo turno del supervisor («LISTO»)."""
    sup = _supervisor("saludo")
    crear_agente(supervisor=sup, herramientas=[ResponderConversacion()]).consultar("hola", PUBLIC)
    assert len(sup.llamadas) == 1


def test_tras_una_busqueda_si_se_vuelve_al_supervisor(crear_agente, embedder, retriever_con_docs):
    from app.tools.rag_retrieve import RagRetrieve

    sup = FakeSupervisor([[("rag_retrieve", '{"consulta": "vacaciones"}')]])
    crear_agente(
        supervisor=sup, herramientas=[RagRetrieve(embedder, retriever_con_docs, None)]
    ).consultar("vacaciones", PUBLIC)
    assert len(sup.llamadas) == 2  # puede pedir más contexto antes de responder
