"""El supervisor interpreta el mensaje antes de buscar: consulta curada para el RAG o, si no
es precisa, una pregunta de aclaración al usuario (sin buscar ni generar)."""

import json

import pytest
from pydantic import ValidationError

from app.models.schemas import Usuario
from app.tools.aclaracion import AclaracionArgs, PedirAclaracion
from app.tools.rag_retrieve import RagRetrieve
from tests.fakes import FakeLLM, FakeSupervisor

PUBLIC = Usuario(id="u1", groups=["public"])
ACLARAR = json.dumps(
    {
        "pregunta": "¿Sobre qué quieres saber: vacaciones o teletrabajo?",
        "opciones": ["Días de vacaciones al año", "Días de teletrabajo por semana"],
    }
)


def _agente(crear_agente, embedder, retriever, turnos, llm=None):
    return crear_agente(
        supervisor=FakeSupervisor(turnos),
        llm=llm or FakeLLM(),
        herramientas=[PedirAclaracion(), RagRetrieve(embedder, retriever, None)],
    )


def test_consulta_imprecisa_rebota_con_pregunta_y_opciones(
    crear_agente, embedder, retriever_con_docs
) -> None:
    llm = FakeLLM()
    r = _agente(
        crear_agente, embedder, retriever_con_docs, [[("pedir_aclaracion", ACLARAR)]], llm
    ).consultar_detallado("¿cuántos días tengo?", PUBLIC)
    assert r.respuesta.aclaracion is not None
    assert r.respuesta.respuesta == "¿Sobre qué quieres saber: vacaciones o teletrabajo?"
    assert r.respuesta.aclaracion.opciones == [
        "Días de vacaciones al año",
        "Días de teletrabajo por semana",
    ]
    assert r.respuesta.citas == [] and llm.llamadas == [] and r.documentos_consultados == []


def test_la_consulta_curada_se_registra(crear_agente, embedder, retriever_con_docs) -> None:
    curada = "política de vacaciones: días laborables al año"
    r = _agente(
        crear_agente, embedder, retriever_con_docs,
        [[("rag_retrieve", json.dumps({"consulta": curada}))]],
    ).consultar_detallado("oye, ¿y las vacas cuántas son?", PUBLIC)  # fmt: skip
    assert r.consultas == [curada] and r.respuesta.citas


def test_con_documentos_encontrados_manda_la_respuesta(
    crear_agente, embedder, retriever_con_docs
) -> None:
    r = _agente(
        crear_agente, embedder, retriever_con_docs,
        [[("pedir_aclaracion", ACLARAR), ("rag_retrieve", '{"consulta": "vacaciones"}')]],
    ).consultar_detallado("vacaciones", PUBLIC)  # fmt: skip
    assert r.respuesta.aclaracion is None and r.respuesta.citas


@pytest.mark.parametrize(
    "args",
    [
        {"pregunta": "Dime de qué tema"},  # no es una pregunta
        {"pregunta": "¿" + "x" * 300 + "?"},  # demasiado larga
        {"pregunta": "¿Cuál?", "opciones": ["a", "b", "c", "d", "e"]},  # demasiadas opciones
    ],
)
def test_aclaracion_acotada(args) -> None:
    with pytest.raises(ValidationError):
        AclaracionArgs.model_validate(args)


def test_el_supervisor_interpreta_antes_de_buscar() -> None:
    from app.graph.prompts import SUPERVISOR_PROMPT

    assert "pedir_aclaracion" in SUPERVISOR_PROMPT and "curada" in SUPERVISOR_PROMPT


def test_aclaracion_y_consultas_llegan_a_la_api(tmp_path, retriever) -> None:
    from fastapi.testclient import TestClient

    from app.config import Settings, get_settings
    from app.deps import build_servicios
    from app.main import app
    from tests.fakes import FakeEmbedder

    settings = Settings(
        database_url="sqlite://", seleccion_libre_de_rol=True, almacen_local_dir=str(tmp_path)
    )
    s = build_servicios(
        settings,
        modelos=(FakeEmbedder(), FakeLLM(), FakeSupervisor([[("pedir_aclaracion", ACLARAR)]])),
        retriever=retriever,
    )
    app.state.servicios, app.state.agente = s, s.agente
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        c = TestClient(app)
        conv = c.post("/conversaciones", json={"rol_id": "public"}).json()
        m = c.post(f"/conversaciones/{conv['id']}/mensajes", json={"pregunta": "días"}).json()
        assert m["aclaracion"]["opciones"][0] == "Días de vacaciones al año"
        guardado = c.get(f"/conversaciones/{conv['id']}").json()["mensajes"][0]
        assert guardado["aclaracion"] == m["aclaracion"] and guardado["consultas"] == []
    finally:
        app.dependency_overrides.clear()
