"""Proveedor de modelos: configuración, clasificación de errores, capacidades y diagnóstico."""

import json
import logging
import subprocess
from types import SimpleNamespace

import httpx
import openai
import pytest
from azure.core.exceptions import ClientAuthenticationError
from fastapi.testclient import TestClient
from openai import AzureOpenAI, OpenAI

from app.config import Settings
from app.deps import build_modelos
from app.main import app, get_agente
from app.modelos.diagnostico import diagnosticar, requisitos_azure, requisitos_openai
from app.modelos.errores import ModeloError, clasificar, es_filtro_de_contenido
from app.modelos.openai_compat import (
    SIN_CLAVE,
    EmbedderOpenAI,
    LLMOpenAI,
    SupervisorOpenAI,
    _cliente_base,
)
from app.models.schemas import Usuario
from app.retrieval.no_configurado import ModelosNoConfigurados, ProveedorNoConfiguradoError

PUBLIC = Usuario(id="ana", groups=["public"])


def _error(estado: int, body: dict | None = None, mensaje: str = "error") -> openai.APIStatusError:
    respuesta = httpx.Response(estado, request=httpx.Request("POST", "https://x/v1/chat"))
    clases = {
        400: openai.BadRequestError,
        401: openai.AuthenticationError,
        403: openai.PermissionDeniedError,
        404: openai.NotFoundError,
        429: openai.RateLimitError,
    }
    clase = clases.get(
        estado, openai.InternalServerError if estado >= 500 else openai.APIStatusError
    )
    return clase(mensaje, response=respuesta, body=body)


# ------------------------------------------------------------------ configuración
def test_por_defecto_azure_y_sin_endpoint_no_configurado() -> None:
    s = Settings()
    assert s.modelos_proveedor == "azure" and s.modelos_faltantes() == ["AZURE_OPENAI_ENDPOINT"]
    embedder, _, _ = build_modelos(s)
    assert isinstance(embedder, ModelosNoConfigurados)
    with pytest.raises(ProveedorNoConfiguradoError, match="MODELOS_PROVEEDOR=azure"):
        embedder.embed(["x"])


def test_openai_sin_clave_ni_url_no_configurado() -> None:
    s = Settings(modelos_proveedor="openai")
    assert s.modelos_faltantes() == ["OPENAI_API_KEY"]
    with pytest.raises(ProveedorNoConfiguradoError, match="OPENAI_API_KEY"):
        build_modelos(s)[1].responder("s", "u")


def test_openai_usa_sus_modelos_y_su_cliente() -> None:
    s = Settings(
        modelos_proveedor="openai", openai_api_key="sk-x", openai_chat_model="gpt-4.1",
        openai_embedding_model="text-embedding-3-small", modelos_max_reintentos=2,
    )  # fmt: skip
    assert (s.modelo_chat, s.modelo_embeddings) == ("gpt-4.1", "text-embedding-3-small")
    c = _cliente_base(s)
    assert type(c) is OpenAI and not isinstance(c, AzureOpenAI)
    assert str(c.base_url).startswith("https://api.openai.com") and c.max_retries == 2


def test_endpoint_compatible_sin_clave() -> None:
    """Ollama / vLLM locales: URL propia y sin clave (el SDK exige una: se pone un marcador)."""
    s = Settings(modelos_proveedor="openai", openai_base_url="http://localhost:11434/v1")
    assert s.modelos_faltantes() == []
    c = _cliente_base(s)
    assert str(c.base_url).startswith("http://localhost:11434") and c.api_key == SIN_CLAVE


def test_azure_sigue_usando_sus_deployments() -> None:
    s = Settings(azure_openai_endpoint="https://x.openai.azure.com/", azure_openai_api_key="k")
    assert s.modelo_chat == s.azure_openai_chat_deployment
    assert isinstance(_cliente_base(s), AzureOpenAI)


