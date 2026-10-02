"""rag_agent: responde con los documentos que el usuario puede leer, siempre con citas.

La política de lectura va DENTRO de la consulta al índice (grupos efectivos del usuario en el
filtro de Qdrant / AI Search) y cada fragmento se re-chequea contra el registro (estado,
revocado, expira_en, ACL y hash) dentro de la propia tool, antes de que el LLM lo vea. Por eso
este agente no necesita el nodo access_guardrail del grafo anterior.
"""

import json
import logging
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.agents.registry import AgentSpec, ToolPolicy
from app.agents.subgraph import ContextoTool
from app.models.schemas import Chunk, Usuario
from app.persistencia.modelos import DocumentoRegistrado, SolicitudAcceso
from app.persistencia.repositorios import RepositorioDocumentos, SqlRepositorioSolicitudesAcceso
from app.prompts import local
from app.retrieval.base import Embedder, Retriever
from app.security.acceso import VerificadorAcceso
from app.security.acl import clasificacion
from app.tools.base import SIN_ACCESO, DocId, ResultadoHerramienta

logger_seguridad = logging.getLogger("seguridad")

RAG_PROMPT = local("rag_agent")


class BuscarDocumentosArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consulta: str = Field(min_length=1, max_length=500, description="Consulta autocontenida")


class MetadatosArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: DocId


class SolicitarAccesoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    titulo: str = Field(min_length=1, max_length=200, description="Título del documento")
    motivo: str = Field(min_length=1, max_length=500, description="Para qué lo necesita")


class HerramientasRag:
    def __init__(
        self,
        embedder: Embedder,
        retriever: Retriever,
        registro: RepositorioDocumentos,
        verificador: VerificadorAcceso,
        solicitudes: SqlRepositorioSolicitudesAcceso,
        top_k: int = 4,
        min_score: float | None = None,
    ) -> None:
        self._embedder, self._retriever, self._registro = embedder, retriever, registro
        self._verificador, self._solicitudes = verificador, solicitudes
        self._top_k, self._min_score = top_k, min_score

    def search_documents(self, args: BuscarDocumentosArgs, ctx: ContextoTool) -> dict[str, Any]:
        grupos = list(ctx.user.acl)
        if not grupos:
            return {"fragmentos": []}
        [vector] = self._embedder.embed([args.consulta])
        recuperados = self._retriever.search(
            args.consulta, vector, groups=grupos, top_k=self._top_k
        )
        fragmentos = []
        for r in recuperados:
            if self._min_score is not None and r.score < self._min_score:
                continue
            if motivo := self._verificador.motivo_rechazo(r.chunk, grupos):
                _descartado(r.chunk, motivo, ctx.user.id)  # revocado, caducado, ACL, hash…
                continue
            doc = self._registro.obtener(r.chunk.doc_id)
            fragmentos.append({
                # Sin números de fragmento: el LLM citaría «[1]», que no identifica nada.
                "doc_id": r.chunk.doc_id, "cita": f"[{r.chunk.doc_id}]",
                "titulo": doc.titulo if doc else r.chunk.doc_id,
                "contenido": r.chunk.contenido, "score": round(r.score, 3),
            })  # fmt: skip
        return {"fragmentos": fragmentos}

    def search_public_internal(
        self, args: BuscarDocumentosArgs, ctx: ContextoTool
    ) -> dict[str, Any]:
        """Colecciones de RR.HH. y de TI: solo documentos públicos o internos que el usuario
        puede leer (nunca confidenciales ni restringidos, aunque su rol los vea)."""
        fragmentos = []
        for f in self.search_documents(args, ctx)["fragmentos"]:
            doc = self._registro.obtener(f["doc_id"])
            if doc and clasificacion(doc.roles) in ("publico", "interno"):
                fragmentos.append(f)
        return {"fragmentos": fragmentos}

    def get_document_metadata(self, args: MetadatosArgs, ctx: ContextoTool) -> dict[str, Any]:
        doc = self._registro.obtener(args.doc_id)
        if doc is None or not self._visible(doc, list(ctx.user.acl)):
            return {"error": SIN_ACCESO}  # igual que inexistente: no se revela que existe
        return {
            "doc_id": doc.doc_id, "titulo": doc.titulo, "dueno": doc.subido_por,
            "clasificacion": clasificacion(doc.roles), "version": doc.doc_hash[:12],
            "indexado_en": doc.indexado_en,
        }  # fmt: skip

    def request_document_access(
        self, args: SolicitarAccesoArgs, ctx: ContextoTool
    ) -> dict[str, Any]:
        """Respuesta idéntica exista o no el documento; el dueño la verá si existe."""
        objetivo = args.titulo.strip().casefold()
        doc = next((d for d in self._registro.listar() if d.titulo.casefold() == objetivo), None)
        solicitud = self._solicitudes.crear(
            SolicitudAcceso(
                id="", solicitante_id=ctx.user.id, titulo=args.titulo, motivo=args.motivo,
                doc_id=doc.doc_id if doc else None, propietario_id=doc.subido_por if doc else None,
                trace_id=ctx.trace_id,
            )
        )  # fmt: skip
        return {"id": solicitud.id, "estado": "registrada"}

    def _visible(self, doc: DocumentoRegistrado, grupos: list[str]) -> bool:
        sintetico = Chunk(
            chunk_id=f"{doc.doc_id}#metadatos", doc_id=doc.doc_id, fuente=doc.doc_id,
            contenido="-", acl_groups=doc.roles, doc_hash=doc.doc_hash,
        )  # fmt: skip
        return self._verificador.motivo_rechazo(sintetico, grupos) is None


