"""CLI de ingesta: origen → validación → chunk → embed → index (vía GestorDocumentos).

Uso:
    python -m ingestor.ingest --source local --path ingestor/sample_docs
    python -m ingestor.ingest --source local --borrar-huerfanos  # borra lo que ya no está
    python -m ingestor.ingest --source blob                      # Azure Storage (MI)

Termina con código 1 si algún documento se rechaza (los aceptados sí se indexan).
"""

import argparse
import logging
import sys
from pathlib import Path

from app.config import get_settings
from app.deps import build_embedder, build_retriever
from app.retrieval.base import Embedder, Retriever
from ingestor.gestor import GestorDocumentos, InformeIngesta
from ingestor.sources import BlobSource, LocalFolderSource, Source

logger = logging.getLogger("ingestor")


def ingestar(
    source: Source, embedder: Embedder, retriever: Retriever, borrar_huerfanos: bool = False
) -> InformeIngesta:
    return GestorDocumentos(embedder, retriever).sincronizar(source, borrar_huerfanos)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=["local", "blob"], default="local")
    parser.add_argument("--path", type=Path, default=Path("ingestor/sample_docs"))
    parser.add_argument(
        "--borrar-huerfanos",
        action="store_true",
        help="Elimina del índice los documentos de los grupos del origen que ya no existen en él",
    )
    args = parser.parse_args()

    settings = get_settings()
    source: Source = (
        LocalFolderSource(args.path)
        if args.source == "local"
        else BlobSource(settings.azure_storage_account_url, settings.azure_storage_container)
    )
    informe = ingestar(
        source, build_embedder(settings), build_retriever(settings), args.borrar_huerfanos
    )
    logger.info("Ingesta completada: %d chunks, %s", informe.chunks, informe.por_estado())
    if informe.rechazados:
        sys.exit(1)


if __name__ == "__main__":
    main()
