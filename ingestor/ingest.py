"""Pipeline de ingesta: origen → validación → chunk → embed → index.

Uso:
    python -m ingestor.ingest --source local --path ingestor/sample_docs
    python -m ingestor.ingest --source blob          # Azure Storage (Managed Identity)

Termina con código 1 si algún documento se rechaza (los aceptados sí se indexan).
"""

import argparse
import logging
import sys
from pathlib import Path

from pydantic import BaseModel

from app.config import get_settings
from app.deps import build_embedder, build_retriever
from app.retrieval.base import Embedder, Retriever
from ingestor.chunking import chunk_document
from ingestor.sources import BlobSource, LocalFolderSource, Source
from ingestor.validacion import DocumentoValidado, validar_documento

logger = logging.getLogger("ingestor")
BATCH = 16


class InformeIngesta(BaseModel):
    documentos: list[DocumentoValidado]
    chunks: int

    @property
    def rechazados(self) -> list[DocumentoValidado]:
        return [d for d in self.documentos if not d.aceptado]


def ingestar(source: Source, embedder: Embedder, retriever: Retriever) -> InformeIngesta:
    retriever.ensure_index()
    resultados: list[DocumentoValidado] = []
    total = 0
    for crudo in source.documentos():
        doc = (
            DocumentoValidado(doc_id=crudo.doc_id, aceptado=False, motivos=[crudo.motivo_descarte])
            if crudo.motivo_descarte
            else validar_documento(crudo.doc_id, crudo.datos)
        )
        resultados.append(doc.model_copy(update={"texto": None}))  # el informe no lleva texto
        if not doc.aceptado or doc.texto is None:
            logger.warning("RECHAZADO %s: %s", doc.doc_id, "; ".join(doc.motivos))
            continue
        chunks = chunk_document(doc.doc_id, doc.texto, doc.acl_groups)
        for i in range(0, len(chunks), BATCH):
            lote = chunks[i : i + BATCH]
            retriever.upsert(lote, embedder.embed([c.contenido for c in lote]))
        for aviso in doc.avisos:
            logger.info("%s: %s", doc.doc_id, aviso)
        logger.info("%s → %d chunks (acl=%s)", doc.doc_id, len(chunks), doc.acl_groups)
        total += len(chunks)
    return InformeIngesta(documentos=resultados, chunks=total)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=["local", "blob"], default="local")
    parser.add_argument("--path", type=Path, default=Path("ingestor/sample_docs"))
    args = parser.parse_args()

    settings = get_settings()
    source: Source = (
        LocalFolderSource(args.path)
        if args.source == "local"
        else BlobSource(settings.azure_storage_account_url, settings.azure_storage_container)
    )
    informe = ingestar(source, build_embedder(settings), build_retriever(settings))
    logger.info(
        "Ingesta completada: %d chunks, %d documentos aceptados, %d rechazados",
        informe.chunks,
        len(informe.documentos) - len(informe.rechazados),
        len(informe.rechazados),
    )
    if informe.rechazados:
        sys.exit(1)


if __name__ == "__main__":
    main()