# ------------------------------------------------------------------ clasificación
@pytest.mark.parametrize(
    ("exc", "tipo"),
    [
        (_error(401, {"error": {"code": "invalid_api_key"}}), "credenciales_invalidas"),
        (_error(401, {"code": "401", "message": "Access denied due to invalid subscription key"}),
         "credenciales_invalidas"),
        (_error(403, {"code": "PermissionDenied"}), "sin_permiso"),
        (_error(404, {"code": "DeploymentNotFound"}), "modelo_no_encontrado"),
        (_error(404, {"error": {"code": "model_not_found"}}), "modelo_no_encontrado"),
        (_error(429, {"error": {"code": "insufficient_quota"}},
                "You exceeded your current quota, please check your plan and billing details."),
         "saldo_agotado"),
        (_error(402, {"error": {"message": "Insufficient credits"}}), "saldo_agotado"),
        (_error(403, {"code": "SubscriptionDisabled"}), "saldo_agotado"),
        (_error(429, {"code": "429"}, "Requests have exceeded call rate limit"),
         "limite_de_peticiones"),
        (_error(400, {"error": {"message": "tools is not supported in this model"}}),
         "capacidad_no_soportada"),
        (_error(400, {"code": "OperationNotSupported",
                      "message": "The embeddings operation does not work with the specified "
                                 "model"}), "capacidad_no_soportada"),
        (_error(400, {"error": {"message": "Unsupported value: 'temperature' does not support 0"}}),
         "capacidad_no_soportada"),
        (_error(400, {"error": {"code": "context_length_exceeded"}}), "contexto_excedido"),
        (_error(400, {"code": "content_filter"}), "filtro_contenido"),
        (_error(503), "servicio_no_disponible"),
        (openai.APIConnectionError(request=httpx.Request("POST", "https://x")), "conexion"),
        (openai.APITimeoutError(request=httpx.Request("POST", "https://x")), "conexion"),
        (ClientAuthenticationError("Please run 'az login'"), "credenciales_invalidas"),
        (_error(400, {"error": {"message": "algo raro"}}), "desconocido"),
    ],
)  # fmt: skip
def test_clasificacion_de_errores(exc, tipo) -> None:
    e = clasificar(exc, "azure", "gpt-4o")
    assert e.tipo == tipo and e.pista and e.mensaje_usuario


def test_mensaje_al_usuario_sin_detalles_internos() -> None:
    e = clasificar(_error(401, {"error": {"message": "key sk-secreta-123 invalid"}}), "openai", "m")
    assert "sk-secreta" not in e.mensaje_usuario and e.estado_http == 503
    assert "OPENAI_API_KEY" in e.pista and "az login" not in e.pista
    azure = clasificar(_error(401), "azure", "gpt-4o")
    assert "az login" in azure.pista


def test_capacidad_en_la_pista() -> None:
    e = clasificar(_error(400, {"error": {"message": "tools not supported"}}), "openai", "mini",
                   capacidad="tool_calling")  # fmt: skip
    assert "tool calling" in e.pista and "mini" in e.pista


# ------------------------------------------------------------------ adaptadores
def _cliente(fallo: Exception | None = None, vector_dims: int = 3):
    def lanzar(**_):
        raise fallo

    def embeddings(**_):
        if fallo:
            raise fallo
        return SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.1] * vector_dims)])

    completions = SimpleNamespace(create=lanzar, parse=lanzar)
    return SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        embeddings=SimpleNamespace(create=embeddings),
    )


def test_supervisor_traduce_tools_no_soportadas() -> None:
    sup = SupervisorOpenAI(_cliente(_error(400, {"error": {"message": "tools not supported"}})),
                           "phi-3", "openai")  # fmt: skip
    with pytest.raises(ModeloError) as e:
        sup.decidir([], [{"type": "function"}], obligar_herramienta=True)
    assert e.value.tipo == "capacidad_no_soportada" and e.value.capacidad == "tool_calling"


def test_llm_traduce_saldo_agotado() -> None:
    llm = LLMOpenAI(_cliente(_error(429, {"error": {"code": "insufficient_quota"}})), "gpt-4o")
    with pytest.raises(ModeloError) as e:
        llm.responder("s", "u")
    assert e.value.tipo == "saldo_agotado" and e.value.capacidad == "salida_estructurada"


def test_embeddings_con_otra_dimension_que_el_indice() -> None:
    emb = EmbedderOpenAI(_cliente(vector_dims=3072), "text-embedding-3-large", "openai", 1536)
    with pytest.raises(ModeloError) as e:
        emb.embed(["hola"])
    assert e.value.tipo == "capacidad_no_soportada" and "3072" in e.value.detalle
    assert "EMBEDDING_DIMENSIONS" in e.value.pista and "reindexa" in e.value.pista
    assert EmbedderOpenAI(_cliente(vector_dims=1536), "m", "openai", 1536).embed(["x"])


