"""Chunking por párrafos con ventana de solape (en caracteres)."""

from app.models.schemas import Chunk


def split_text(texto: str, max_chars: int = 1000, overlap: int = 150) -> list[str]:
    if overlap >= max_chars:
        raise ValueError("overlap debe ser menor que max_chars")
    parrafos = [p.strip() for p in texto.split("\n\n") if p.strip()]
    trozos: list[str] = []
    actual = ""
    for parrafo in parrafos:
        # Párrafos más largos que la ventana se cortan en ventanas con solape.
        piezas = (
            [parrafo]
            if len(parrafo) <= max_chars
            else [parrafo[i : i + max_chars] for i in range(0, len(parrafo), max_chars - overlap)]
        )
        for pieza in piezas:
            if actual and len(actual) + 2 + len(pieza) > max_chars:
                trozos.append(actual)
                # Solape con la cola del chunk anterior, sin exceder max_chars.
                cola = min(overlap, max_chars - len(pieza) - 2)
                actual = f"{actual[len(actual) - cola :]}\n\n{pieza}" if cola > 0 else pieza
            else:
                actual = f"{actual}\n\n{pieza}" if actual else pieza
    if actual:
        trozos.append(actual)
    return trozos


def chunk_document(
    doc_id: str,
    texto: str,
    acl_groups: list[str],
    max_chars: int = 1000,
    overlap: int = 150,
) -> list[Chunk]:
    return [
        Chunk(
            chunk_id=f"{doc_id}#{i}",
            doc_id=doc_id,
            fuente=doc_id,
            contenido=trozo,
            acl_groups=acl_groups,
        )
        for i, trozo in enumerate(split_text(texto, max_chars, overlap))
    ]
