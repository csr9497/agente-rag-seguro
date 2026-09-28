"""Gestión del ciclo de vida de los documentos del RAG.

Lo usan el CLI de ingesta y la API (/documentos):
- indexar: valida → hash → sin cambios / duplicado / nuevo / actualizado. Al actualizar se
  borran antes los chunks antiguos (no quedan chunks obsoletos si el documento encoge).
- eliminar: borra todos los chunks del documento.
- sincronizar: indexa un origen completo y, opcionalmente, borra los documentos del índice
  que ya no existen en él (huérfanos).
"""

import hashlib
import logging
from datetime import UTC, datetime

from pydantic import BaseModel

from app.models.schemas import DocumentoIndexado, ResultadoOperacion
from app.retrieval.base import Embedder, Retriever
from ingestor.chunking import chunk_document
from ingestor.sources import Source
from ingestor.validacion import validar_documento

logger = logging.getLogger("ingestor")
BATCH = 16


class InformeIngesta(BaseModel):
    operaciones: list[ResultadoOperacion]

    @property
    def chunks(self) -> int:
        return sum(o.chunks for o in self.operaciones if o.estado in {"indexado", "actualizado"})

    @property
    def rechazados(self) -> list[ResultadoOperacion]:
        return [o for o in self.operaciones if o.estado == "rechazado"]

    def por_estado(self) -> dict[str, int]:
        conteo: dict[str, int] = {}
        for o in self.operaciones:
            conteo[o.estado] = conteo.get(o.estado, 0) + 1
        return conteo


def hash_texto(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


class GestorDocumentos:
    def __init__(self, embedder: Embedder, retriever: Retriever) -> None:
        self._embedder = embedder
        self._retriever = retriever
        self._indice_listo = False

    def _asegurar_indice(self) -> None:
        if not self._indice_listo:
            self._retriever.ensure_index()
            self._indice_listo = True

    def indexar(self, doc_id: str, datos: bytes, forzar: bool = False) -> ResultadoOperacion:
        validado = validar_documento(doc_id, datos)
        if not validado.aceptado or validado.texto is None:
            return ResultadoOperacion(doc_id=doc_id, estado="rechazado", motivos=validado.motivos)

        self._asegurar_indice()
        doc_hash = hash_texto(validado.texto)
        previo = self._retriever.document_hash(doc_id)
        if previo == doc_hash and not forzar:
            return ResultadoOperacion(doc_id=doc_id, estado="sin_cambios", avisos=validado.avisos)

        grupo = validado.acl_groups[0]
        otros = [d for d in self._retriever.find_by_hash(doc_hash, grupo) if d != doc_id]
        if otros and not forzar:
            return ResultadoOperacion(
                doc_id=doc_id,
                estado="duplicado",
                duplicado_de=otros[0],
                motivos=[f"mismo contenido que {otros[0]} en el grupo {grupo!r}"],
            )

        chunks = chunk_document(
            doc_id,
            validado.texto,
            validado.acl_groups,
            doc_hash=doc_hash,
            indexado_en=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        # Embeddings antes de borrar: si el proveedor falla, el documento anterior sigue intacto.
        vectores: list[list[float]] = []
        for i in range(0, len(chunks), BATCH):
            vectores += self._embedder.embed([c.contenido for c in chunks[i : i + BATCH]])
        if previo is not None:
            self._retriever.delete_document(doc_id)
        for i in range(0, len(chunks), BATCH):
            self._retriever.upsert(chunks[i : i + BATCH], vectores[i : i + BATCH])

        return ResultadoOperacion(
            doc_id=doc_id,
            estado="actualizado" if previo is not None else "indexado",
            chunks=len(chunks),
            avisos=validado.avisos,
        )

    def eliminar(self, doc_id: str) -> ResultadoOperacion:
        borrados = self._retriever.delete_document(doc_id)
        return ResultadoOperacion(
            doc_id=doc_id, estado="eliminado" if borrados else "no_encontrado", chunks=borrados
        )

    def listar(self, groups: list[str]) -> list[DocumentoIndexado]:
        return self._retriever.list_documents(groups)

    def sincronizar(
        self, source: Source, borrar_huerfanos: bool = False, grupos_origen: list[str] | None = None
    ) -> InformeIngesta:
        """Indexa todo el origen. Con `borrar_huerfanos`, elimina del índice los documentos
        de `grupos_origen` que ya no están en el origen."""
        operaciones: list[ResultadoOperacion] = []
        vistos: set[str] = set()
        for crudo in source.documentos():
            vistos.add(crudo.doc_id)
            if crudo.motivo_descarte:
                r = ResultadoOperacion(
                    doc_id=crudo.doc_id, estado="rechazado", motivos=[crudo.motivo_descarte]
                )
            else:
                r = self.indexar(crudo.doc_id, crudo.datos)
            _log(r)
            operaciones.append(r)

        if borrar_huerfanos:
            # Por defecto, los grupos presentes en el origen (carpetas de primer nivel).
            grupos = grupos_origen or sorted({d.split("/", 1)[0] for d in vistos if "/" in d})
            for doc in self.listar(grupos):
                if doc.doc_id not in vistos:
                    r = self.eliminar(doc.doc_id)
                    _log(r)
                    operaciones.append(r)
        return InformeIngesta(operaciones=operaciones)


def _log(r: ResultadoOperacion) -> None:
    if r.estado in {"rechazado", "duplicado"}:
        logger.warning("%s %s: %s", r.estado.upper(), r.doc_id, "; ".join(r.motivos))
    else:
        logger.info("%s %s (%d chunks)", r.estado, r.doc_id, r.chunks)
    for aviso in r.avisos:
        logger.info("%s: %s", r.doc_id, aviso)
