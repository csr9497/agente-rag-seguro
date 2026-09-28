import pytest

from ingestor.chunking import chunk_document, split_text


def test_texto_corto_es_un_solo_chunk() -> None:
    assert split_text("Hola.\n\nAdiós.", max_chars=100, overlap=10) == ["Hola.\n\nAdiós."]


def test_ningun_chunk_supera_max_chars() -> None:
    texto = "\n\n".join(f"Párrafo {i} " + "x" * 180 for i in range(20))
    trozos = split_text(texto, max_chars=500, overlap=100)
    assert len(trozos) > 1
    assert all(len(t) <= 500 for t in trozos)


def test_chunks_consecutivos_se_solapan() -> None:
    texto = "\n\n".join(f"P{i}-" + "y" * 90 for i in range(10))
    trozos = split_text(texto, max_chars=300, overlap=50)
    for anterior, siguiente in zip(trozos, trozos[1:], strict=False):
        assert anterior[-20:] in siguiente


def test_parrafo_gigante_se_parte_en_ventanas() -> None:
    trozos = split_text("z" * 2500, max_chars=1000, overlap=100)
    assert len(trozos) >= 3
    assert all(len(t) <= 1000 for t in trozos)


def test_overlap_invalido() -> None:
    with pytest.raises(ValueError):
        split_text("abc", max_chars=10, overlap=10)


def test_chunk_document_propaga_acl_e_ids() -> None:
    chunks = chunk_document("rrhh/doc.md", "a\n\nb", ["rrhh"])
    assert [c.chunk_id for c in chunks] == ["rrhh/doc.md#0"]
    assert chunks[0].acl_groups == ["rrhh"]
