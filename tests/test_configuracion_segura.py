"""Regresión de configuración: los modos de depuración nunca quedan expuestos."""

import re
from pathlib import Path

import yaml

RAIZ = Path(__file__).parents[1]
FLAGS_SOLO_LOCAL = ("IDENTIDAD_DEBUG", "GESTION_DOCUMENTOS", "EXPONER_TOPOLOGIA")


def _compose() -> dict:
    return yaml.safe_load((RAIZ / "docker-compose.yml").read_text(encoding="utf-8"))


def test_servicios_solo_escuchan_en_localhost() -> None:
    servicios = _compose()["services"]
    for nombre in ("app", "qdrant", "web"):
        for puerto in servicios[nombre]["ports"]:
            assert str(puerto).startswith("127.0.0.1:"), f"{nombre} expuesto: {puerto}"


def test_la_web_elimina_la_cabecera_de_identidad() -> None:
    conf = (RAIZ / "web" / "default.conf.template").read_text(encoding="utf-8")
    # En cada location que hace proxy al backend.
    assert conf.count("proxy_pass") == conf.count('proxy_set_header X-Usuario-Grupos "";') == 2


def test_terraform_no_activa_modos_de_depuracion() -> None:
    for tf in (RAIZ / "infra").rglob("*.tf"):
        contenido = tf.read_text(encoding="utf-8")
        for flag in FLAGS_SOLO_LOCAL:
            assert not re.search(rf"\b{flag}\b", contenido), f"{flag} en {tf}"
