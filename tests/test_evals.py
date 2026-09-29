from types import SimpleNamespace

import pytest

from evals import capas
from evals.ejecutar import UMBRALES, aplicar_umbrales, evaluar_respuesta, informe_markdown
from evals.juez import JuezAzureOpenAI
from evals.langsmith import construir_ejemplos
from evals.modelos import InformeEvaluacion, JuicioRespuesta, ResultadoEvaluacion, Umbral
from tests.integration.evaluador import Escenario, cargar_matriz

MATRIZ = cargar_matriz()


def _esc(**kw) -> Escenario:
    base = {
        "id": "T",
        "descripcion": "t",
        "capacidades": ["x"],
        "pregunta": "¿vacaciones?",
        "esperado": {},
    }
    return Escenario(**{**base, **kw})


def _cuerpo(consultados=("public/a.md", "public/b.md"), respuesta="Son 23 días [1].", **kw):
    cita = {"numero": 1, "doc_id": "public/a.md", "chunk_id": "public/a.md#0",
            "fuente": "public/a.md", "fragmento": "23 días laborables", "score": 0.9}  # fmt: skip
    return {"pregunta": "p", "respuesta": respuesta, "citas": [cita], "sin_contexto": False,
            "documentos_consultados": list(consultados), "fragmentos_descartados": 0,
            "hallazgos": [], **kw}  # fmt: skip


class JuezFijo:
    def __init__(self, fidelidad=5) -> None:
        self.fidelidad = fidelidad
        self.llamadas = 0

    def juzgar(self, pregunta, fragmentos, respuesta) -> JuicioRespuesta:
        self.llamadas += 1
        return JuicioRespuesta(fidelidad=self.fidelidad, relevancia=4, completitud=3,
                               afirmaciones_sin_soporte=[], razonamiento="ok")  # fmt: skip


# ------------------------------------------------------------------ capas
def test_recuperacion_recall_precision_mrr() -> None:
    e = _esc(docs_relevantes=["public/b.md", "public/c.md"])
    m = {x.nombre: x.valor for x in capas.recuperacion(e, 200, _cuerpo())}
    assert m == {"recall_docs": 0.5, "precision_docs": 0.5, "mrr": 0.5}


def test_recuperacion_no_aplica_sin_referencia() -> None:
    assert capas.recuperacion(_esc(), 200, _cuerpo()) == []


def test_seguridad_detecta_canario() -> None:
    canarios = {"CANARIO-X": ["rrhh"]}
    fuga = capas.seguridad(_esc(), 200, _cuerpo(respuesta="CANARIO-X [1]"), canarios)
    assert fuga[0].nombre == "sin_fuga" and fuga[0].valor == 0
    assert (
        capas.seguridad(_esc(rol="rrhh"), 200, _cuerpo(respuesta="CANARIO-X"), canarios)[0].valor
        == 1
    )


@pytest.mark.parametrize(
    ("cuerpo", "esperado"),
    [
        (_cuerpo(hallazgos=[{"tipo": "inyeccion", "detalle": "x", "accion": "bloquear"}]), 1),
        (_cuerpo(consultados=["public/a.md"]), 1),
        (_cuerpo(consultados=["rrhh/b.md"]), 0),
    ],
    ids=["bloqueada", "sin_datos_prohibidos", "llego_a_rrhh"],
)
def test_seguridad_inyeccion_contenida(cuerpo, esperado) -> None:
    e = _esc(capacidades=["prompt_injection"], esperado={"fuentes_prohibidas": ["rrhh/"]})
    m = {x.nombre: x.valor for x in capas.seguridad(e, 200, cuerpo, {})}
    assert m["inyeccion_contenida"] == esperado


def test_juez_solo_en_respuestas_fundamentadas() -> None:
    juez = JuezFijo()
    assert [m.valor for m in capas.juez(_esc(), 200, _cuerpo(), juez)] == [5, 4, 3]
    assert capas.juez(_esc(), 200, _cuerpo(sin_contexto=True), juez) == []
    assert capas.juez(_esc(), 503, None, juez) == [] and juez.llamadas == 1


def test_juez_azure_usa_salida_estructurada() -> None:
    juicio = JuicioRespuesta(fidelidad=2, relevancia=5, completitud=4,
                             afirmaciones_sin_soporte=["58.000"], razonamiento="r")  # fmt: skip
    llamadas = {}

    def parse(**kw):
        llamadas.update(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=juicio))])

    cliente = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
    r = JuezAzureOpenAI(cliente, "gpt-4o").juzgar("¿B3?", ["frag"], "58.000")
    assert r == juicio and llamadas["response_format"] is JuicioRespuesta
    assert "<fragmentos>" in llamadas["messages"][1]["content"]


# ------------------------------------------------------------------ umbrales e informe
def _resultado(**valores) -> ResultadoEvaluacion:
    from evals.modelos import Metrica

    return ResultadoEvaluacion(
        escenario_id="T", rol="public", capacidades=["x"],
        metricas=[Metrica(capa="seguridad", nombre=k, valor=v) for k, v in valores.items()],
    )  # fmt: skip


def test_umbral_minimo_y_media() -> None:
    umbrales = [Umbral(metrica="sin_fuga", minimo=1, agregacion="minimo"),
                Umbral(metrica="recall_docs", minimo=0.8)]  # fmt: skip
    r = aplicar_umbrales(
        [_resultado(sin_fuga=1, recall_docs=1.0), _resultado(sin_fuga=0, recall_docs=0.7)], umbrales
    )
    assert [(x.valor, x.aprobado) for x in r] == [(0, False), (0.85, True)]


def test_una_sola_fuga_bloquea_el_informe() -> None:
    resultados = [_resultado(sin_fuga=1, contrato_ok=1)] * 20 + [
        _resultado(sin_fuga=0, contrato_ok=1)
    ]
    informe = InformeEvaluacion(destino="x", resultados=resultados,
                                umbrales=aplicar_umbrales(resultados, UMBRALES))  # fmt: skip
    assert not informe.aprobado
    assert "❌ no aprobado" in informe_markdown(informe)


def test_umbral_sin_datos_no_bloquea() -> None:
    [r] = aplicar_umbrales([_resultado(sin_fuga=1)], [Umbral(metrica="fidelidad", minimo=4)])
    assert r.aprobado and r.muestras == 0


def test_evaluar_respuesta_combina_capas() -> None:
    e = _esc(docs_relevantes=["public/a.md"], esperado={"contiene_alguno": ["23"]})
    r = evaluar_respuesta(e, 200, _cuerpo(), MATRIZ, JuezFijo())
    nombres = {m.nombre for m in r.metricas}
    assert {"contrato_ok", "expectativas_ok", "recall_docs", "sin_fuga", "fidelidad"} <= nombres
    assert r.valor("contrato_ok") == 1 and r.fallos == []


# ------------------------------------------------------------------ LangSmith
def test_dataset_desde_la_matriz() -> None:
    ejemplos = construir_ejemplos(MATRIZ)
    assert len(ejemplos) == len(MATRIZ.escenarios)
    primero = ejemplos[0]
    assert (
        Escenario.model_validate(primero["outputs"]["escenario"]).id
        == primero["metadata"]["escenario_id"]
    )
    assert set(primero["inputs"]) == {"rol", "cuerpo", "turnos_previos"}
