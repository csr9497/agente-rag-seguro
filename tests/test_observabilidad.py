import os

import pytest

from app.config import Settings
from app.observabilidad import (
    OCULTO,
    ConfiguracionInseguraError,
    configurar_trazas,
    ocultar,
    traza_consulta,
    usuario_seudonimo,
)


def test_la_suite_no_traza() -> None:
    assert Settings().trazas_modo == "apagado"
    assert os.environ["LANGSMITH_TRACING"] == "false"


def test_apagado_no_devuelve_cliente() -> None:
    assert configurar_trazas(Settings(trazas_modo="apagado")) is None
    assert os.environ["LANGSMITH_TRACING"] == "false"


def test_sin_clave_no_traza() -> None:
    assert configurar_trazas(Settings(trazas_modo="completo", langsmith_api_key=None)) is None


def test_completo_en_prod_no_arranca() -> None:
    with pytest.raises(ConfiguracionInseguraError):
        configurar_trazas(Settings(trazas_modo="completo", entorno="prod", langsmith_api_key="x"))


def test_ocultar_enmascara_pii_y_oculta_contenido() -> None:
    entrada = {
        "pregunta": "Soy ana@empresa.com con DNI 12345678Z",
        "recuperados": [{"chunk": {"doc_id": "rrhh/b.md", "contenido": "Banda B3 58.000"}}],
        "mensajes": [
            {"role": "user", "content": "hola ana@empresa.com"},
            {"role": "tool", "content": "- (rrhh/b.md) Banda B3 58.000"},
        ],
        "citas": [{"fragmento": "58.000", "doc_id": "rrhh/b.md"}],
    }
    salida = ocultar(entrada)
    texto = str(salida)
    assert "ana@empresa.com" not in texto and "12345678Z" not in texto and "58.000" not in texto
    assert salida["recuperados"][0]["chunk"]["doc_id"] == "rrhh/b.md"  # se ve qué, no el texto
    assert salida["recuperados"][0]["chunk"]["contenido"] == OCULTO
    assert salida["mensajes"][1]["content"] == OCULTO
    assert "[EMAIL]" in salida["mensajes"][0]["content"]


def test_config_de_la_consulta_lleva_metadata_sin_identificar_al_usuario() -> None:
    with traza_consulta(None, Settings(), roles=["rrhh"], conversacion_id="c1") as config:
        pass
    assert config["run_name"] == "consulta"
    assert "rol:rrhh" in config["tags"]
    assert config["metadata"]["conversacion_id"] == "c1"
    assert len(usuario_seudonimo("ana")) == 12 and usuario_seudonimo("ana") != "ana"
