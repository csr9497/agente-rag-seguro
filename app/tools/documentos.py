"""Tools de navegación por documentos. Todas filtran por los grupos del usuario del estado."""

from pydantic import BaseModel, ConfigDict, Field

from app.models.schemas import Chunk, ChunkRecuperado, Usuario
from app.retrieval.base import Embedder, Retriever
from app.tools.base import PATRON_GRUPO, SIN_ACCESO, DocId, ResultadoHerramienta

MAX_CATALOGO = 50


# ------------------------------------------------------------------ listar_documentos
class ListarDocumentosArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grupo: str | None = Field(
        default=None,
        pattern=PATRON_GRUPO,
        description="Restringe a un grupo del usuario (no amplía el acceso)",
    )


class ListarDocumentos:
    nombre = "listar_documentos"
    descripcion = (
        "Lista los documentos a los que el usuario tiene acceso (identificador, grupo y número "
        "de fragmentos). Úsala para preguntas sobre qué documentos existen o para elegir qué "
        "documento leer con leer_documento o buscar_en_documento."
    )
    args_model = ListarDocumentosArgs

    def __init__(self, retriever: Retriever) -> None:
        self._retriever = retriever

    def ejecutar(
        self, args: ListarDocumentosArgs, usuario: Usuario, top_k: int
    ) -> ResultadoHerramienta:
        grupos = [g for g in usuario.groups if args.grupo in (None, g)]
        docs = self._retriever.list_documents(grupos)
        if not docs:
            return ResultadoHerramienta(nota="No hay documentos visibles.")
        lineas = [
            f"- {d.doc_id} (grupo: {', '.join(d.acl_groups)}; fragmentos: {d.chunks})"
            for d in docs[:MAX_CATALOGO]
        ]
        if len(docs) > MAX_CATALOGO:
            lineas.append(f"- … y {len(docs) - MAX_CATALOGO} documentos más")
        catalogo = Chunk(
            chunk_id=f"catalogo#{args.grupo or '*'}",
            doc_id="catalogo",
            fuente="catálogo de documentos",
            contenido="Documentos disponibles:\n" + "\n".join(lineas),
            acl_groups=grupos,
        )
        return ResultadoHerramienta(chunks=[ChunkRecuperado(chunk=catalogo, score=1.0)])


# ------------------------------------------------------------------- leer_documento
class LeerDocumentoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: DocId
    desde: int = Field(default=0, ge=0, description="Primer fragmento a leer (0 = inicio)")
    cantidad: int = Field(default=3, ge=1, le=8, description="Fragmentos consecutivos a leer")


class LeerDocumento:
    nombre = "leer_documento"
    descripcion = (
        "Lee fragmentos consecutivos de un documento concreto. Úsala cuando la respuesta "
        "necesita el contexto completo de un documento o los fragmentos vecinos de uno ya "
        "encontrado."
    )
    args_model = LeerDocumentoArgs

    def __init__(self, retriever: Retriever) -> None:
        self._retriever = retriever

    def ejecutar(
        self, args: LeerDocumentoArgs, usuario: Usuario, top_k: int
    ) -> ResultadoHerramienta:
        chunks = self._retriever.get_document_chunks(args.doc_id, usuario.groups)
        if not chunks:
            return ResultadoHerramienta(nota=SIN_ACCESO)
        seleccion = chunks[args.desde : args.desde + args.cantidad]
        if not seleccion:
            return ResultadoHerramienta(
                nota=f"{args.doc_id} solo tiene {len(chunks)} fragmentos (0-{len(chunks) - 1})."
            )
        fin = args.desde + len(seleccion) - 1
        return ResultadoHerramienta(
            chunks=[ChunkRecuperado(chunk=c, score=1.0) for c in seleccion],
            nota=f"{args.doc_id}: fragmentos {args.desde}-{fin} de {len(chunks)}.",
        )


# -------------------------------------------------------------- buscar_en_documento
class BuscarEnDocumentoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: DocId
    consulta: str = Field(min_length=1, max_length=500)


class BuscarEnDocumento:
    nombre = "buscar_en_documento"
    descripcion = (
        "Búsqueda semántica restringida a un documento concreto. Úsala cuando ya sabes qué "
        "documento contiene la respuesta y quieres el fragmento más relevante."
    )
    args_model = BuscarEnDocumentoArgs

    def __init__(self, embedder: Embedder, retriever: Retriever) -> None:
        self._embedder = embedder
        self._retriever = retriever

    def ejecutar(
        self, args: BuscarEnDocumentoArgs, usuario: Usuario, top_k: int
    ) -> ResultadoHerramienta:
        [vector] = self._embedder.embed([args.consulta])
        encontrados = self._retriever.search(
            args.consulta, vector, groups=usuario.groups, top_k=top_k, doc_id=args.doc_id
        )
        if not encontrados:
            return ResultadoHerramienta(nota=SIN_ACCESO)
        return ResultadoHerramienta(chunks=encontrados)
