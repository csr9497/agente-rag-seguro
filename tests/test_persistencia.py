import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.models.schemas import Cita, Hallazgo
from app.persistencia.modelos import DocumentoRegistrado, MensajeGuardado, Rol
from app.persistencia.repositorios import (
    SqlRepositorioConversaciones,
    SqlRepositorioDocumentos,
    SqlRepositorioRoles,
    crear_motor,
    inicializar,
)


@pytest.fixture
def motor():
    m = crear_motor("sqlite://")
    inicializar(m)
    return m


def _doc(doc_id="public/a.md", roles=("public",), **kw) -> DocumentoRegistrado:
    return DocumentoRegistrado(
        doc_id=doc_id, titulo="A", roles=list(roles), doc_hash="h" * 64, chunks=1, **kw
    )


# ------------------------------------------------------------------ roles
def test_semilla_de_roles(motor) -> None:
    roles = {r.id: r for r in SqlRepositorioRoles(motor).listar()}
    assert set(roles) == {"administrador", "rrhh", "finanzas", "public"}
    assert roles["administrador"].puede("administrar_roles")
    assert roles["rrhh"].puede("gestionar_documentos")
    assert roles["rrhh"].publica_para == ["public", "rrhh"]
    assert roles["public"].permisos == [] and roles["public"].creado_en


def test_inicializar_es_idempotente(motor) -> None:
    inicializar(motor)
    assert len(SqlRepositorioRoles(motor).listar()) == 4


def test_guardar_actualiza_permisos_y_publicacion(motor) -> None:
    repo = SqlRepositorioRoles(motor)
    repo.guardar(Rol(id="compras", nombre="Compras", permisos=["gestionar_documentos"]))
    rol = repo.obtener("compras")
    rol = rol.model_copy(update={"permisos": [], "publica_para": ["compras", "public"]})
    repo.guardar(rol)
    guardado = repo.obtener("compras")
    assert guardado.permisos == [] and guardado.publica_para == ["compras", "public"]
    assert guardado.creado_en == rol.creado_en


def test_rol_inactivo_no_tiene_permisos_efectivos(motor) -> None:
    repo = SqlRepositorioRoles(motor)
    rrhh = repo.obtener("rrhh")
    repo.guardar(rrhh.model_copy(update={"activo": False}))
    assert not repo.obtener("rrhh").puede("gestionar_documentos")
    assert "rrhh" not in {r.id for r in repo.listar(incluir_inactivos=False)}


def test_publica_para_exige_roles_existentes(motor) -> None:
    with pytest.raises(IntegrityError):
        SqlRepositorioRoles(motor).guardar(Rol(id="x", nombre="X", publica_para=["no-existe"]))


@pytest.mark.parametrize("rol_id", ["RRHH", "con espacio", "../x", ""])
def test_id_de_rol_invalido(rol_id) -> None:
    with pytest.raises(ValidationError):
        Rol(id=rol_id, nombre="X")


# ------------------------------------------------------------------ documentos
def test_registrar_listar_por_rol_y_eliminar(motor) -> None:
    repo = SqlRepositorioDocumentos(motor)
    repo.registrar(_doc("public/a.md", ("public", "rrhh")))
    repo.registrar(_doc("rrhh/b.md", ("rrhh",)))
    assert [d.doc_id for d in repo.listar("public")] == ["public/a.md"]
    assert [d.doc_id for d in repo.listar("rrhh")] == ["public/a.md", "rrhh/b.md"]
    assert repo.eliminar("rrhh/b.md") and not repo.eliminar("rrhh/b.md")
    assert repo.obtener("rrhh/b.md") is None


def test_registrar_de_nuevo_reemplaza_roles(motor) -> None:
    repo = SqlRepositorioDocumentos(motor)
    repo.registrar(_doc(roles=("public", "rrhh")))
    repo.registrar(_doc(roles=("rrhh",)))
    assert repo.obtener("public/a.md").roles == ["rrhh"]


def test_documento_con_rol_inexistente_falla(motor) -> None:
    with pytest.raises(IntegrityError):
        SqlRepositorioDocumentos(motor).registrar(_doc(roles=("fantasma",)))


