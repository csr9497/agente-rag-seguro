"""Orígenes de documentos.

Convención de ACL: el primer nivel de carpeta (o prefijo del blob) es el grupo que puede
ver el documento, p. ej. `rrhh/salarios.md` → acl_groups=["rrhh"].
"""

from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple, Protocol

EXTENSIONES = {".md", ".txt"}


class Documento(NamedTuple):
    doc_id: str
    texto: str
    acl_groups: list[str]


class Source(Protocol):
    def documentos(self) -> Iterator[Documento]: ...


def _acl_desde_ruta(ruta_relativa: str) -> list[str]:
    partes = ruta_relativa.split("/")
    if len(partes) < 2:
        raise ValueError(f"'{ruta_relativa}' debe estar dentro de una carpeta de grupo")
    return [partes[0]]


class LocalFolderSource:
    def __init__(self, raiz: Path) -> None:
        self._raiz = raiz

    def documentos(self) -> Iterator[Documento]:
        for ruta in sorted(self._raiz.rglob("*")):
            if ruta.is_file() and ruta.suffix in EXTENSIONES:
                rel = ruta.relative_to(self._raiz).as_posix()
                yield Documento(rel, ruta.read_text(encoding="utf-8"), _acl_desde_ruta(rel))


class BlobSource:
    def __init__(self, account_url: str, container: str) -> None:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import ContainerClient

        self._container = ContainerClient(account_url, container, DefaultAzureCredential())

    def documentos(self) -> Iterator[Documento]:
        for blob in self._container.list_blobs():
            if Path(blob.name).suffix not in EXTENSIONES:
                continue
            texto = self._container.download_blob(blob.name).readall().decode("utf-8")
            yield Documento(blob.name, texto, _acl_desde_ruta(blob.name))
