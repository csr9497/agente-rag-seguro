"""Barrera 4 (access_guardrail) e integridad índice ↔ registro."""

import json

import pytest

from app.config import Settings
from app.deps import build_servicios
from app.models.schemas import Chunk, Usuario
from app.security.acceso import VerificadorRegistro
from tests.fakes import FakeEmbedder, FakeLLM, FakeSupervisor

PUBLIC = Usuario(id="u", groups=["public"])
SECRETO = "CANARIO-RRHH-7F3A: banda B3 hasta 58.000."


@pytest.fixture
def supervisor() -> FakeSupervisor:
    return FakeSupervisor(
        [[("rag_retrieve", json.dumps({"consulta": "banda salario vacaciones"}))]]
    )


@pytest.fixture
def servicios(retriever, supervisor, tmp_path):
    s = build_servicios(
        Settings(database_url="sqlite://", almacen_local_dir=str(tmp_path)),
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


def _sin_rastro_para_el_supervisor(supervisor) -> None:
    todo = json.dumps(supervisor.llamadas, ensure_ascii=False)
    assert "CANARIO" not in todo and "58.000" not in todo


def test_indice_con_acl_manipulada_se_descarta(servicios, supervisor) -> None:
    # Alguien reescribe la ACL de un chunk confidencial en el índice para que lo vea public.
    _inyectar(servicios, "rrhh/bandas.md", SECRETO + " salario", ["public"])
    r = servicios.agente.consultar_detallado("¿salario de la banda?", PUBLIC)
    assert r.fragmentos_descartados == 1
    assert "rrhh/bandas.md" not in r.documentos_consultados
    assert any(
        h.tipo == "acceso_no_autorizado" and "rol_no_autorizado_registro" in h.detalle
        for h in r.hallazgos
    )
    _sin_rastro_para_el_supervisor(supervisor)


def test_chunk_huerfano_sin_registro_se_descarta(servicios, supervisor) -> None:
    _inyectar(servicios, "public/inyectado.md", "CANARIO-RRHH-7F3A salario vacaciones", ["public"])
    r = servicios.agente.consultar_detallado("salario", PUBLIC)
    assert "public/inyectado.md" not in r.documentos_consultados
    assert any("no_registrado" in h.detalle for h in r.hallazgos)
    _sin_rastro_para_el_supervisor(supervisor)


def test_documento_en_cuarentena_no_se_usa(servicios) -> None:
    servicios.registro.marcar_estado("rrhh/vacaciones.md", "bloqueado", "revisión")
    r = servicios.agente.consultar_detallado("vacaciones", PUBLIC)
    assert r.documentos_consultados == [] and r.respuesta.sin_contexto
    assert any("documento_bloqueado" in h.detalle for h in r.hallazgos)


def test_documento_autorizado_pasa(servicios) -> None:
    r = servicios.agente.consultar_detallado("vacaciones", PUBLIC)
    assert r.documentos_consultados == ["rrhh/vacaciones.md"] and r.fragmentos_descartados == 0


def test_supervisor_ve_sin_acceso_igual_que_si_no_existiera(servicios, supervisor) -> None:
    _inyectar(servicios, "rrhh/bandas.md", SECRETO, ["public"])
    servicios.agente.consultar_detallado("x", Usuario(id="u", groups=["public"]))
    mensajes_tool = [m for m in supervisor.llamadas[-1] if m["role"] == "tool"]
    assert all("58.000" not in m["content"] for m in mensajes_tool)


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


def test_catalogo_solo_lista_documentos_activos_del_rol(servicios, supervisor) -> None:
    supervisor.turnos = [[("listar_documentos", "{}")]]
    r = servicios.agente.consultar_detallado("¿qué documentos hay?", PUBLIC)
    catalogo = next(m for m in supervisor.llamadas[1] if m["role"] == "tool")["content"]
    assert "rrhh/vacaciones.md" in catalogo and "bandas" not in catalogo
    assert r.fragmentos_descartados == 0


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
