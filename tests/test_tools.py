"""Tools del agente: permisos (grupos del estado, no del LLM), validación de argumentos y
respuesta indistinguible entre 'no existe' y 'sin acceso'."""

import json

import pytest
from pydantic import ValidationError

from app.models.schemas import Usuario
from app.tools.base import SIN_ACCESO, schema_openai
from app.tools.documentos import (
    BuscarEnDocumento,
    BuscarEnDocumentoArgs,
    LeerDocumento,
    LeerDocumentoArgs,
    ListarDocumentos,
    ListarDocumentosArgs,
)
from app.tools.rag_retrieve import RagRetrieve, RagRetrieveArgs
from ingestor.gestor import GestorDocumentos
from tests.fakes import FakeEmbedder

PUBLIC = Usuario(id="a", groups=["public"])
RRHH = Usuario(id="b", groups=["rrhh"])
AMBOS = Usuario(id="c", groups=["public", "rrhh"])
TODAS = [RagRetrieve, ListarDocumentos, LeerDocumento, BuscarEnDocumento]


# -------------------------------------------------------------------- rag_retrieve
def test_rag_retrieve_filtra_por_grupos_del_usuario(retriever_con_docs, embedder) -> None:
    tool = RagRetrieve(embedder, retriever_con_docs)
    args = RagRetrieveArgs(consulta="bandas salariales")
    public = tool.ejecutar(args, PUBLIC, top_k=10).chunks
    rrhh = tool.ejecutar(args, RRHH, top_k=10).chunks
    assert public and all(r.chunk.doc_id.startswith("public/") for r in public)
    assert {r.chunk.doc_id for r in rrhh} == {"rrhh/bandas-salariales.md"}


def test_rag_retrieve_respeta_top_k(retriever_con_docs, embedder) -> None:
    tool = RagRetrieve(embedder, retriever_con_docs)
    assert len(tool.ejecutar(RagRetrieveArgs(consulta="política"), PUBLIC, 1).chunks) == 1


# --------------------------------------------------------------- listar_documentos
def test_listar_documentos_devuelve_catalogo_citable_filtrado(retriever_con_docs) -> None:
    r = ListarDocumentos(retriever_con_docs).ejecutar(ListarDocumentosArgs(), PUBLIC, 4)
    [catalogo] = r.chunks
    assert catalogo.chunk.fuente == "catálogo de documentos"
    assert "public/politica-vacaciones.md" in catalogo.chunk.contenido
    assert "rrhh/" not in catalogo.chunk.contenido
    assert catalogo.chunk.acl_groups == ["public"]


def test_listar_documentos_por_grupo_no_amplia_acceso(retriever_con_docs) -> None:
    tool = ListarDocumentos(retriever_con_docs)
    r = tool.ejecutar(ListarDocumentosArgs(grupo="rrhh"), PUBLIC, 4)
    assert r.chunks == [] and r.nota == "No hay documentos visibles."
    r = tool.ejecutar(ListarDocumentosArgs(grupo="rrhh"), AMBOS, 4)
    assert "rrhh/bandas-salariales.md" in r.chunks[0].chunk.contenido
    assert "public/" not in r.chunks[0].chunk.contenido


# ------------------------------------------------------------------ leer_documento
def test_leer_documento_en_orden_y_con_ventana(retriever) -> None:
    texto = "\n\n".join(f"Sección {i}. " + "x" * 950 for i in range(5)).encode()
    GestorDocumentos(FakeEmbedder(), retriever).indexar("public/manual.md", texto)
    tool = LeerDocumento(retriever)

    r = tool.ejecutar(LeerDocumentoArgs(doc_id="public/manual.md", desde=1, cantidad=2), PUBLIC, 4)
    assert [c.chunk.chunk_id for c in r.chunks] == ["public/manual.md#1", "public/manual.md#2"]
    assert r.nota == "public/manual.md: fragmentos 1-2 de 5."

    fuera = tool.ejecutar(LeerDocumentoArgs(doc_id="public/manual.md", desde=9), PUBLIC, 4)
    assert fuera.chunks == [] and "solo tiene 5" in (fuera.nota or "")


def test_leer_documento_ajeno_e_inexistente_son_indistinguibles(retriever_con_docs) -> None:
    tool = LeerDocumento(retriever_con_docs)
    ajeno = tool.ejecutar(LeerDocumentoArgs(doc_id="rrhh/bandas-salariales.md"), PUBLIC, 4)
    inexistente = tool.ejecutar(LeerDocumentoArgs(doc_id="rrhh/no-existe.md"), PUBLIC, 4)
    assert ajeno == inexistente
    assert ajeno.chunks == [] and ajeno.nota == SIN_ACCESO


def test_leer_documento_propio(retriever_con_docs) -> None:
    r = LeerDocumento(retriever_con_docs).ejecutar(
        LeerDocumentoArgs(doc_id="rrhh/bandas-salariales.md"), RRHH, 4
    )
    assert r.chunks and "58.000" in r.chunks[0].chunk.contenido


# ------------------------------------------------------------- buscar_en_documento
def test_buscar_en_documento_solo_devuelve_ese_documento(retriever_con_docs, embedder) -> None:
    tool = BuscarEnDocumento(embedder, retriever_con_docs)
    args = BuscarEnDocumentoArgs(doc_id="public/teletrabajo.md", consulta="vacaciones")
    r = tool.ejecutar(args, PUBLIC, 10)
    assert r.chunks and {c.chunk.doc_id for c in r.chunks} == {"public/teletrabajo.md"}


def test_buscar_en_documento_ajeno(retriever_con_docs, embedder) -> None:
    tool = BuscarEnDocumento(embedder, retriever_con_docs)
    args = BuscarEnDocumentoArgs(doc_id="rrhh/bandas-salariales.md", consulta="banda B3")
    r = tool.ejecutar(args, PUBLIC, 10)
    assert r.chunks == [] and r.nota == SIN_ACCESO


# ------------------------------------------------------------ argumentos y esquemas
@pytest.mark.parametrize(
    ("modelo", "payload"),
    [
        (RagRetrieveArgs, {"consulta": ""}),
        (RagRetrieveArgs, {"consulta": "x", "groups": ["rrhh"]}),
        (ListarDocumentosArgs, {"grupo": "RRHH"}),
        (ListarDocumentosArgs, {"groups": ["rrhh"]}),
        (LeerDocumentoArgs, {"doc_id": "public/../rrhh/x.md"}),
        (LeerDocumentoArgs, {"doc_id": "/etc/passwd"}),
        (LeerDocumentoArgs, {"doc_id": "public/x.md", "cantidad": 50}),
        (LeerDocumentoArgs, {"doc_id": "public/x.md", "desde": -1}),
        (BuscarEnDocumentoArgs, {"doc_id": "public/x.md"}),
        (BuscarEnDocumentoArgs, {"doc_id": "public/x.md", "consulta": "q", "usuario": "admin"}),
    ],
)
def test_argumentos_invalidos(modelo, payload) -> None:
    with pytest.raises(ValidationError):
        modelo.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("tool", TODAS, ids=lambda t: t.nombre)
def test_ningun_esquema_expone_grupos_ni_usuario(tool) -> None:
    params = schema_openai(tool)["function"]["parameters"]
    assert params["additionalProperties"] is False
    assert not {"groups", "grupos", "usuario", "user", "acl_groups"} & set(params["properties"])
