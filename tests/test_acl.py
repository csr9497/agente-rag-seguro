"""ACL de documentos codificada en acl_groups: roles (confidencial), «dept:<d>» (interno) y
«user:<id>» (restringido). Las entradas acaban dentro de filtros de Qdrant y de AI Search
(OData): solo se aceptan formatos seguros."""

import pytest

from app.security.acl import (
    clasificacion,
    entrada_valida,
    es_rol,
    grupos_efectivos,
)


@pytest.mark.parametrize(
    "entrada",
    ["public", "rrhh", "dept:it", "dept:recursos-humanos", "user:github:ana", "user:3f2a-b1"],
)
def test_entradas_validas(entrada) -> None:
    assert entrada_valida(entrada)


@pytest.mark.parametrize(
    "entrada",
    [
        "", "Public", "dept:", "dept:IT", "user:", "user:o'brien", "user:a,b", "user:a b",
        "grupo:x", "dept:it,rrhh", "public') or true or ('",
    ],
)  # fmt: skip
def test_entradas_peligrosas_o_mal_formadas(entrada) -> None:
    assert not entrada_valida(entrada)


def test_grupos_efectivos_suman_departamento_y_usuario() -> None:
    assert grupos_efectivos("github:ana", ["public"], ["it"]) == [
        "public", "dept:it", "user:github:ana",
    ]  # fmt: skip


def test_grupos_efectivos_descartan_lo_que_no_valida() -> None:
    """Un id de usuario con comillas o comas no entra en ningún filtro (solo sus roles)."""
    assert grupos_efectivos("o'brien", ["public"], ["IT", "it"]) == ["public", "dept:it"]


def test_es_rol() -> None:
    assert es_rol("rrhh") and not es_rol("dept:it") and not es_rol("user:ana")


@pytest.mark.parametrize(
    ("acl", "esperada"),
    [
        (["public"], "publico"),
        (["public", "dept:it"], "publico"),
        (["dept:it"], "interno"),
        (["rrhh"], "confidencial"),
        (["rrhh", "dept:it"], "confidencial"),
        (["user:github:ana"], "restringido"),
        (["rrhh", "user:github:ana"], "restringido"),
    ],
)
def test_clasificacion(acl, esperada) -> None:
    assert clasificacion(acl) == esperada


def test_el_filtro_de_ai_search_admite_la_acl_nueva_y_rechaza_inyecciones() -> None:
    from app.retrieval.azure_search_retriever import build_acl_filter

    assert build_acl_filter(["public", "dept:it", "user:github:ana"]) == (
        "acl_groups/any(g: search.in(g, 'public,dept:it,user:github:ana', ','))"
    )
    for malicioso in ["public') or true or ('", "user:a,b", "user:o'brien"]:
        with pytest.raises(ValueError):
            build_acl_filter(["public", malicioso])
