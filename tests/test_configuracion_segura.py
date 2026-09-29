"""Regresión de configuración: los modos de depuración nunca quedan expuestos."""

import re
from pathlib import Path

import yaml

RAIZ = Path(__file__).parents[1]
FLAGS_SOLO_LOCAL = (
    "IDENTIDAD_DEBUG",
    "GESTION_DOCUMENTOS",
    "EXPONER_TOPOLOGIA",
    "SELECCION_LIBRE_DE_ROL",
)


def _compose() -> dict:
    return yaml.safe_load((RAIZ / "docker-compose.yml").read_text(encoding="utf-8"))


def test_servicios_solo_escuchan_en_localhost() -> None:
    servicios = _compose()["services"]
    for nombre in ("app", "qdrant", "web", "redis", "postgres"):
        for puerto in servicios[nombre]["ports"]:
            assert str(puerto).startswith("127.0.0.1:"), f"{nombre} expuesto: {puerto}"


def test_la_web_elimina_la_cabecera_de_identidad() -> None:
    conf = (RAIZ / "web" / "default.conf.template").read_text(encoding="utf-8")
    # En cada location que hace proxy al backend.
    assert conf.count("proxy_pass") == conf.count('proxy_set_header X-Usuario-Grupos "";') == 2


def test_terraform_no_activa_modos_de_depuracion() -> None:
    """En Azure estos flags solo pueden aparecer fijados explícitamente a "false"."""
    for tf in (RAIZ / "infra").rglob("*.tf"):
        contenido = tf.read_text(encoding="utf-8")
        for flag in FLAGS_SOLO_LOCAL:
            for m in re.finditer(rf"\b{flag}\b\s*=\s*(\S+)", contenido):
                assert m.group(1) == '"false"', f"{flag} = {m.group(1)} en {tf}"
            menciones = len(re.findall(rf"\b{flag}\b", contenido))
            asignaciones = len(re.findall(rf"\b{flag}\b\s*=", contenido))
            assert menciones == asignaciones, f"{flag} usado fuera de una asignación en {tf}"


def test_trazas_completas_nunca_en_terraform() -> None:
    for tf in (RAIZ / "infra").rglob("*.tf"):
        assert (
            '"completo"'
            not in re.sub(r"#.*", "", tf.read_text(encoding="utf-8")).split("TRAZAS_MODO")[-1][:80]
        ), f"TRAZAS_MODO completo en {tf}"


def test_compose_no_contiene_contrasenas() -> None:
    """Regla 3: las credenciales vienen de .env, nunca escritas en el compose."""
    postgres = _compose()["services"]["postgres"]["environment"]
    assert postgres["POSTGRES_PASSWORD"].startswith("${POSTGRES_PASSWORD")


def test_cliente_azure_openai_reintenta_429_con_backoff() -> None:
    from app.config import Settings
    from app.retrieval.azure_openai import _cliente_base

    s = Settings(
        azure_openai_endpoint="https://x.openai.azure.com/",
        azure_openai_api_key="k",
        azure_openai_max_reintentos=4,
        azure_openai_timeout_s=30,
    )
    c = _cliente_base(s)
    assert c.max_retries == 4 and c.timeout == 30
