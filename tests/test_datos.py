"""Tool data_query: catálogo de consultas con permisos por rol (sin SQL del LLM)."""

import json

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.datos.catalogo import permisos_por_consulta
from app.deps import build_servicios
from app.models.schemas import Chunk, Usuario
from app.persistencia.repositorios import crear_motor, inicializar
from app.security.acceso import VerificadorRegistro
from app.tools.datos import SIN_ACCESO_DATOS, DataQuery, DataQueryArgs
from tests.fakes import FakeEmbedder, FakeLLM, FakeSupervisor

PUBLIC = Usuario(id="u", groups=["public"])
RRHH = Usuario(id="r", groups=["rrhh"])


@pytest.fixture
def tool() -> DataQuery:
    motor = crear_motor("sqlite://")
    inicializar(motor)
    return DataQuery(motor)


def test_festivos_para_todos(tool) -> None:
    r = tool.ejecutar(DataQueryArgs(consulta="festivos", anio=2026), PUBLIC, 4)
    [chunk] = r.chunks
    assert chunk.chunk.doc_id == "datos:festivos" and chunk.chunk.acl_groups == ["public"]
    assert "2026-07-28 | Fiestas Patrias" in chunk.chunk.contenido
    assert "2027" not in chunk.chunk.contenido


def test_datos_de_rrhh_solo_para_rrhh(tool) -> None:
    args = DataQueryArgs(consulta="presupuesto_formacion", departamento="it")
    denegado = tool.ejecutar(args, PUBLIC, 4)
    assert denegado.chunks == [] and denegado.nota == SIN_ACCESO_DATOS
    [chunk] = tool.ejecutar(args, RRHH, 4).chunks
    assert (
        "it | Tecnología | 60000" in chunk.chunk.contenido and "ventas" not in chunk.chunk.contenido
    )


def test_parametros_obligatorios(tool) -> None:
    r = tool.ejecutar(DataQueryArgs(consulta="festivos"), PUBLIC, 4)
    assert r.chunks == [] and "Parámetros no válidos" in (r.nota or "")


@pytest.mark.parametrize(
    "payload",
    [
        {"consulta": "DROP TABLE roles"},
        {"consulta": "festivos", "anio": 2026, "sql": "select *"},
        {"consulta": "plantilla_por_departamento", "departamento": "it' OR '1'='1"},
        {"consulta": "festivos", "anio": 1900},
    ],
    ids=["consulta_libre", "campo_extra", "inyeccion_sql", "fuera_de_rango"],
)
def test_argumentos_invalidos(payload) -> None:
    with pytest.raises(ValidationError):
        DataQueryArgs.model_validate_json(json.dumps(payload))


def test_verificador_contrasta_con_el_catalogo(tool) -> None:
    motor = crear_motor("sqlite://")
    inicializar(motor)
    from app.persistencia.repositorios import SqlRepositorioDocumentos

    v = VerificadorRegistro(SqlRepositorioDocumentos(motor), permisos_por_consulta())
    forjado = Chunk(chunk_id="datos:presupuesto_formacion#x", doc_id="datos:presupuesto_formacion",
                    fuente="x", contenido="x", acl_groups=["public"])  # fmt: skip
    assert v.motivo_rechazo(forjado, ["public"]) == "consulta_no_autorizada"
    legitimo = forjado.model_copy(update={"acl_groups": ["rrhh"]})
    assert v.motivo_rechazo(legitimo, ["rrhh"]) is None
    inventado = forjado.model_copy(update={"doc_id": "datos:inventada"})
    assert v.motivo_rechazo(inventado, ["rrhh"]) == "consulta_no_autorizada"


def test_agente_usa_data_query_y_no_cachea(retriever, tmp_path) -> None:
    llm = FakeLLM()
    sup = FakeSupervisor([[("data_query", json.dumps({"consulta": "festivos", "anio": 2026}))]])
    s = build_servicios(
        Settings(database_url="sqlite://", almacen_local_dir=str(tmp_path)),
        modelos=(FakeEmbedder(), llm, sup),
        retriever=retriever,
    )
    r = s.agente.consultar_detallado("¿Qué festivos hay en 2026?", PUBLIC)
    assert r.documentos_consultados == ["datos:festivos"]
    _, user = llm.llamadas[0]
    assert 'fuente="datos internos: festivos"' in user
    assert not s.agente.consultar_detallado("¿Qué festivos hay en 2026?", PUBLIC).desde_cache


def test_agente_public_no_obtiene_datos_de_rrhh(retriever, tmp_path) -> None:
    llm = FakeLLM()
    sup = FakeSupervisor([[("data_query", json.dumps({"consulta": "plantilla_por_departamento"}))]])
    s = build_servicios(
        Settings(database_url="sqlite://", almacen_local_dir=str(tmp_path)),
        modelos=(FakeEmbedder(), llm, sup),
        retriever=retriever,
    )
    r = s.agente.consultar_detallado("¿Cuánta gente hay en IT?", PUBLIC)
    assert r.respuesta.sin_contexto and llm.llamadas == []
    assert SIN_ACCESO_DATOS in json.dumps(sup.llamadas[1], ensure_ascii=False)
