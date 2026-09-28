"""Ejecuta la matriz de escenarios contra un despliegue real.

    INTEGRATION_BASE_URL=http://localhost:8000 INTEGRATION_IDENTIDAD_DEBUG=true \
        uv run pytest -m integration
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


@pytest.mark.integration
@pytest.mark.parametrize("escenario", MATRIZ.escenarios, ids=lambda e: e.id)
def test_escenario(
    escenario: Escenario,
    http: httpx.Client,
    identidad_debug: bool,
    resultados: list[ResultadoEscenario],
) -> None:
    if escenario.grupos is not None and not identidad_debug:
        resultados.append(
            ResultadoEscenario(id=escenario.id, capacidades=escenario.capacidades, estado="omitido")
        )
        pytest.skip("requiere IDENTIDAD_DEBUG en el destino")

    resp = http.post("/consultar", json=escenario.cuerpo(), headers=escenario.cabeceras())
    body = (
        resp.json() if resp.headers.get("content-type", "").startswith("application/json") else None
    )
    resultado = evaluar(escenario, resp.status_code, body)
    resultados.append(resultado)

    assert resultado.estado == "ok", "\n".join(
        [*resultado.fallos, f"respuesta: {resultado.respuesta!r}"]
    )
