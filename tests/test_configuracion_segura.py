"""Regresión de configuración: los modos de depuración nunca quedan expuestos."""

import re
from pathlib import Path

import pytest
import yaml

from app.config import ConfiguracionInseguraError, Settings, validar_seguridad

RAIZ = Path(__file__).parents[1]
FLAGS_SOLO_LOCAL = (
    "IDENTIDAD_DEBUG",
    "GESTION_DOCUMENTOS",
    "EXPONER_TOPOLOGIA",
    "SELECCION_LIBRE_DE_ROL",
)


def _compose() -> dict:
    return yaml.safe_load((RAIZ / "docker-compose.yml").read_text(encoding="utf-8"))


def test_servicios_solo_escuchan_en_localhost() -> None:
    servicios = _compose()["services"]
    for nombre in ("app", "qdrant", "web", "redis", "postgres"):
        for puerto in servicios[nombre]["ports"]:
            assert str(puerto).startswith("127.0.0.1:"), f"{nombre} expuesto: {puerto}"


def test_la_web_elimina_la_cabecera_de_identidad() -> None:
    conf = (RAIZ / "web" / "default.conf.template").read_text(encoding="utf-8")
    # En cada location que hace proxy al backend.
    assert conf.count("proxy_pass") == conf.count('proxy_set_header X-Usuario-Grupos "";') == 2


def test_terraform_no_activa_modos_de_depuracion() -> None:
    """En Azure estos flags solo pueden aparecer fijados explícitamente a "false"."""
    for tf in (RAIZ / "infra").rglob("*.tf"):
        contenido = tf.read_text(encoding="utf-8")
        for flag in FLAGS_SOLO_LOCAL:
            for m in re.finditer(rf"\b{flag}\b\s*=\s*(\S+)", contenido):
                assert m.group(1) == '"false"', f"{flag} = {m.group(1)} en {tf}"
            menciones = len(re.findall(rf"\b{flag}\b", contenido))
            asignaciones = len(re.findall(rf"\b{flag}\b\s*=", contenido))
            assert menciones == asignaciones, f"{flag} usado fuera de una asignación en {tf}"


def test_trazas_completas_nunca_en_terraform() -> None:
    for tf in (RAIZ / "infra").rglob("*.tf"):
        assert (
            '"completo"'
            not in re.sub(r"#.*", "", tf.read_text(encoding="utf-8")).split("TRAZAS_MODO")[-1][:80]
        ), f"TRAZAS_MODO completo en {tf}"


def test_compose_no_contiene_contrasenas() -> None:
    """Regla 3: las credenciales vienen de .env, nunca escritas en el compose."""
    postgres = _compose()["services"]["postgres"]["environment"]
    assert postgres["POSTGRES_PASSWORD"].startswith("${POSTGRES_PASSWORD")


def test_cliente_azure_openai_reintenta_429_con_backoff() -> None:
    from app.config import Settings
    from app.modelos.openai_compat import _cliente_base

    s = Settings(
        azure_openai_endpoint="https://x.openai.azure.com/",
        azure_openai_api_key="k",
        modelos_max_reintentos=4,
        modelos_timeout_s=30,
    )
    c = _cliente_base(s)
    assert c.max_retries == 4 and c.timeout == 30


def test_supervisor_azure_obliga_herramienta_solo_si_se_pide() -> None:
    from types import SimpleNamespace

    from app.modelos.openai_compat import SupervisorOpenAI

    enviados = []

    def create(**kw):
        enviados.append(kw["tool_choice"])
        mensaje = SimpleNamespace(tool_calls=None, model_dump=lambda **_: {"role": "assistant"})
        return SimpleNamespace(choices=[SimpleNamespace(message=mensaje)])

    cliente = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    sup = SupervisorOpenAI(cliente, "gpt-4o")
    sup.decidir([], [{"type": "function"}], obligar_herramienta=True)
    sup.decidir([], [{"type": "function"}])
    assert enviados == ["required", "auto"]


def test_umbral_de_cache_estricto_para_ada() -> None:
    from app.config import Settings

    assert Settings().cache_umbral >= 0.97


ENTRA = {"auth_modo": "entra", "entra_tenant_id": "t", "entra_audiencia": "api://x"}


EASYAUTH = {"auth_modo": "easyauth", "proxy_secreto": "x" * 48}


@pytest.mark.parametrize(
    "kw",
    [
        {},  # stub por defecto: la web es pública y todos serían «anonimo»
        {"auth_modo": "entra"},  # sin tenant ni audiencia
        {"auth_modo": "easyauth"},  # sin secreto de proxy
        {"auth_modo": "easyauth", "proxy_secreto": "corto"},
        {**ENTRA, "identidad_debug": True},
        {**ENTRA, "seleccion_libre_de_rol": True},
        {**EASYAUTH, "identidad_debug": True},
    ],
)
def test_prod_falla_cerrada(kw) -> None:
    with pytest.raises(ConfiguracionInseguraError):
        validar_seguridad(Settings(entorno="prod", **kw))


def test_prod_con_entra_y_local_sin_restricciones() -> None:
    validar_seguridad(Settings(entorno="prod", **ENTRA))
    validar_seguridad(Settings(entorno="prod", **EASYAUTH))
    validar_seguridad(Settings(entorno="local", identidad_debug=True, seleccion_libre_de_rol=True))


def test_terraform_exige_login() -> None:
    """La web pública siempre con Easy Auth (Entra ID) y el backend en modo easyauth."""
    apps = (RAIZ / "infra" / "apps" / "main.tf").read_text(encoding="utf-8")
    assert re.search(r'AUTH_MODO\s*=\s*"easyauth"', apps)
    assert '"RedirectToLoginPage"' in apps and "Microsoft.App/containerApps/authConfigs" in apps
    identidad = (RAIZ / "infra" / "identidad" / "main.tf").read_text(encoding="utf-8")
    assert "app_role_assignment_required = true" in identidad  # solo usuarios con rol
