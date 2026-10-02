"""Ingesta con la ACL completa: un `<documento>.acl.json` al lado del original declara roles,
departamentos, usuarios y caducidad; llega igual al índice (acl_groups) y al registro."""

import json
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from app.persistencia.repositorios import (
    SqlRepositorioDocumentos,
    SqlRepositorioRoles,
    crear_motor,
    inicializar,
)
from app.retrieval.qdrant_retriever import QdrantRetriever
from ingestor.gestor import GestorDocumentos
from ingestor.sources import LocalFolderSource
from ingestor.validacion import validar_documento
from tests.fakes import DIM, FakeEmbedder

TEXTO = "# VPN\n\nPara conectar a la VPN corporativa usa el cliente oficial y tu usuario.\n"


@pytest.fixture
def entorno(tmp_path):
    motor = crear_motor("sqlite://")
    inicializar(motor)
    registro = SqlRepositorioDocumentos(motor)
    retriever = QdrantRetriever(QdrantClient(":memory:"), "t", DIM)
    gestor = GestorDocumentos(
        FakeEmbedder(), retriever, registro=registro, roles=SqlRepositorioRoles(motor),
        departamentos=lambda: {"it", "rrhh", "ventas"},
    )  # fmt: skip
    return tmp_path, gestor, registro, retriever


def _escribir(raiz: Path, rel: str, acl: dict | None = None) -> None:
    ruta = raiz / rel
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(TEXTO, encoding="utf-8")
    if acl is not None:
        ruta.with_name(ruta.name + ".acl.json").write_text(json.dumps(acl), encoding="utf-8")


def test_sidecar_con_departamento_usuario_y_caducidad(entorno) -> None:
    raiz, gestor, registro, retriever = entorno
    _escribir(raiz, "public/vpn.md", {
        "departamentos": ["it"], "usuarios": ["github:ana"],
        "expira_en": "2027-01-01T00:00:00+00:00",
    })  # fmt: skip
    informe = gestor.sincronizar(LocalFolderSource(raiz))
    assert informe.por_estado() == {"indexado": 1}  # el .acl.json no se indexa como documento
    doc = registro.obtener("public/vpn.md")
    assert doc.roles == ["dept:it", "user:github:ana"]  # el sidecar sustituye a la carpeta
    assert doc.expira_en == "2027-01-01T00:00:00+00:00"
    (indexado,) = retriever.list_documents(["dept:it"])
    assert sorted(indexado.acl_groups) == ["dept:it", "user:github:ana"]


def test_sin_sidecar_manda_la_carpeta(entorno) -> None:
    raiz, gestor, registro, _ = entorno
    _escribir(raiz, "rrhh/vpn.md")
    gestor.sincronizar(LocalFolderSource(raiz))
    assert registro.obtener("rrhh/vpn.md").roles == ["rrhh"]


def test_departamento_inexistente_se_rechaza(entorno) -> None:
    raiz, gestor, registro, _ = entorno
    _escribir(raiz, "public/vpn.md", {"departamentos": ["marte"]})
    (op,) = gestor.sincronizar(LocalFolderSource(raiz)).operaciones
    assert op.estado == "rechazado" and "marte" in " ".join(op.motivos)
    assert registro.obtener("public/vpn.md") is None


@pytest.mark.parametrize(
    "acl", [{"usuarios": ["o'brien"]}, {"departamentos": ["IT,rrhh"]}, {"roles": "public"}, "{mal"]
)
def test_sidecar_mal_formado_o_peligroso_se_rechaza(entorno, acl) -> None:
    raiz, gestor, registro, _ = entorno
    _escribir(raiz, "public/vpn.md")
    sidecar = raiz / "public" / "vpn.md.acl.json"
    sidecar.write_text(acl if isinstance(acl, str) else json.dumps(acl), encoding="utf-8")
    (op,) = gestor.sincronizar(LocalFolderSource(raiz)).operaciones
    assert op.estado == "rechazado" and registro.obtener("public/vpn.md") is None


def test_cambiar_solo_la_caducidad_actualiza_el_registro(entorno) -> None:
    raiz, gestor, registro, _ = entorno
    _escribir(raiz, "public/vpn.md", {"roles": ["public"]})
    gestor.sincronizar(LocalFolderSource(raiz))
    _escribir(
        raiz, "public/vpn.md", {"roles": ["public"], "expira_en": "2026-12-01T00:00:00+00:00"}
    )
    gestor.sincronizar(LocalFolderSource(raiz))
    assert registro.obtener("public/vpn.md").expira_en == "2026-12-01T00:00:00+00:00"


def test_validacion_de_roles_explicitos_acepta_la_acl_nueva() -> None:
    ok = validar_documento("x/a.md", TEXTO.encode(), roles=["dept:it", "user:github:ana"])
    assert ok.aceptado and ok.acl_groups == ["dept:it", "user:github:ana"]
    malo = validar_documento("x/a.md", TEXTO.encode(), roles=["user:a,b"])
    assert not malo.aceptado
