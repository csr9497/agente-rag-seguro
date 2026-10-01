"""rag_agent: responde con los documentos que el usuario puede leer, siempre con citas.

La política de lectura va DENTRO de la consulta al índice (grupos efectivos del usuario en el
filtro de Qdrant / AI Search) y cada fragmento se re-chequea contra el registro (estado,
revocado, expira_en, ACL y hash) dentro de la propia tool, antes de que el LLM lo vea. Por eso
este agente no necesita el nodo access_guardrail del grafo anterior.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agents.registry import AgentSpec, ToolPolicy
from app.agents.subgraph import ContextoTool
from app.models.schemas import Chunk
from app.persistencia.modelos import DocumentoRegistrado, SolicitudAcceso
from app.persistencia.repositorios import RepositorioDocumentos, SqlRepositorioSolicitudesAcceso
from app.prompts import local
from app.retrieval.base import Embedder, Retriever
from app.security.acceso import VerificadorAcceso
from app.security.acl import clasificacion
from app.tools.base import SIN_ACCESO, DocId

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
            if self._verificador.motivo_rechazo(r.chunk, grupos) is not None:
                continue  # re-chequeo: revocado, caducado, ACL desincronizada, hash…
            doc = self._registro.obtener(r.chunk.doc_id)
            fragmentos.append({
                "n": len(fragmentos) + 1, "doc_id": r.chunk.doc_id,
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
                fragmentos.append({**f, "n": len(fragmentos) + 1})
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


def crear_rag_agent(h: HerramientasRag) -> AgentSpec:
    return AgentSpec(
        name="rag_agent",
        description=(
            "Preguntas sobre el contenido de los documentos de la empresa que el usuario puede "
            "leer (políticas, procedimientos, guías). Responde siempre con citas."
        ),
        system_prompt=RAG_PROMPT,
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
        },
    )  # fmt: skip
