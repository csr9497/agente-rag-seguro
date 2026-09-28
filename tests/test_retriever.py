"""Filtro de permisos (regla 1: permisos en el dato)."""

import pytest

from app.retrieval.azure_search_retriever import build_acl_filter
from app.retrieval.qdrant_retriever import QdrantRetriever
from tests.fakes import FakeEmbedder


def _buscar(r: QdrantRetriever, e: FakeEmbedder, q: str, groups: list[str]) -> set[str]:
    [v] = e.embed([q])
    return {x.chunk.doc_id for x in r.search(q, v, groups=groups, top_k=10)}


def test_usuario_public_no_ve_documentos_de_rrhh(retriever_con_docs, embedder) -> None:
    docs = _buscar(retriever_con_docs, embedder, "bandas salariales banda senior", ["public"])
    assert docs, "debería recuperar documentos públicos"
    assert all(d.startswith("public/") for d in docs)


def test_usuario_rrhh_ve_sus_documentos(retriever_con_docs, embedder) -> None:
    docs = _buscar(retriever_con_docs, embedder, "bandas salariales", ["rrhh"])
    assert docs == {"rrhh/bandas-salariales.md"}


def test_varios_grupos_suman_visibilidad(retriever_con_docs, embedder) -> None:
    docs = _buscar(retriever_con_docs, embedder, "vacaciones salarios", ["public", "rrhh"])
    assert "rrhh/bandas-salariales.md" in docs
    assert any(d.startswith("public/") for d in docs)


@pytest.mark.parametrize("groups", [[], ["inexistente"]])
def test_sin_grupos_validos_no_devuelve_nada(retriever_con_docs, embedder, groups) -> None:
    assert _buscar(retriever_con_docs, embedder, "vacaciones", groups) == set()


def test_ensure_index_es_idempotente(retriever) -> None:
    retriever.ensure_index()
    retriever.ensure_index()


def test_filtro_odata_azure_search() -> None:
    assert (
        build_acl_filter(["public", "3f2a-9c"])
        == "acl_groups/any(g: search.in(g, 'public,3f2a-9c', ','))"
    )


@pytest.mark.parametrize("malo", ["a' or true", "a,b", "x)", ""])
def test_filtro_odata_rechaza_inyeccion(malo) -> None:
    with pytest.raises(ValueError):
        build_acl_filter([malo])


def test_filtro_odata_exige_grupos() -> None:
    with pytest.raises(ValueError):
        build_acl_filter([])
