"""Registro con la ACL completa (roles, departamentos y usuarios), revocación y caducidad.
El verificador es el re-chequeo contra la fuente de verdad antes de que el LLM vea nada."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import Boolean, Column, Integer, MetaData, String, Table, Text, inspect

from app.models.schemas import Chunk
from app.persistencia.modelos import DocumentoRegistrado
from app.persistencia.repositorios import (
    SqlRepositorioDepartamentosUsuario,
    SqlRepositorioDocumentos,
    crear_motor,
    inicializar,
)
from app.security.acceso import VerificadorRegistro


@pytest.fixture
def motor():
    m = crear_motor("sqlite://")
    inicializar(m)
    return m


def _doc(acl, doc_id="it/vpn.md", **kw) -> DocumentoRegistrado:
    return DocumentoRegistrado(
        doc_id=doc_id, titulo="VPN", roles=acl, doc_hash="h" * 64, chunks=1, **kw
    )


def _chunk(acl, doc_id="it/vpn.md") -> Chunk:
    return Chunk(
        chunk_id=f"{doc_id}#0", doc_id=doc_id, fuente=doc_id, contenido="x",
        acl_groups=acl, doc_hash="h" * 64,
    )  # fmt: skip


# ------------------------------------------------------------------------------- registro
def test_la_acl_completa_se_guarda_y_se_recupera(motor) -> None:
    repo = SqlRepositorioDocumentos(motor)
    repo.registrar(_doc(["rrhh", "dept:it", "user:github:ana"], subido_por="github:luis"))
    doc = repo.obtener("it/vpn.md")
    assert doc.roles == ["dept:it", "rrhh", "user:github:ana"]
    assert doc.subido_por == "github:luis" and not doc.revocado and doc.expira_en is None


def test_departamento_inexistente_no_se_registra(motor) -> None:
    with pytest.raises(Exception):  # noqa: B017, PT011 — clave foránea
        SqlRepositorioDocumentos(motor).registrar(_doc(["dept:marte"]))


def test_revocar_y_caducidad(motor) -> None:
    repo = SqlRepositorioDocumentos(motor)
    repo.registrar(_doc(["public"], expira_en="2026-12-31T00:00:00+00:00"))
    repo.marcar_revocado("it/vpn.md", True)
    doc = repo.obtener("it/vpn.md")
    assert doc.revocado and doc.expira_en == "2026-12-31T00:00:00+00:00"


def test_listar_por_rol_incluye_entradas_de_la_acl(motor) -> None:
    repo = SqlRepositorioDocumentos(motor)
    repo.registrar(_doc(["dept:it"]))
    assert [d.doc_id for d in repo.listar(rol_id="dept:it")] == ["it/vpn.md"]


def test_departamentos_de_cada_usuario(motor) -> None:
    repo = SqlRepositorioDepartamentosUsuario(motor)
    assert repo.de("github:ana") == []
    repo.asignar("github:ana", ["it", "ventas"])
    assert repo.de("github:ana") == ["it", "ventas"]
    repo.asignar("github:ana", ["it"])
    assert repo.de("github:ana") == ["it"]
    with pytest.raises(Exception):  # noqa: B017, PT011 — departamento inexistente
        repo.asignar("github:ana", ["marte"])


def test_migra_una_base_anterior_sin_columnas_nuevas() -> None:
    """create_all no altera tablas existentes: _migrar añade revocado y expira_en."""
    m = crear_motor("sqlite://")
    viejo = MetaData()
    Table(
        "documentos", viejo,
        Column("doc_id", String(400), primary_key=True), Column("titulo", String(200)),
        Column("doc_hash", String(64)), Column("chunks", Integer), Column("estado", String(16)),
        Column("motivo_estado", Text), Column("subido_por", String(64)),
        Column("indexado_en", String(32)), Column("legacy", Boolean),
    )  # fmt: skip
    viejo.create_all(m)
    inicializar(m)
    columnas = {c["name"] for c in inspect(m).get_columns("documentos")}
    assert {"revocado", "expira_en"} <= columnas


# ----------------------------------------------------------------------------- verificador
@pytest.fixture
def verificador(motor):
    repo = SqlRepositorioDocumentos(motor)
    reloj = {"ahora": datetime(2026, 10, 1, tzinfo=UTC)}
    return repo, reloj, VerificadorRegistro(repo, ahora=lambda: reloj["ahora"])


@pytest.mark.parametrize(
    ("acl", "grupos", "autorizado"),
    [
        (["dept:it"], ["public", "dept:it", "user:u1"], True),
        (["dept:it"], ["public", "dept:ventas", "user:u1"], False),
        (["user:u1"], ["public", "user:u1"], True),
        (["user:u1"], ["public", "user:u2"], False),
        (["rrhh"], ["public", "dept:it", "user:u1"], False),
    ],
)
def test_verificador_con_la_acl_nueva(verificador, acl, grupos, autorizado) -> None:
    repo, _, v = verificador
    repo.registrar(_doc(acl))
    assert (v.motivo_rechazo(_chunk(acl), grupos) is None) is autorizado


def test_documento_revocado_se_rechaza_aunque_siga_en_el_indice(verificador) -> None:
    repo, _, v = verificador
    repo.registrar(_doc(["public"]))
    repo.marcar_revocado("it/vpn.md", True)
    assert v.motivo_rechazo(_chunk(["public"]), ["public"]) == "documento_revocado"


def test_documento_caducado_se_rechaza(verificador) -> None:
    repo, reloj, v = verificador
    repo.registrar(_doc(["public"], expira_en="2026-10-15T00:00:00+00:00"))
    assert v.motivo_rechazo(_chunk(["public"]), ["public"]) is None
    reloj["ahora"] = datetime(2026, 10, 15, tzinfo=UTC)
    assert v.motivo_rechazo(_chunk(["public"]), ["public"]) == "documento_caducado"
