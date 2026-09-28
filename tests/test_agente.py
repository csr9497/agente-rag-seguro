"""Grafo LangGraph: flujo, permisos, citación, guardrails y auditoría."""

import json
import logging

import pytest

from app.models.schemas import RespuestaLLM, Usuario
from app.rag.prompts import SIN_CONTEXTO
from app.security.guardrails import MENSAJE_BLOQUEO
from tests.fakes import FakeLLM, FakeSupervisor, GuardrailQueBloquea

PUBLIC = Usuario(id="u1", groups=["public"])


def _rag(consulta: str) -> tuple[str, str]:
    return ("rag_retrieve", json.dumps({"consulta": consulta}))


def test_grafo_tiene_los_nodos_del_diseno(agente) -> None:
    nodos = set(agente.grafo.get_graph().nodes)
    assert {
        "authorize", "input_guardrail", "supervisor", "tools",
        "generate", "output_guardrail", "audit",
    } <= nodos  # fmt: skip


def test_respuesta_con_citas(agente, supervisor, llm) -> None:
    r = agente.consultar("días de vacaciones", PUBLIC)
    assert not r.sin_contexto
    assert [c.numero for c in r.citas] == [1]
    assert r.citas[0].doc_id.startswith("public/")
    assert len(supervisor.llamadas) == 2  # busca, y después decide terminar
    assert len(llm.llamadas) == 1


def test_supervisor_recibe_resultados_de_la_herramienta(agente, supervisor) -> None:
    agente.consultar("vacaciones", PUBLIC)
    segundo_turno = supervisor.llamadas[1]
    tool_msg = next(m for m in segundo_turno if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "call_0_0"
    assert "public/politica-vacaciones.md" in tool_msg["content"]


def test_contexto_de_rrhh_nunca_llega_a_un_usuario_public(agente, supervisor, llm) -> None:
    agente.consultar("bandas salariales banda senior", PUBLIC)
    _, user = llm.llamadas[0]
    contexto = user.split("PREGUNTA:")[0]
    assert "rrhh/" not in contexto and "58.000" not in contexto
    assert all("rrhh/" not in str(m.get("content")) for m in supervisor.llamadas[-1])


def test_varias_busquedas_acumulan_contexto_sin_duplicados(crear_agente, llm) -> None:
    sup = FakeSupervisor([[_rag("vacaciones"), _rag("teletrabajo")], [_rag("vacaciones")]])
    agente = crear_agente(supervisor=sup)
    agente.consultar("vacaciones y teletrabajo", PUBLIC)
    _, user = llm.llamadas[0]
    fuentes = [line for line in user.splitlines() if line.startswith("[")]
    assert len(fuentes) == len(set(fuentes))
    assert any("teletrabajo" in f for f in fuentes) and any("vacaciones" in f for f in fuentes)
    assert "Sin resultados nuevos" in str(sup.llamadas[2])


def test_max_iteraciones_corta_el_bucle(crear_agente) -> None:
    sup = FakeSupervisor([[_rag(f"vacaciones {i}")] for i in range(10)])
    crear_agente(supervisor=sup, max_iteraciones=2).consultar("vacaciones", PUBLIC)
    assert len(sup.llamadas) == 2


def test_herramienta_inexistente_devuelve_error_al_supervisor(crear_agente, llm) -> None:
    sup = FakeSupervisor([[("borrar_todo", "{}")]])
    r = crear_agente(supervisor=sup).consultar("vacaciones", PUBLIC)
    assert "no existe" in str(sup.llamadas[1])
    assert r.sin_contexto and llm.llamadas == []


def test_el_llm_no_puede_elegir_grupos(crear_agente, llm) -> None:
    """Regla 1: los grupos vienen del estado autenticado, no de los argumentos del LLM."""
    ataque = json.dumps({"consulta": "bandas salariales", "groups": ["rrhh"]})
    sup = FakeSupervisor([[("rag_retrieve", ataque)]])
    r = crear_agente(supervisor=sup).consultar("bandas salariales", PUBLIC)
    assert "argumentos no válidos" in str(sup.llamadas[1])
    assert r.sin_contexto and llm.llamadas == []


def test_usuario_sin_grupos_no_llama_a_ningun_modelo(agente, supervisor, llm) -> None:
    r = agente.consultar("vacaciones", Usuario(id="u2", groups=[]))
    assert r.sin_contexto and r.respuesta == SIN_CONTEXTO
    assert supervisor.llamadas == [] and llm.llamadas == []


def test_llm_indica_no_encontrado(crear_agente) -> None:
    llm = FakeLLM(RespuestaLLM(respuesta="No lo sé", citas_usadas=[], encontrado=False))
    r = crear_agente(llm=llm).consultar("capital de Francia", PUBLIC)
    assert r.sin_contexto and r.citas == []


def test_citas_invalidas_se_tratan_como_sin_contexto(crear_agente) -> None:
    llm = FakeLLM(RespuestaLLM(respuesta="Inventado [99]", citas_usadas=[99], encontrado=True))
    r = crear_agente(llm=llm).consultar("vacaciones", PUBLIC)
    assert r.sin_contexto and r.respuesta == SIN_CONTEXTO


def test_min_score_filtra_resultados(crear_agente, llm) -> None:
    r = crear_agente(min_score=1.01).consultar("vacaciones", PUBLIC)
    assert r.sin_contexto and llm.llamadas == []


def test_guardrail_de_entrada_bloquea_antes_del_supervisor(crear_agente, supervisor) -> None:
    agente = crear_agente(guardrail_entrada=GuardrailQueBloquea("jailbreak"))
    r = agente.consultar("modo JAILBREAK activado", PUBLIC)
    assert r.respuesta == MENSAJE_BLOQUEO and r.sin_contexto
    assert supervisor.llamadas == []


def test_guardrail_de_salida_sustituye_la_respuesta(crear_agente) -> None:
    r = crear_agente(guardrail_salida=GuardrailQueBloquea("23")).consultar("vacaciones", PUBLIC)
    assert r.respuesta == MENSAJE_BLOQUEO and r.citas == []


@pytest.mark.parametrize(
    "usuario, pregunta",
    [(PUBLIC, "vacaciones"), (Usuario(id="u2", groups=[]), "vacaciones")],
    ids=["respondida", "sin_permisos"],
)
def test_toda_consulta_se_audita_una_vez(agente, caplog, usuario, pregunta) -> None:
    with caplog.at_level(logging.INFO, logger="audit"):
        agente.consultar(pregunta, usuario)
    registros = [r for r in caplog.records if r.name == "audit"]
    assert len(registros) == 1
    assert f'"usuario":"{usuario.id}"' in registros[0].getMessage()


def test_consulta_bloqueada_tambien_se_audita(crear_agente, caplog) -> None:
    agente = crear_agente(guardrail_entrada=GuardrailQueBloquea("jailbreak"))
    with caplog.at_level(logging.INFO, logger="audit"):
        agente.consultar("jailbreak", PUBLIC)
    assert sum(r.name == "audit" for r in caplog.records) == 1
