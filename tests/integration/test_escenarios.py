"""Ejecuta la matriz de escenarios contra un despliegue real: cada escenario abre una
conversación con su rol y envía la pregunta.

    INTEGRATION_BASE_URL=http://localhost:8000 uv run pytest -m integration

El destino necesita SELECCION_LIBRE_DE_ROL=true (local) y los documentos de ejemplo
ingestados (`make ingest`).
"""

import httpx
import pytest

from tests.integration.evaluador import (
    Escenario,
    ResultadoEscenario,
    cargar_matriz,
    evaluar,
)

MATRIZ = cargar_matriz()


def _json(resp: httpx.Response):  # noqa: ANN202
    es_json = resp.headers.get("content-type", "").startswith("application/json")
    return resp.json() if es_json else None


@pytest.mark.integration
@pytest.mark.parametrize("escenario", MATRIZ.escenarios, ids=lambda e: e.id)
def test_escenario(
    escenario: Escenario, http: httpx.Client, resultados: list[ResultadoEscenario]
) -> None:
    conv = http.post("/conversaciones", json={"rol_id": escenario.rol})
    if conv.status_code != 201:
        resultado = evaluar(escenario, conv.status_code, _json(conv), MATRIZ.canarios)
    else:
        url = f"/conversaciones/{conv.json()['id']}/mensajes"
        for previa in escenario.turnos_previos:
            http.post(url, json={"pregunta": previa})
        resp = http.post(url, json=escenario.cuerpo())
        resultado = evaluar(escenario, resp.status_code, _json(resp), MATRIZ.canarios)
    resultados.append(resultado)

    assert resultado.estado == "ok", "\n".join(
        [*resultado.fallos, f"respuesta: {resultado.respuesta!r}"]
    )
