"""Conversación básica (saludos, agradecimientos, ayuda): respuesta por plantilla, sin buscar
ni llamar al LLM de generación, y sin inventar contenido (regla 1)."""

import json

import pytest

from app.modelos.errores import ModeloError
from app.models.schemas import Usuario
from app.rag.catalogo import CatalogoRol
from app.tools.conversacion import PLANTILLAS, ResponderConversacion
from tests.fakes import FakeLLM, FakeSupervisor

PUBLIC = Usuario(id="u1", groups=["public"])


def _supervisor(tipo: str) -> FakeSupervisor:
    return FakeSupervisor([[("conversacion", json.dumps({"tipo": tipo}))]])


@pytest.mark.parametrize("tipo", ["saludo", "agradecimiento", "despedida"])
def test_plantilla_sin_llm_ni_citas(crear_agente, tipo) -> None:
    llm = FakeLLM()
    r = crear_agente(
        supervisor=_supervisor(tipo), llm=llm, herramientas=[ResponderConversacion()]
    ).consultar("hola", PUBLIC)
    assert r.respuesta == PLANTILLAS[tipo] and r.conversacional
    assert r.citas == [] and llm.llamadas == []


@pytest.mark.parametrize("tipo", ["ayuda", "fuera_de_ambito"])
def test_ayuda_y_fuera_de_ambito_orientan_con_el_catalogo(crear_agente, tipo) -> None:
    """No responde con conocimiento general: el LLM explica qué SÍ puede consultar el rol."""
    llm = FakeLLM()
    catalogo = CatalogoRol(documentos=["Política de vacaciones"])
    r = crear_agente(
        supervisor=_supervisor(tipo), llm=llm, herramientas=[ResponderConversacion()],
        catalogo=lambda grupos: catalogo,
    ).consultar("dame una receta de pasta", PUBLIC)  # fmt: skip
    assert r.conversacional and r.sin_contexto and r.citas == []
    assert "Política de vacaciones" in r.respuesta
    assert len(llm.llamadas) == 1 and f"<motivo>\n{tipo}\n</motivo>" in llm.llamadas[0][1]


def test_si_la_orientacion_falla_queda_la_plantilla(crear_agente) -> None:
    class LLMQueFalla:
        def responder(self, system, user):  # noqa: ANN001, ANN202
            raise ModeloError("servicio_no_disponible", "openai", "gpt-4o", "caído")

    r = crear_agente(
        supervisor=_supervisor("fuera_de_ambito"), llm=LLMQueFalla(),
        herramientas=[ResponderConversacion()],
    ).consultar("receta de pasta", PUBLIC)  # fmt: skip
    assert r.respuesta == PLANTILLAS["fuera_de_ambito"] and r.conversacional


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