class HerramientaDeLectura(Protocol):
    """Las tools de solo lectura del grafo anterior (data_query, listar/leer/buscar en
    documentos)."""

    nombre: str
    descripcion: str
    args_model: type[BaseModel]

    def ejecutar(self, args: Any, usuario: Usuario, top_k: int) -> ResultadoHerramienta: ...


def adaptar_lectura(
    herramienta: HerramientaDeLectura,
    verificador: VerificadorAcceso,
    registro: RepositorioDocumentos,
    top_k: int = 4,
) -> ToolPolicy:
    """Una tool de lectura del grafo anterior como tool de rag_agent: actúa con los grupos
    efectivos del usuario del token y cada fragmento pasa el re-chequeo contra el registro
    (lo que antes hacía access_guardrail)."""

    def ejecutar(args: Any, ctx: ContextoTool) -> dict[str, Any]:
        usuario = Usuario(id=ctx.user.id, groups=list(ctx.user.acl))
        resultado = herramienta.ejecutar(args, usuario, top_k)
        fragmentos = []
        for r in resultado.chunks:
            if motivo := verificador.motivo_rechazo(r.chunk, usuario.groups):
                _descartado(r.chunk, motivo, ctx.user.id)
                continue
            doc = _titulo_registrado(registro, r.chunk.doc_id)
            fragmentos.append({
                "doc_id": r.chunk.doc_id, "cita": f"[{r.chunk.doc_id}]", "fuente": r.chunk.fuente,
                "titulo": doc or r.chunk.fuente, "contenido": r.chunk.contenido,
                "score": round(r.score, 3),
            })  # fmt: skip
        salida: dict[str, Any] = {"fragmentos": fragmentos}
        if resultado.nota:
            salida["nota"] = resultado.nota
        return salida

    return ToolPolicy(
        ejecutar, herramienta.args_model, scope="docs:read", mode="auto",
        description=herramienta.descripcion,
    )  # fmt: skip


def _descartado(chunk: Chunk, motivo: str, usuario_id: str) -> None:
    """Señal de seguridad: el índice devolvió algo que el registro no autoriza (manipulación,
    desincronización, revocación…). Se registra sin el contenido del fragmento."""
    logger_seguridad.warning(json.dumps({
        "accion": "fragmento_descartado", "usuario": usuario_id, "doc_id": chunk.doc_id,
        "chunk_id": chunk.chunk_id, "motivo": motivo,
    }, ensure_ascii=False))  # fmt: skip


def _titulo_registrado(registro: RepositorioDocumentos, doc_id: str) -> str | None:
    doc = registro.obtener(doc_id) if "/" in doc_id else None
    return doc.titulo if doc else None


def crear_rag_agent(h: HerramientasRag, lectura: dict[str, ToolPolicy] | None = None) -> AgentSpec:
    return AgentSpec(
        name="rag_agent",
        description=(
            "Preguntas sobre el contenido de los documentos de la empresa que el usuario puede "
            "leer (políticas, procedimientos, guías). Responde siempre con citas."
        ),
        system_prompt=RAG_PROMPT,
        consultar_antes=True,
        tools={
            "search_documents": ToolPolicy(
                h.search_documents, BuscarDocumentosArgs, scope="docs:read", mode="auto",
                description="Búsqueda semántica en los documentos que el usuario puede leer.",
            ),
            "get_document_metadata": ToolPolicy(
                h.get_document_metadata, MetadatosArgs, scope="docs:read", mode="auto",
                description="Título, dueño, clasificación y versión de un documento.",
            ),
            "request_document_access": ToolPolicy(
                h.request_document_access, SolicitarAccesoArgs, scope="docs:request",
                mode="confirm_user", writes=True,
                description="Pide acceso a un documento que el usuario no puede leer.",
            ),
            # data_query, listar_documentos, leer_documento, buscar_en_documento.
            **(lectura or {}),
        },
    )  # fmt: skip
