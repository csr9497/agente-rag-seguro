"""Configuración de las pruebas de integración.

Variables de entorno:
    INTEGRATION_BASE_URL        URL del backend (p. ej. http://localhost:8000). Obligatoria.
    INTEGRATION_IDENTIDAD_DEBUG "true" si el destino tiene IDENTIDAD_DEBUG=true; habilita los
                                escenarios con `grupos`.
    INTEGRATION_REPORT          Ruta del informe (por defecto reports/integracion.md).
"""

import os
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from tests.integration.evaluador import ResultadoEscenario, cargar_matriz, generar_informe

_RESULTADOS: list[ResultadoEscenario] = []


@pytest.fixture(scope="session")
def base_url() -> str:
    url = os.environ.get("INTEGRATION_BASE_URL")
    if not url:
        pytest.skip("INTEGRATION_BASE_URL no definida")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def identidad_debug() -> bool:
    return os.environ.get("INTEGRATION_IDENTIDAD_DEBUG", "").lower() == "true"


@pytest.fixture(scope="session")
def http(base_url: str) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=base_url, timeout=60) as client:
        yield client


@pytest.fixture(scope="session")
def resultados() -> list[ResultadoEscenario]:
    return _RESULTADOS


def pytest_sessionfinish(session: pytest.Session) -> None:
    if not _RESULTADOS:
        return
    destino = Path(os.environ.get("INTEGRATION_REPORT", "reports/integracion.md"))
    destino.parent.mkdir(parents=True, exist_ok=True)
    orden = {e.id: i for i, e in enumerate(cargar_matriz().escenarios)}
    _RESULTADOS.sort(key=lambda r: orden.get(r.id, 0))
    destino.write_text(
        generar_informe(cargar_matriz(), _RESULTADOS, os.environ.get("INTEGRATION_BASE_URL")),
        encoding="utf-8",
    )
