"""Grupos efectivos del usuario en la conversación (rol + «dept:» + «user:») con la composición
real: el filtro va dentro de la consulta al índice y el registro lo re-chequea."""

import json

import pytest

from app.config import Settings
from app.deps import build_servicios
from app.models.schemas import Usuario
from app.rag.catalogo import construir_catalogo
from ingestor.sources import LocalFolderSource
from tests.fakes import FakeEmbedder, FakeLLM, FakeSupervisor

TEXTO = "# Guía VPN interna\n\nLa VPN interna de TI usa el puerto 4433 y el perfil corporativo.\n"


@pytest.fixture
def servicios(retriever, tmp_path):
    docs = tmp_path / "docs"
    for rel, acl in [
        ("public/vpn-ti.md", {"departamentos": ["it"]}),
        ("public/acuerdo-ana.md", {"usuarios": ["github:ana"]}),
    ]:
        ruta = docs / rel
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(TEXTO.replace("VPN", rel), encoding="utf-8")
        ruta.with_name(ruta.name + ".acl.json").write_text(json.dumps(acl), encoding="utf-8")
    s = build_servicios(
        Settings(
            database_url="sqlite://", almacen_local_dir=str(tmp_path / "almacen"),
            seleccion_libre_de_rol=True, cache_semantica=False,
            departamentos_iniciales={"github:ana": ["it"], "github:luis": ["ventas"]},
        ),
        modelos=(FakeEmbedder(), FakeLLM(), FakeSupervisor()),
        retriever=retriever,
    )  # fmt: skip
    informe = s.gestor.sincronizar(LocalFolderSource(docs))
    assert informe.por_estado() == {"indexado": 2}
    return s


def _consultados(s, usuario_id: str, pregunta: str) -> list[str]:  # noqa: ANN001
    usuario = Usuario(id=usuario_id, groups=["public"])
    conv = s.conversaciones.iniciar(usuario, "public")
    return s.conversaciones.preguntar(usuario, conv.id, pregunta).documentos_consultados


def test_el_departamento_da_acceso_a_lo_interno(servicios) -> None:
    assert "public/vpn-ti.md" in _consultados(servicios, "github:ana", "VPN interna puerto")
    assert "public/vpn-ti.md" not in _consultados(servicios, "github:luis", "VPN interna puerto")


def test_lo_restringido_solo_lo_ve_su_usuario(servicios) -> None:
    pregunta = "acuerdo puerto perfil"
    assert "public/acuerdo-ana.md" in _consultados(servicios, "github:ana", pregunta)
    assert "public/acuerdo-ana.md" not in _consultados(servicios, "github:luis", pregunta)


def test_revocar_corta_el_acceso_aunque_siga_en_el_indice(servicios) -> None:
    servicios.registro.marcar_revocado("public/vpn-ti.md", True)
    assert "public/vpn-ti.md" not in _consultados(servicios, "github:ana", "VPN interna puerto")


def test_el_catalogo_solo_describe_roles_reales() -> None:
    c = construir_catalogo(
        ["public", "dept:it", "user:github:ana"], [], {"public": ("Empleado general", "")}, []
    )
    assert c.roles == ["Empleado general"]
