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
    return {"respuesta": respuesta, "citas": [cita] if citas else [], "sin_contexto": sin_contexto}


def test_matriz_del_repo_es_valida() -> None:
    matriz = cargar_matriz()
    assert matriz.escenarios
    informe = generar_informe(matriz, [])
    assert "| auditoria | 1 | 0 |" in informe and "sin escenarios" in informe


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
