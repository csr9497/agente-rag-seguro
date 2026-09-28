"""Almacén de originales de los documentos (fuente de verdad para reindexar).

Cada original se guarda con la referencia de sus roles:
- local: `data/documentos/<doc_id>` + `<doc_id>.roles.json` al lado.
- blob (Azure): blob `<doc_id>` con metadatos `roles=<r1,r2>` y `doc_hash`.
"""

import json
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel


class OriginalGuardado(BaseModel):
    doc_id: str
    roles: list[str]
    doc_hash: str
    ubicacion: str


class AlmacenDocumentos(Protocol):
    def guardar(
        self, doc_id: str, datos: bytes, roles: list[str], doc_hash: str
    ) -> OriginalGuardado: ...

    def leer_roles(self, doc_id: str) -> list[str] | None: ...

    def eliminar(self, doc_id: str) -> None: ...


class AlmacenLocal:
    def __init__(self, raiz: Path) -> None:
        self._raiz = raiz.resolve()

    def _ruta(self, doc_id: str) -> Path:
        ruta = (self._raiz / doc_id).resolve()
        if not ruta.is_relative_to(self._raiz):  # doc_id ya validado; defensa en profundidad
            raise ValueError(f"Ruta fuera del almacén: {doc_id}")
        return ruta

    def guardar(
        self, doc_id: str, datos: bytes, roles: list[str], doc_hash: str
    ) -> OriginalGuardado:
        ruta = self._ruta(doc_id)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(datos)
        ruta.with_name(ruta.name + ".roles.json").write_text(
            json.dumps({"roles": roles, "doc_hash": doc_hash}), encoding="utf-8"
        )
        return OriginalGuardado(doc_id=doc_id, roles=roles, doc_hash=doc_hash, ubicacion=str(ruta))

    def leer_roles(self, doc_id: str) -> list[str] | None:
        meta = self._ruta(doc_id).with_name(Path(doc_id).name + ".roles.json")
        if not meta.exists():
            return None
        return json.loads(meta.read_text(encoding="utf-8"))["roles"]

    def eliminar(self, doc_id: str) -> None:
        ruta = self._ruta(doc_id)
        ruta.unlink(missing_ok=True)
        ruta.with_name(ruta.name + ".roles.json").unlink(missing_ok=True)


class AlmacenBlob:
    def __init__(self, account_url: str, container: str) -> None:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import ContainerClient

        self._container = ContainerClient(account_url, container, DefaultAzureCredential())

    def guardar(
        self, doc_id: str, datos: bytes, roles: list[str], doc_hash: str
    ) -> OriginalGuardado:
        blob = self._container.get_blob_client(doc_id)
        blob.upload_blob(
            datos, overwrite=True, metadata={"roles": ",".join(roles), "doc_hash": doc_hash}
        )
        return OriginalGuardado(doc_id=doc_id, roles=roles, doc_hash=doc_hash, ubicacion=blob.url)

    def leer_roles(self, doc_id: str) -> list[str] | None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            meta = self._container.get_blob_client(doc_id).get_blob_properties().metadata
        except ResourceNotFoundError:
            return None
        return [r for r in meta.get("roles", "").split(",") if r] or None

    def eliminar(self, doc_id: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            self._container.delete_blob(doc_id)
        except ResourceNotFoundError:
            pass