def test_filtro_de_contenido_tipificado_sigue_siendo_bloqueo(crear_agente) -> None:
    filtro = clasificar(_error(400, {"code": "content_filter"}), "azure", "gpt-4o")
    assert es_filtro_de_contenido(filtro)

    class Supervisor:
        def decidir(self, *a, **k):
            raise filtro

    r = crear_agente(supervisor=Supervisor()).consultar_detallado("hola", PUBLIC)
    assert r.respuesta.sin_contexto and any(h.accion == "bloquear" for h in r.hallazgos)


# ------------------------------------------------------------------ API y auditoría
class _SupervisorSinSaldo:
    def decidir(self, *a, **k):
        raise clasificar(_error(429, {"error": {"code": "insufficient_quota"}}), "openai", "gpt-4o")


def test_api_responde_con_el_tipo_de_error_y_audita(crear_agente, caplog) -> None:
    agente = crear_agente(supervisor=_SupervisorSinSaldo())
    app.dependency_overrides[get_agente] = lambda: agente
    from app.api.dependencias import get_servicios

    app.dependency_overrides[get_servicios] = lambda: SimpleNamespace(
        roles=SimpleNamespace(solo_activos=lambda u: u),
        departamentos=SimpleNamespace(de=lambda _: []),
    )
    try:
        with caplog.at_level(logging.INFO):
            r = TestClient(app).post("/consultar", json={"pregunta": "mi email es a@b.com"})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 503
    assert r.json() == {
        "detail": "El servicio de IA no tiene saldo o cuota disponible. Avisa al administrador.",
        "codigo": "saldo_agotado",
    }
    [registro] = [x for x in caplog.records if x.name == "audit"]
    auditado = json.loads(registro.getMessage())
    assert auditado["hallazgos"][0]["detalle"].startswith("modelo:saldo_agotado")
    assert "a@b.com" not in registro.getMessage()  # PII enmascarada también aquí
    assert any("facturación" in x.getMessage() for x in caplog.records if x.name == "app.main")


# ------------------------------------------------------------------ diagnóstico
def _az(returncode: int, cuenta: dict | None = None):
    def ejecutar(*_a, **_k):
        return subprocess.CompletedProcess([], returncode, json.dumps(cuenta or {}), "")

    return ejecutar


AZURE = {"azure_openai_endpoint": "https://x.openai.azure.com/"}


def _estado(comprobaciones, nombre):
    return next(c for c in comprobaciones if c.nombre == nombre)


def test_diagnostico_azure_sin_cli(monkeypatch) -> None:
    monkeypatch.delenv("IDENTITY_ENDPOINT", raising=False)
    c = requisitos_azure(Settings(**AZURE), ejecutar=_az(0), buscar=lambda _: None)
    cli = _estado(c, "Azure CLI")
    assert cli.estado == "error" and "az login" in cli.pista


def test_diagnostico_azure_sin_login(monkeypatch) -> None:
    monkeypatch.delenv("IDENTITY_ENDPOINT", raising=False)
    c = requisitos_azure(Settings(**AZURE), ejecutar=_az(1), buscar=lambda _: "/usr/bin/az")
    assert _estado(c, "Sesión (az login)").estado == "error"


@pytest.mark.parametrize(("estado", "esperado"), [("Enabled", "ok"), ("Disabled", "error"),
                                                  ("Warned", "aviso")])  # fmt: skip
def test_diagnostico_azure_suscripcion(monkeypatch, estado, esperado) -> None:
    monkeypatch.delenv("IDENTITY_ENDPOINT", raising=False)
    cuenta = {"name": "Azure for Students", "id": "s1", "state": estado, "user": {"name": "yo"}}
    c = requisitos_azure(Settings(**AZURE), ejecutar=_az(0, cuenta), buscar=lambda _: "az")
    assert _estado(c, "Suscripción").estado == esperado


def test_diagnostico_azure_con_clave_no_necesita_cli() -> None:
    c = requisitos_azure(Settings(**AZURE, azure_openai_api_key="k"), buscar=lambda _: None)
    assert all(x.nombre != "Azure CLI" for x in c) and all(x.estado == "ok" for x in c)


def test_diagnostico_openai() -> None:
    assert _estado(requisitos_openai(Settings(modelos_proveedor="openai")), "Clave").estado == (
        "error"
    )
    local = requisitos_openai(Settings(modelos_proveedor="openai", openai_base_url="http://h/v1"))
    assert _estado(local, "Clave").estado == "aviso"


def test_diagnostico_no_llama_si_faltan_requisitos() -> None:
    d = diagnosticar(Settings(modelos_proveedor="openai"), llamadas=True)
    assert not d.ok and [c.nombre for c in d.comprobaciones][-1] == "Modelo de embeddings"
