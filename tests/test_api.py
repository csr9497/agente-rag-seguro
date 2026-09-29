import logging

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.deps import build_agente
from app.main import app, get_agente


@pytest.fixture
def client(agente):
    app.dependency_overrides[get_agente] = lambda: agente
    yield TestClient(app)  # sin `with`: no se ejecuta el lifespan (no toca Azure)
    app.dependency_overrides.clear()


def test_health(client) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_consultar_devuelve_respuesta_citada(client) -> None:
    r = client.post("/consultar", json={"pregunta": "¿Cuántos días de vacaciones tengo?"})
    assert r.status_code == 200
    body = r.json()
    assert body["sin_contexto"] is False
    assert body["citas"][0]["fuente"].startswith("public/")


@pytest.mark.parametrize(
    "payload",
    [{}, {"pregunta": ""}, {"pregunta": "x" * 2001}, {"pregunta": "a", "top_k": 0}, {"q": "a"}],
)
def test_validacion_de_entrada(client, payload) -> None:
    assert client.post("/consultar", json=payload).status_code == 422


def test_consulta_queda_auditada(client, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="audit"):
        client.post("/consultar", json={"pregunta": "vacaciones"})
    registro = next(r for r in caplog.records if r.name == "audit")
    assert '"pregunta":"vacaciones"' in registro.getMessage()
    assert "public/" in registro.getMessage()


def test_cabecera_de_grupos_se_ignora_sin_identidad_debug(client) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(identidad_debug=False)
    r = client.post(
        "/consultar", json={"pregunta": "bandas salariales"}, headers={"X-Usuario-Grupos": "rrhh"}
    )
    assert all(c["fuente"].startswith("public/") for c in r.json()["citas"])


def test_cabecera_de_grupos_con_identidad_debug(client) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(identidad_debug=True)
    r = client.post(
        "/consultar", json={"pregunta": "bandas salariales"}, headers={"X-Usuario-Grupos": "rrhh"}
    )
    assert r.json()["citas"][0]["fuente"] == "rrhh/bandas-salariales.md"


def test_sin_azure_openai_responde_503_indicando_que_falta(client, tmp_path) -> None:
    settings = Settings(azure_openai_endpoint="", qdrant_path=str(tmp_path / "qdrant"))
    app.dependency_overrides[get_agente] = lambda: build_agente(settings)
    r = client.post("/consultar", json={"pregunta": "vacaciones"})
    assert r.status_code == 503
    assert "AZURE_OPENAI_ENDPOINT" in r.json()["detail"]


def test_topologia_oculta_por_defecto(client) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(exponer_topologia=False)
    assert client.get("/grafo").status_code == 404
    assert client.get("/grafo.mmd").status_code == 404


def test_topologia_muestra_el_grafo(client) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(exponer_topologia=True)
    mmd = client.get("/grafo.mmd").text
    for arista in [
        "__start__ --> authorize",
        "authorize -.-> input_guardrail",
        "authorize -.-> audit",
        "supervisor -.-> tools",
        "tools --> access_guardrail",
        "access_guardrail -.-> supervisor",
        "access_guardrail -.-> generate",
        "supervisor -.-> generate",
        "input_guardrail -.-> cache_lookup",
        "cache_lookup -.-> supervisor",
        "cache_lookup -.-> output_guardrail",
        "output_guardrail --> cache_store",
        "cache_store --> audit",
    ]:
        assert arista in mmd
    assert 'class="mermaid"' in client.get("/grafo").text
