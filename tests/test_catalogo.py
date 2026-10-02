"""Catálogo del rol: qué puede consultar el usuario, para orientarle cuando no hay información.
Regla 1: sale de los permisos del dato; nunca incluye nada de roles que el usuario no tiene."""

from app.rag.catalogo import construir_catalogo

ROLES = {
    "public": ("Empleado general", "Políticas generales: vacaciones, teletrabajo, onboarding."),
    "rrhh": ("Recursos Humanos", "Nóminas, bandas salariales y procesos de RRHH."),
}
DOCUMENTOS = [
    ("public/vacaciones.md", "Política de vacaciones", ["public"]),
    ("public/teletrabajo.md", "Política de teletrabajo", ["public", "rrhh"]),
    ("rrhh/bandas.md", "Bandas salariales 2026", ["rrhh"]),
]
DATOS = [
    ("Festivos oficiales de la empresa en un año (parámetro: anio)", ["public", "rrhh"]),
    ("Número de personas por departamento", ["rrhh"]),
]


def test_solo_documentos_y_roles_del_usuario() -> None:
    c = construir_catalogo(["public"], DOCUMENTOS, ROLES, DATOS)
    assert c.documentos == ["Política de teletrabajo", "Política de vacaciones"]
    assert c.roles == [f"Empleado general: {ROLES['public'][1]}"]
    texto = c.como_texto()
    assert "Bandas salariales" not in texto and "Recursos Humanos" not in texto
    assert "Nóminas" not in texto


def test_el_supervisor_recibe_los_identificadores_reales_y_el_usuario_solo_titulos() -> None:
    """Regresión: con solo títulos, el supervisor inventaba identificadores para
    buscar_en_documento («el documento no existe»)."""
    c = construir_catalogo(["public"], DOCUMENTOS, ROLES, DATOS)
    assert "- Política de vacaciones (id: public/vacaciones.md)" in c.como_texto(con_ids=True)
    assert "public/vacaciones.md" not in c.como_texto()
    assert "rrhh/bandas.md" not in c.como_texto(con_ids=True)


def test_varios_roles_suman_sin_duplicar() -> None:
    c = construir_catalogo(["public", "rrhh"], DOCUMENTOS, ROLES, DATOS)
    assert c.documentos == [
        "Bandas salariales 2026", "Política de teletrabajo", "Política de vacaciones",
    ]  # fmt: skip
    assert len(c.roles) == 2


def test_sin_grupos_catalogo_vacio() -> None:
    c = construir_catalogo([], DOCUMENTOS, ROLES, DATOS)
    assert c.vacio and c.documentos == [] and c.datos == []


def test_datos_internos_filtrados_por_rol_y_sin_detalles_tecnicos() -> None:
    assert construir_catalogo(["public"], [], ROLES, DATOS).datos == [
        "Festivos oficiales de la empresa en un año"
    ]
    assert len(construir_catalogo(["rrhh"], [], ROLES, DATOS).datos) == 2
    assert construir_catalogo(["public"], [], ROLES, []).datos == []


def test_rol_sin_descripcion_usa_el_nombre() -> None:
    c = construir_catalogo(["finanzas"], [], {"finanzas": ("Finanzas", "")}, [])
    assert c.roles == ["Finanzas"]


def test_composicion_real_public_no_ve_titulos_de_otros_roles(retriever, tmp_path) -> None:
    """Registro, roles y documentos de ejemplo reales: la orientación de un empleado general
    (el FakeLLM repite el catálogo que recibe) no nombra nada de RRHH ni de Finanzas."""

    from app.config import Settings
    from app.deps import build_servicios
    from app.models.schemas import Usuario
    from ingestor.sources import LocalFolderSource
    from tests.conftest import SAMPLE_DOCS, conectar_orquestador
    from tests.fakes import FakeEmbedder, FakeLLM, GuionLLM

    llm = FakeLLM()
    sup = GuionLLM([[("conversacion", {"tipo": "fuera_de_ambito"})]])
    s = build_servicios(
        Settings(database_url="sqlite://", almacen_local_dir=str(tmp_path)),
        modelos=(FakeEmbedder(), llm, sup),
        retriever=retriever,
    )
    s.gestor.sincronizar(LocalFolderSource(SAMPLE_DOCS))
    o = conectar_orquestador(s, (FakeEmbedder(), llm, sup))
    usuario = Usuario(id="u", groups=["public"])
    r = o.consultar("dame una receta de pasta", usuario).respuesta
    assert "Política de vacaciones" in r.respuesta or "vacaciones" in r.respuesta.lower()
    for ajeno in (
        "Bandas", "contratación", "Nómina", "Presupuesto", "Recursos Humanos", "personas por",
    ):  # fmt: skip
        assert ajeno.lower() not in r.respuesta.lower(), ajeno
    assert "<catalogo>" in str(sup.llamadas[0])  # el supervisor también lo recibió
