"""Re-chequeo contra el registro (barrera 4: lo que hacía access_guardrail, ahora en las tools
de rag_agent) e integridad índice ↔ registro."""

import json
import logging

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.config import Settings
from app.deps import build_orquestador, build_servicios
from app.models.schemas import Chunk, Usuario
from app.security.acceso import VerificadorRegistro
from tests.fakes import FakeEmbedder, FakeLLM, RagEco

PUBLIC = Usuario(id="u", groups=["public"])
SECRETO = "CANARIO-RRHH-7F3A: banda B3 hasta 58.000."


@pytest.fixture
def supervisor() -> RagEco:
    """Supervisor y rag_agent falsos; `vistos` guarda todo lo que recibieron los modelos."""
    return RagEco()


@pytest.fixture
def servicios(retriever, supervisor, tmp_path):
    s = build_servicios(
        Settings(database_url="sqlite://", almacen_local_dir=str(tmp_path), cache_semantica=False),
        modelos=(FakeEmbedder(), FakeLLM(), supervisor),
        retriever=retriever,
    )
    s.gestor.indexar(
        "rrhh/vacaciones.md", b"Vacaciones: 23 dias al salario.", roles=["public", "rrhh"]
    )
    s.gestor.indexar("rrhh/bandas.md", SECRETO.encode(), roles=["rrhh"])
    return s


def _inyectar(servicios, doc_id: str, contenido: str, acl: list[str], doc_hash: str = "") -> None:
    """Simula un índice manipulado o desincronizado escribiendo directamente en él."""
    chunk = Chunk(
        chunk_id=f"{doc_id}#99", doc_id=doc_id, fuente=doc_id, contenido=contenido,
        acl_groups=acl, doc_hash=doc_hash,
    )  # fmt: skip
    [v] = FakeEmbedder().embed([contenido])
    servicios.retriever.upsert([chunk], [v])


@pytest.fixture
def consultar(servicios, supervisor):
    """Consulta por el orquestador real; devuelve (resultado, eventos de seguridad)."""
    orquestador = build_orquestador(
        servicios, InMemorySaver(), (FakeEmbedder(), FakeLLM(), supervisor)
    )

    def _consultar(pregunta: str, caplog):  # noqa: ANN001, ANN202
        with caplog.at_level(logging.WARNING, logger="seguridad"):
            r = orquestador.consultar(pregunta, PUBLIC)
        eventos = [json.loads(x.getMessage()) for x in caplog.records if x.name == "seguridad"]
        return r, eventos

    return _consultar


def _sin_rastro_para_los_modelos(supervisor) -> None:
    todo = json.dumps(supervisor.vistos, ensure_ascii=False)
    assert "CANARIO" not in todo and "58.000" not in todo


def test_indice_con_acl_manipulada_se_descarta(servicios, supervisor, consultar, caplog) -> None:
    # Alguien reescribe la ACL de un chunk confidencial en el índice para que lo vea public.
    _inyectar(servicios, "rrhh/bandas.md", SECRETO + " salario", ["public"])
    r, eventos = consultar("¿salario de la banda?", caplog)
    assert "rrhh/bandas.md" not in r.documentos_consultados
    assert {(e["doc_id"], e["motivo"]) for e in eventos} == {
        ("rrhh/bandas.md", "rol_no_autorizado_registro")
    }
    assert "CANARIO" not in json.dumps(eventos)  # el evento no lleva el contenido
    _sin_rastro_para_los_modelos(supervisor)


def test_chunk_huerfano_sin_registro_se_descarta(servicios, supervisor, consultar, caplog) -> None:
    _inyectar(servicios, "public/inyectado.md", "CANARIO-RRHH-7F3A salario vacaciones", ["public"])
    r, eventos = consultar("salario", caplog)
    assert "public/inyectado.md" not in r.documentos_consultados
    assert any(e["motivo"] == "no_registrado" for e in eventos)
    _sin_rastro_para_los_modelos(supervisor)


def test_documento_en_cuarentena_no_se_usa(servicios, consultar, caplog) -> None:
    servicios.registro.marcar_estado("rrhh/vacaciones.md", "bloqueado", "revisión")
    r, eventos = consultar("vacaciones", caplog)
    assert r.documentos_consultados == [] and r.respuesta.sin_contexto
    assert any(e["motivo"] == "documento_bloqueado" for e in eventos)


def test_documento_autorizado_pasa(consultar, caplog) -> None:
    r, eventos = consultar("vacaciones", caplog)
    assert r.documentos_consultados == ["rrhh/vacaciones.md"] and eventos == []


# ------------------------------------------------------------------ verificador
@pytest.mark.parametrize(
    ("acl", "doc_hash", "esperado"),
    [
        (["public", "rrhh"], "", None),
        (["public"], "", "acl_desincronizada"),
        (["public", "rrhh"], "0" * 64, "hash_distinto"),
    ],
)
def test_verificador_registro(servicios, acl, doc_hash, esperado) -> None:
    doc = servicios.registro.obtener("rrhh/vacaciones.md")
    chunk = Chunk(chunk_id="rrhh/vacaciones.md#0", doc_id="rrhh/vacaciones.md", fuente="x",
                  contenido="x", acl_groups=acl, doc_hash=doc_hash or doc.doc_hash)  # fmt: skip
    assert VerificadorRegistro(servicios.registro).motivo_rechazo(chunk, ["public"]) == esperado


# ------------------------------------------------------------------ integridad
def test_integridad_limpia(servicios) -> None:
    informe = servicios.verificar_integridad()
    assert informe.ok and informe.revisados == 2


def test_integridad_detecta_y_pone_en_cuarentena(servicios) -> None:
    _inyectar(servicios, "public/huerfano.md", "nada", ["public"])
    # ACL desincronizada: el índice dice public, el registro solo rrhh.
    servicios.retriever.delete_document("rrhh/bandas.md")
    _inyectar(servicios, "rrhh/bandas.md", SECRETO, ["public"],
              doc_hash=servicios.registro.obtener("rrhh/bandas.md").doc_hash)  # fmt: skip
    informe = servicios.verificar_integridad()
    tipos = {(p.doc_id, p.tipo, p.accion) for p in informe.problemas}
    assert ("public/huerfano.md", "huerfano_en_indice", "ninguna") in tipos
    assert ("rrhh/bandas.md", "acl_desincronizada", "bloqueado") in tipos
    assert servicios.registro.obtener("rrhh/bandas.md").estado == "bloqueado"


def test_integridad_falta_en_indice_y_reactivacion(servicios) -> None:
    servicios.retriever.delete_document("rrhh/vacaciones.md")
    informe = servicios.verificar_integridad()
    assert [(p.tipo, p.accion) for p in informe.problemas] == [("falta_en_indice", "pendiente")]
    assert servicios.registro.obtener("rrhh/vacaciones.md").estado == "pendiente"

    # Reindexar lo deja coherente y la siguiente verificación lo reactiva.
    servicios.gestor.indexar(
        "rrhh/vacaciones.md", b"Vacaciones: 23 dias al salario.", roles=["public", "rrhh"],
        forzar=True,
    )  # fmt: skip
    servicios.registro.marcar_estado("rrhh/vacaciones.md", "pendiente", "x")
    informe = servicios.verificar_integridad()
    assert informe.ok and informe.reactivados == ["rrhh/vacaciones.md"]
