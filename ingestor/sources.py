"""Orígenes de documentos. Solo leen bytes; la validación y la ACL las decide
`ingestor.validacion` (convención: `<grupo>/<documento>.md` → acl_groups=["<grupo>"])."""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple, Protocol

from ingestor.validacion import MAX_BYTES


class DocumentoCrudo(NamedTuple):
    doc_id: str
    datos: bytes
    motivo_descarte: str | None = None  # el origen ya sabe que no es válido (p. ej. symlink)
    roles: list[str] | None = None  # si el origen los guarda (metadatos del blob); si no, carpeta


class Source(Protocol):
    def documentos(self) -> Iterator[DocumentoCrudo]: ...


class LocalFolderSource:
    """Recorre la carpeta sin seguir enlaces simbólicos ni salir de la raíz."""

    def __init__(self, raiz: Path, max_bytes: int = MAX_BYTES) -> None:
        self._raiz = raiz.resolve()
        self._max_bytes = max_bytes

    def documentos(self) -> Iterator[DocumentoCrudo]:
        for carpeta, subcarpetas, archivos in os.walk(self._raiz, followlinks=False):
            subcarpetas.sort()
            for nombre in sorted(archivos):
                ruta = Path(carpeta) / nombre
                rel = ruta.relative_to(self._raiz).as_posix()
                if ruta.is_symlink():
                    yield DocumentoCrudo(rel, b"", "ruta: enlace simbólico no permitido")
                elif not ruta.resolve().is_relative_to(self._raiz):
                    yield DocumentoCrudo(rel, b"", "ruta: fuera de la carpeta de documentos")
                elif ruta.stat().st_size > self._max_bytes:
                    yield DocumentoCrudo(rel, b"", f"tamaño: supera {self._max_bytes} bytes")
                else:
                    yield DocumentoCrudo(rel, ruta.read_bytes())


class BlobSource:
    def __init__(self, account_url: str, container: str, max_bytes: int = MAX_BYTES) -> None:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import ContainerClient

        self._container = ContainerClient(account_url, container, DefaultAzureCredential())
        self._max_bytes = max_bytes

    def documentos(self) -> Iterator[DocumentoCrudo]:
        for blob in self._container.list_blobs(include=["metadata"]):
            roles = [r for r in (blob.metadata or {}).get("roles", "").split(",") if r] or None
            if blob.size > self._max_bytes:  # no se descarga
                yield DocumentoCrudo(blob.name, b"", f"tamaño: supera {self._max_bytes} bytes")
            else:
                datos = self._container.download_blob(blob.name).readall()
                yield DocumentoCrudo(blob.name, datos, roles=roles)
