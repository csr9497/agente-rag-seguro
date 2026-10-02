"""Prompts versionados en LangSmith: publicación desde el repositorio y carga de una versión
(con la copia local como respaldo)."""

from types import SimpleNamespace

import pytest
from langsmith.utils import LangSmithNotFoundError

from app.config import Settings
from app.models.schemas import Usuario
from app.prompts import PROMPTS, local
from app.prompts.publicar import plantilla, publicar
from app.prompts.registro import RegistroPrompts
from app.security.guardrails import GuardrailSalida

PUBLIC = Usuario(id="u1", groups=["public"])


class FakeLangSmith:
    """Prompt Hub en memoria: commits por prompt y etiquetas que apuntan a un commit."""

    def __init__(self) -> None:
        self.commits: dict[str, list[str]] = {}
        self.etiquetas: dict[tuple[str, str], int] = {}

    def _prompt_exists(self, nombre: str) -> bool:
        return nombre in self.commits

    def _get_prompt_url(self, nombre: str) -> str:
        return f"https://smith.langchain.com/prompts/{nombre}"

    def push_prompt(self, nombre, *, object, description, commit_tags):
        self.commits.setdefault(nombre, []).append(object.messages[0].prompt.template)
        for e in commit_tags:
            self.etiquetas[(nombre, e)] = len(self.commits[nombre]) - 1
        return self._get_prompt_url(nombre)

    def pull_prompt(self, identificador: str):
        nombre, _, version = identificador.partition(":")
        if nombre not in self.commits:
            raise LangSmithNotFoundError(identificador)
        if not version or version == "latest":
            i = len(self.commits[nombre]) - 1
        elif (nombre, version) in self.etiquetas:
            i = self.etiquetas[(nombre, version)]
        else:
            raise LangSmithNotFoundError(identificador)
        return plantilla(self.commits[nombre][i])

    def list_prompt_commits(self, nombre, limit):
        yield SimpleNamespace(owner="-", repo=nombre, id=len(self.commits[nombre]) - 1)

    def request_with_retries(self, metodo, ruta, json):
        nombre = ruta.split("/")[3]
        etiqueta = ruta.split("/")[5] if metodo == "PATCH" else json["tag_name"]
        if metodo == "PATCH" and (nombre, etiqueta) not in self.etiquetas:
            raise LangSmithNotFoundError(ruta)
        self.etiquetas[(nombre, etiqueta)] = int(json["commit_id"])


def test_publicar_crea_los_prompts_y_repetirlo_no_duplica() -> None:
    hub = FakeLangSmith()
    primera = publicar(hub, ["dev"])
    assert {p.estado for p in primera} == {"nuevo"}
    assert {p.nombre for p in primera} == {n for n, _ in PROMPTS.values()}
    assert hub.commits["agente-rag-orquestador"] == [local("orquestador")]

    segunda = publicar(hub, ["dev", "prod"])
    assert {p.estado for p in segunda} == {"sin cambios"}
    assert all(len(c) == 1 for c in hub.commits.values())
    assert hub.etiquetas[("agente-rag-rag-agent", "prod")] == 0  # la etiqueta se crea igual


def test_publicar_sube_un_commit_si_el_texto_cambio() -> None:
    hub = FakeLangSmith()
    hub.commits["agente-rag-guardian"] = ["versión antigua"]
    estados = {p.nombre: p.estado for p in publicar(hub, ["dev"])}
    assert estados["agente-rag-guardian"] == "actualizado"
    assert hub.commits["agente-rag-guardian"][-1] == local("guardian")


def test_publicar_rechaza_etiquetas_no_validas() -> None:
    with pytest.raises(ValueError):
        publicar(FakeLangSmith(), ["dev/../prod"])


def test_las_llaves_del_texto_no_son_variables() -> None:
    assert plantilla('Responde en JSON: {"a": 1}').messages[0].prompt.template.endswith("1}")


def _registro(hub, **settings) -> RegistroPrompts:
    return RegistroPrompts(Settings(**settings), hub)


def test_registro_carga_la_etiqueta_y_avisa_a_los_guardrails() -> None:
    hub = FakeLangSmith()
    texto = local("orquestador") + "\nRegla nueva de la versión de LangSmith para probarla."
    hub.push_prompt("agente-rag-orquestador", object=plantilla(texto), description="",
                    commit_tags=["dev"])  # fmt: skip
    registro = _registro(hub)
    cargados: list[str] = []
    registro.al_cargar(cargados.append)
    assert registro.texto("orquestador", "dev") == (texto, "dev")
    assert cargados == [texto]
    assert registro.texto("orquestador") == (local("orquestador"), "local")  # PROMPTS_ORIGEN=local


def test_registro_usa_el_local_si_la_version_no_existe_o_no_hay_cliente() -> None:
    assert _registro(FakeLangSmith()).texto("orquestador", "prod")[1] == "local (fallback)"
    assert _registro(None, prompts_origen="langsmith").texto("rag_agent")[1] == "local (fallback)"


def test_el_guardrail_de_salida_vigila_los_prompts_cargados() -> None:
    linea = "Esta es una instrucción secreta de una versión del prompt cargada de LangSmith."
    guardrail = GuardrailSalida([])
    assert guardrail.revisar(f"Te cuento: {linea}").permitido
    guardrail.proteger(linea)
    assert not guardrail.revisar(f"Te cuento: {linea}").permitido
