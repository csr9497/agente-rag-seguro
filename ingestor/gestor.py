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
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel

from app.models.schemas import DocumentoIndexado, ResultadoOperacion
from app.persistencia.almacen import AlmacenDocumentos
from app.persistencia.modelos import DocumentoRegistrado
from app.persistencia.repositorios import RepositorioDocumentos, RepositorioRoles
from app.retrieval.base import Embedder, Retriever
from app.security.content_safety import (
    ClienteShields,
    ContentSafetyError,
    detectar_ataque_en_documento,
)
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


def titulo_de(texto: str, doc_id: str) -> str:
    """Primer encabezado Markdown o, si no hay, el nombre del archivo."""
    for linea in texto.splitlines():
        if linea.startswith("# ") and linea[2:].strip():
            return linea[2:].strip()[:200]
    return PurePosixPath(doc_id).stem.replace("-", " ").replace("_", " ")[:200]


def hash_texto(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


class GestorDocumentos:
    """Con `registro` y `roles` (la app y el CLI) valida que los roles existen y están activos
    y registra cada documento: el registro es la fuente de verdad de sus permisos."""

    def __init__(
        self,
        embedder: Embedder,
        retriever: Retriever,
        registro: RepositorioDocumentos | None = None,
        roles: RepositorioRoles | None = None,
        almacen: AlmacenDocumentos | None = None,
        shields: ClienteShields | None = None,
        shields_fallo: Literal["cerrado", "abierto"] = "cerrado",
    ) -> None:
        self._embedder = embedder
        self._retriever = retriever
        self._registro = registro
        self._roles = roles
        self._almacen = almacen
        self._shields = shields
        self._shields_fallo = shields_fallo
        self._indice_listo = False

    def _revisar_shields(self, texto: str) -> tuple[str | None, str | None]:
        """Inyección indirecta con Prompt Shields, además de las heurísticas locales ya
        aplicadas. Devuelve (motivo de rechazo, aviso)."""
        if self._shields is None:
            return None, None
        try:
            if detectar_ataque_en_documento(self._shields, texto):
                return "inyección de prompt: prompt_shields", None
        except ContentSafetyError as e:
            if self._shields_fallo == "cerrado":
                return f"no se pudo verificar con Prompt Shields ({e})", None
            return None, f"sin verificar con Prompt Shields ({e})"
        return None, None

    def _asegurar_indice(self) -> None:
        if not self._indice_listo:
            self._retriever.ensure_index()
            self._indice_listo = True

    def indexar(
        self,
        doc_id: str,
        datos: bytes,
        forzar: bool = False,
        roles: list[str] | None = None,
        subido_por: str | None = None,
    ) -> ResultadoOperacion:
        validado = validar_documento(doc_id, datos, roles=roles)
        if not validado.aceptado or validado.texto is None:
            return ResultadoOperacion(doc_id=doc_id, estado="rechazado", motivos=validado.motivos)
        acl = validado.acl_groups
        if motivo := self._roles_no_validos(acl):
            return ResultadoOperacion(doc_id=doc_id, estado="rechazado", motivos=[motivo])
        motivo, aviso = self._revisar_shields(validado.texto)
        if motivo:
            return ResultadoOperacion(doc_id=doc_id, estado="rechazado", motivos=[motivo])
        if aviso:
            validado.avisos.append(aviso)

        self._asegurar_indice()
        doc_hash = hash_texto(validado.texto)
        previo = self._retriever.document_hash(doc_id)
        registrado = self._registro.obtener(doc_id) if self._registro else None
        mismos_roles = registrado is None or registrado.roles == acl
        if previo == doc_hash and mismos_roles and not forzar:
            return ResultadoOperacion(doc_id=doc_id, estado="sin_cambios", avisos=validado.avisos)

        for rol in acl:
            otros = [d for d in self._retriever.find_by_hash(doc_hash, rol) if d != doc_id]
            if otros and not forzar:
                return ResultadoOperacion(
                    doc_id=doc_id,
                    estado="duplicado",
                    duplicado_de=otros[0],
                    motivos=[f"mismo contenido que {otros[0]} para el rol {rol!r}"],
                )

        chunks = chunk_document(
            doc_id,
            validado.texto,
            validado.acl_groups,
            doc_hash=doc_hash,
            indexado_en=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        # Embeddings antes de tocar nada: si el proveedor falla, no cambia nada.
        vectores: list[list[float]] = []
        for i in range(0, len(chunks), BATCH):
            vectores += self._embedder.embed([c.contenido for c in chunks[i : i + BATCH]])
        # Orden: original (con roles) → índice → registro. El original es la fuente de verdad
        # para reindexar; si fallara el registro, access_guardrail descarta los chunks.
        if self._almacen:
            self._almacen.guardar(doc_id, datos, acl, doc_hash)
        if previo is not None:
            self._retriever.delete_document(doc_id)
        for i in range(0, len(chunks), BATCH):
            self._retriever.upsert(chunks[i : i + BATCH], vectores[i : i + BATCH])
        # Registro después del índice: si fallara, access_guardrail descarta los chunks
        # (no registrados) hasta que un reintento lo complete.
        if self._registro:
            self._registro.registrar(
                DocumentoRegistrado(
                    doc_id=doc_id,
                    titulo=titulo_de(validado.texto, doc_id),
                    roles=acl,
                    doc_hash=doc_hash,
                    chunks=len(chunks),
                    subido_por=subido_por,
                    indexado_en=chunks[0].indexado_en if chunks else "",
                )
            )

        return ResultadoOperacion(
            doc_id=doc_id,
            estado="actualizado" if previo is not None else "indexado",
            chunks=len(chunks),
            avisos=validado.avisos,
        )

    def _roles_no_validos(self, acl: list[str]) -> str | None:
        if self._roles is None:
            return None
        activos = {r.id for r in self._roles.listar(incluir_inactivos=False)}
        if faltan := [r for r in acl if r not in activos]:
            return f"roles: no existen o están inactivos {faltan}"
        return None

    def eliminar(self, doc_id: str) -> ResultadoOperacion:
        if self._registro:
            self._registro.eliminar(doc_id)
        if self._almacen:
            self._almacen.eliminar(doc_id)
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
                r = self.indexar(crudo.doc_id, crudo.datos, roles=crudo.roles)
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
