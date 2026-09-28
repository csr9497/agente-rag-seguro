"""Pipeline de ingesta: origen → chunk → embed → index.

Uso:
    python -m ingestor.ingest --source local --path ingestor/sample_docs
    python -m ingestor.ingest --source blob          # Azure Storage (Managed Identity)
"""

import argparse
import logging
from pathlib import Path

from app.config import get_settings
from app.deps import build_embedder, build_retriever
from app.retrieval.base import Embedder, Retriever
from ingestor.chunking import chunk_document
from ingestor.sources import BlobSource, LocalFolderSource, Source

logger = logging.getLogger("ingestor")
BATCH = 16


def ingestar(source: Source, embedder: Embedder, retriever: Retriever) -> int:
    retriever.ensure_index()
    total = 0
    for doc in source.documentos():
        chunks = chunk_document(doc.doc_id, doc.texto, doc.acl_groups)
        for i in range(0, len(chunks), BATCH):
            lote = chunks[i : i + BATCH]
            retriever.upsert(lote, embedder.embed([c.contenido for c in lote]))
        logger.info("%s → %d chunks (acl=%s)", doc.doc_id, len(chunks), doc.acl_groups)
        total += len(chunks)
    return total


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
    total = ingestar(source, build_embedder(settings), build_retriever(settings))
    logger.info("Ingesta completada: %d chunks", total)


if __name__ == "__main__":
    main()
