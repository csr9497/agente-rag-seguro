"""Tool data_query: catálogo de consultas con permisos por rol (sin SQL del LLM)."""

import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from app.config import Settings
from app.datos.catalogo import permisos_por_consulta
from app.deps import build_orquestador, build_servicios
from app.models.schemas import Chunk, Usuario
from app.persistencia.repositorios import crear_motor, inicializar
from app.security.acceso import VerificadorRegistro
from app.tools.datos import SIN_ACCESO_DATOS, DataQuery, DataQueryArgs
from tests.fakes import FakeEmbedder, FakeLLM, GuionLLM

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


class SupervisorDeDatos:
    """Supervisor y rag_agent falsos: delega, pide `consulta` a data_query y cita el resultado."""

    def __init__(self, consulta: dict) -> None:
        self.consulta, self.vistos = consulta, []

    def decidir(self, mensajes, herramientas, obligar_herramienta=False):  # noqa: ANN001, ANN201
        self.vistos += mensajes
        nombres = {h["function"]["name"] for h in herramientas}
        tool = [m["content"] for m in mensajes if m["role"] == "tool"]
        if "delegar_rag_agent" in nombres:
            guion = GuionLLM([[("delegar_rag_agent", {"tarea": "datos"})]])
        elif not tool:
            guion = GuionLLM([[("data_query", self.consulta)]])
        else:
            cita = f"[datos:{self.consulta['consulta']}]" if "datos:" in tool[-1] else ""
            guion = GuionLLM([], final=f"Resultado {cita}".strip())
        return guion.decidir(mensajes, herramientas, obligar_herramienta)


def _orquestador(retriever, tmp_path, sup, cache: bool = True):  # noqa: ANN001, ANN202
    s = build_servicios(
        Settings(database_url="sqlite://", almacen_local_dir=str(tmp_path), cache_semantica=cache),
        modelos=(FakeEmbedder(), FakeLLM(), sup), retriever=retriever,
    )  # fmt: skip
    return build_orquestador(s, InMemorySaver(), (FakeEmbedder(), FakeLLM(), sup))


def test_rag_agent_usa_data_query_y_no_se_cachea(retriever, tmp_path) -> None:
    sup = SupervisorDeDatos({"consulta": "festivos", "anio": 2026})
    o = _orquestador(retriever, tmp_path, sup)
    r = o.consultar("¿Qué festivos hay en 2026?", PUBLIC)
    assert r.documentos_consultados == ["datos:festivos"] and not r.respuesta.sin_contexto
    assert r.respuesta.citas[0].fuente == "datos internos: festivos"
    assert not o.consultar("¿Qué festivos hay en 2026?", PUBLIC).desde_cache


def test_public_no_obtiene_datos_de_rrhh(retriever, tmp_path) -> None:
    sup = SupervisorDeDatos({"consulta": "plantilla_por_departamento"})
    r = _orquestador(retriever, tmp_path, sup).consultar("¿Cuánta gente hay en IT?", PUBLIC)
    assert r.respuesta.sin_contexto and r.documentos_consultados == []
    vistos = json.dumps(sup.vistos, ensure_ascii=False)
    assert "Tecnología" not in vistos and SIN_ACCESO_DATOS in vistos