def test_documento_sin_roles_no_es_valido() -> None:
    with pytest.raises(ValidationError):
        _doc(roles=())


def test_cuarentena(motor) -> None:
    repo = SqlRepositorioDocumentos(motor)
    repo.registrar(_doc())
    repo.marcar_estado("public/a.md", "bloqueado", "roles desincronizados")
    doc = repo.obtener("public/a.md")
    assert doc.estado == "bloqueado" and doc.motivo_estado == "roles desincronizados"
    assert repo.listar(estado="activo") == []


# ------------------------------------------------------------------ conversaciones
def test_conversacion_con_mensajes(motor) -> None:
    repo = SqlRepositorioConversaciones(motor)
    conv = repo.crear("rrhh")
    cita = Cita(
        numero=1,
        doc_id="rrhh/b.md",
        chunk_id="rrhh/b.md#0",
        fuente="rrhh/b.md",
        fragmento="…",
        score=0.9,
    )
    guardado = repo.agregar_mensaje(
        conv.id,
        MensajeGuardado(
            pregunta="¿Banda B3?",
            respuesta="42.000–58.000 [1]",
            sin_contexto=False,
            citas=[cita],
            documentos_consultados=["rrhh/b.md", "public/a.md"],
            fragmentos_descartados=1,
            hallazgos=[Hallazgo(tipo="pii", detalle="email", accion="enmascarar")],
        ),
    )
    assert guardado.id == 1 and guardado.creado_en
    leida = repo.obtener(conv.id)
    assert leida.rol_id == "rrhh" and len(leida.mensajes) == 1
    m = leida.mensajes[0]
    assert m.citas == [cita] and m.fragmentos_descartados == 1
    assert m.documentos_consultados == ["rrhh/b.md", "public/a.md"]
    assert m.hallazgos[0].detalle == "email"


def test_conversacion_con_rol_inexistente_falla(motor) -> None:
    with pytest.raises(IntegrityError):
        SqlRepositorioConversaciones(motor).crear("fantasma")


def test_conversacion_inexistente(motor) -> None:
    assert SqlRepositorioConversaciones(motor).obtener("no-existe") is None


def test_sqlite_en_disco(tmp_path) -> None:
    url = f"sqlite:///{tmp_path}/sub/app.db"
    inicializar(crear_motor(url))
    assert (tmp_path / "sub" / "app.db").exists()
    assert len(SqlRepositorioRoles(crear_motor(url)).listar()) == 4


def test_sqlite_en_archivo_admite_peticiones_concurrentes(tmp_path) -> None:
    """La web lanza peticiones en paralelo (documentos, roles, historial): el motor de la app
    debe aguantar lecturas concurrentes sin mezclar resultados."""
    from concurrent.futures import ThreadPoolExecutor

    from app.persistencia.repositorios import SqlRepositorioRoles, crear_motor, inicializar

    motor = crear_motor(f"sqlite:///{tmp_path / 'app.db'}")
    inicializar(motor)
    roles = SqlRepositorioRoles(motor)
    with ThreadPoolExecutor(max_workers=8) as pool:
        resultados = list(pool.map(lambda _: len(roles.listar()), range(200)))
    assert set(resultados) == {4}


def test_inicializar_crea_los_roles_que_faltan_sin_tocar_los_existentes(tmp_path) -> None:
    from app.persistencia.repositorios import SqlRepositorioRoles, crear_motor, inicializar

    motor = crear_motor(f"sqlite:///{tmp_path / 'app.db'}")
    inicializar(motor)
    roles = SqlRepositorioRoles(motor)
    public = roles.obtener("public")
    roles.guardar(public.model_copy(update={"descripcion": "Editada por el administrador"}))
    with motor.begin() as c:  # base antigua: sin el rol finanzas
        from sqlalchemy import text

        c.execute(text("delete from rol_publica_para where rol_id = 'finanzas'"))
        c.execute(text("delete from rol_permisos where rol_id = 'finanzas'"))
        c.execute(text("delete from roles where id = 'finanzas'"))
    inicializar(motor)
    assert roles.obtener("finanzas").publica_para == ["public"]
    assert roles.obtener("public").descripcion == "Editada por el administrador"
