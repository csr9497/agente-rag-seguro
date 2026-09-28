import pytest
from pydantic import ValidationError

from app.models.schemas import Usuario
from app.tools.base import schema_openai
from app.tools.rag_retrieve import RagRetrieve, RagRetrieveArgs


def test_rag_retrieve_filtra_por_grupos_del_usuario(retriever_con_docs, embedder) -> None:
    tool = RagRetrieve(embedder, retriever_con_docs)
    args = RagRetrieveArgs(consulta="bandas salariales")
    public = tool.ejecutar(args, Usuario(id="a", groups=["public"]), top_k=10)
    rrhh = tool.ejecutar(args, Usuario(id="b", groups=["rrhh"]), top_k=10)
    assert public and all(r.chunk.doc_id.startswith("public/") for r in public)
    assert {r.chunk.doc_id for r in rrhh} == {"rrhh/bandas-salariales.md"}


def test_rag_retrieve_respeta_top_k(retriever_con_docs, embedder) -> None:
    tool = RagRetrieve(embedder, retriever_con_docs)
    r = tool.ejecutar(RagRetrieveArgs(consulta="política"), Usuario(id="a", groups=["public"]), 1)
    assert len(r) == 1


@pytest.mark.parametrize(
    "payload",
    ['{"consulta": ""}', '{"consulta": "x", "groups": ["rrhh"]}', "{}", "no-json"],
)
def test_args_invalidos(payload) -> None:
    with pytest.raises(ValidationError):
        RagRetrieveArgs.model_validate_json(payload)


def test_schema_openai_no_expone_grupos() -> None:
    schema = schema_openai(RagRetrieve)  # type: ignore[arg-type]
    params = schema["function"]["parameters"]
    assert schema["function"]["name"] == "rag_retrieve"
    assert set(params["properties"]) == {"consulta"}
    assert params["additionalProperties"] is False
