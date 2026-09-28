"""Tests del propio evaluador de integración (corren en la suite unitaria)."""

import pytest
from pydantic import ValidationError

from tests.integration.evaluador import Escenario, Matriz, cargar_matriz, evaluar, generar_informe


def _esc(**esperado) -> Escenario:
    return Escenario(id="T-1", descripcion="t", capacidades=["x"], pregunta="p", esperado=esperado)


def _body(respuesta="Son 23 días [1].", fuente="public/a.md", sin_contexto=False, citas=True):
    cita = {
        "numero": 1,
        "doc_id": fuente,
        "chunk_id": f"{fuente}#0",
        "fuente": fuente,
        "fragmento": "...",
        "score": 0.9,
    }
    return {
        "pregunta": "p",
        "respuesta": respuesta,
        "citas": [cita] if citas else [],
        "sin_contexto": sin_contexto,
        "documentos_consultados": [fuente, "public/otro.md"],
        "fragmentos_descartados": 0,
        "hallazgos": [],
    }


def test_matriz_del_repo_es_valida() -> None:
    matriz = cargar_matriz()
    assert matriz.escenarios
    informe = generar_informe(matriz, [])
    assert "| auditoria | 1 | 0 | 0 | 0 | 0 | 3 | 🧪 solo unitarios |" in informe


def test_ok_cuando_cumple_todo() -> None:
    e = _esc(
        sin_contexto=False, min_citas=1, alguna_fuente_de=["public/a.md"], contiene_alguno=["23"]
    )
    assert evaluar(e, 200, _body()).estado == "ok"


def test_detecta_fuente_prohibida_y_fuga() -> None:
    e = _esc(fuentes_prohibidas=["rrhh/"], no_contiene=["58.000"])
    r = evaluar(e, 200, _body(respuesta="B3 hasta 58.000 [1]", fuente="rrhh/b.md"))
    assert r.estado == "fallo" and len(r.fallos) == 2


def test_contrato_invalido() -> None:
    r = evaluar(_esc(), 200, {"respuesta": "x"})
    assert r.fallos[0].startswith("contrato")


def test_invariante_respuesta_sin_citas() -> None:
    r = evaluar(_esc(), 200, _body(citas=False))
    assert any("sin citas" in f for f in r.fallos)


def test_status_esperado() -> None:
    assert evaluar(_esc(status=422), 422, {"detail": []}).estado == "ok"
    assert evaluar(_esc(), 500, None).estado == "fallo"


def test_matriz_rechaza_capacidad_no_declarada() -> None:
    with pytest.raises(ValidationError):
        Matriz.model_validate(
            {
                "version": 1,
                "fase_actual": 1,
                "capacidades": {},
                "escenarios": [_esc().model_dump()],
            }
        )


def test_escenario_exige_pregunta_o_payload() -> None:
    with pytest.raises(ValidationError):
        Escenario(id="x", descripcion="d", capacidades=["c"], esperado={})


def test_todas_fuentes_de() -> None:
    e = _esc(todas_fuentes_de=["public/a.md", "public/b.md"])
    r = evaluar(e, 200, _body())
    assert r.fallos == ["faltan citas de ['public/b.md']"]


def test_referencias_a_tests_unitarios_existen() -> None:
    """Cada test declarado en la matriz existe: la cobertura documentada es real."""
    from pathlib import Path

    for nombre, cap in cargar_matriz().capacidades.items():
        for ref in cap.tests_unitarios:
            ruta, funcion = ref.split("::")
            fuente = Path(ruta).read_text(encoding="utf-8")
            assert f"def {funcion}(" in fuente, f"{nombre}: no existe {ref}"


def test_canario_en_rol_no_autorizado_es_fuga() -> None:
    canarios = {"CANARIO-X": ["rrhh"]}
    fuga = _body(respuesta="El código es CANARIO-X [1]")
    r = evaluar(_esc(), 200, fuga, canarios)
    assert r.estado == "fallo" and r.fallos[0].startswith("FUGA")
    rrhh = Escenario(
        id="T", descripcion="t", capacidades=["x"], rol="rrhh", pregunta="p", esperado={}
    )
    assert evaluar(rrhh, 200, fuga, canarios).estado == "ok"


def test_canario_se_busca_aunque_falle_el_status() -> None:
    r = evaluar(_esc(status=403), 403, {"detail": "CANARIO-X"}, {"CANARIO-X": ["rrhh"]})
    assert r.estado == "fallo"


def test_no_consultados() -> None:
    r = evaluar(_esc(no_consultados=["public/otro"]), 200, _body())
    assert r.fallos == ["documentos consultados no permitidos: ['public/otro.md']"]


def test_metrica_recall_docs() -> None:
    e = Escenario(id="T", descripcion="t", capacidades=["x"], pregunta="p", esperado={},
                  docs_relevantes=["public/a.md", "public/b.md"])  # fmt: skip
    assert evaluar(e, 200, _body()).metricas == {"recall_docs": 0.5}
