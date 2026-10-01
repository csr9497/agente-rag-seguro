"""Clientes y adaptadores sobre el SDK `openai`, que sirve tanto para Azure OpenAI
(`AzureOpenAI`) como para OpenAI y endpoints compatibles (`OpenAI` con `base_url`).

Cada llamada traduce los errores del proveedor a `ModeloError` indicando la capacidad que se
estaba usando (tool calling, salida estructurada, embeddings).
"""

from typing import Any

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langsmith.wrappers import wrap_openai
from openai import AzureOpenAI, OpenAI

from app.config import Settings
from app.modelos.errores import ModeloError, Proveedor, traducir_errores
from app.models.schemas import DecisionSupervisor, RespuestaLLM, ToolCall

_SCOPE_COGNITIVE = "https://cognitiveservices.azure.com/.default"
# Endpoints compatibles sin autenticación (Ollama, vLLM local): el SDK exige una clave.
SIN_CLAVE = "sin-clave"


def build_client(settings: Settings) -> OpenAI:
    """Con trazas activas, cada llamada (tokens, coste, latencia) aparece en LangSmith."""
    cliente = _cliente_base(settings)
    return cliente if settings.trazas_modo == "apagado" else wrap_openai(cliente)


def _cliente_base(settings: Settings) -> OpenAI:
    comunes = {
        "max_retries": settings.modelos_max_reintentos,
        "timeout": settings.modelos_timeout_s,
    }
    if settings.modelos_proveedor == "openai":
        clave = settings.openai_api_key.get_secret_value() if settings.openai_api_key else None
        return OpenAI(
            base_url=settings.openai_base_url or None,  # None: https://api.openai.com/v1
            api_key=clave or SIN_CLAVE,
            **comunes,
        )
    # Azure: clave solo en local (desde Key Vault vía .env); sin clave, Entra ID
    # (az login en local, Managed Identity en Azure).
    if settings.azure_openai_api_key:
        return AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key.get_secret_value(),
            api_version=settings.azure_openai_api_version,
            **comunes,
        )
    return AzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        azure_ad_token_provider=get_bearer_token_provider(
            DefaultAzureCredential(), _SCOPE_COGNITIVE
        ),
        api_version=settings.azure_openai_api_version,
        **comunes,
    )


class EmbedderOpenAI:
    def __init__(
        self,
        client: OpenAI,
        modelo: str,
        proveedor: Proveedor = "azure",
        dimensiones: int | None = None,
    ) -> None:
        self._client = client
        self._modelo = modelo
        self._proveedor = proveedor
        self._dimensiones = dimensiones

    def embed(self, textos: list[str]) -> list[list[float]]:
        with traducir_errores(self._proveedor, self._modelo, "embeddings"):
            resp = self._client.embeddings.create(model=self._modelo, input=textos)
        vectores = [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
        dims = len(vectores[0]) if vectores else None
        if self._dimensiones and dims and dims != self._dimensiones:
            # Un índice creado con otra dimensión no admite estos vectores (ni la búsqueda).
            raise ModeloError(
                "capacidad_no_soportada",
                self._proveedor,
                self._modelo,
                f"devuelve {dims} dimensiones; EMBEDDING_DIMENSIONS={self._dimensiones}",
                "embeddings",
                pista=f"El modelo '{self._modelo}' genera vectores de {dims} dimensiones y el "
                f"índice usa {self._dimensiones}: ajusta EMBEDDING_DIMENSIONS (y reindexa) o "
                "usa un modelo de embeddings de esa dimensión.",
            )
        return vectores


class SupervisorOpenAI:
    def __init__(self, client: OpenAI, modelo: str, proveedor: Proveedor = "azure") -> None:
        self._client = client
        self._modelo = modelo
        self._proveedor = proveedor

    def decidir(
        self,
        mensajes: list[dict[str, Any]],
        herramientas: list[dict[str, Any]],
        obligar_herramienta: bool = False,
    ) -> DecisionSupervisor:
        with traducir_errores(self._proveedor, self._modelo, "tool_calling"):
            completion = self._client.chat.completions.create(
                model=self._modelo,
                messages=mensajes,  # type: ignore[arg-type]
                tools=herramientas,  # type: ignore[arg-type]
                tool_choice="required" if obligar_herramienta and herramientas else "auto",
                temperature=0,
            )
        mensaje = completion.choices[0].message
        return DecisionSupervisor(
            tool_calls=[
                ToolCall(id=tc.id, nombre=tc.function.name, argumentos=tc.function.arguments)
                for tc in mensaje.tool_calls or []
                if tc.type == "function"
            ],
            mensaje_asistente=mensaje.model_dump(exclude_none=True),
        )


class LLMOpenAI:
    def __init__(self, client: OpenAI, modelo: str, proveedor: Proveedor = "azure") -> None:
        self._client = client
        self._modelo = modelo
        self._proveedor = proveedor

    def responder(self, system: str, user: str) -> RespuestaLLM:
        with traducir_errores(self._proveedor, self._modelo, "salida_estructurada"):
            completion = self._client.chat.completions.parse(
                model=self._modelo,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format=RespuestaLLM,
                temperature=0,
            )
        mensaje = completion.choices[0].message
        if mensaje.parsed is None:
            # Rechazo del modelo o salida no parseable: tratamos como "sin contexto".
            return RespuestaLLM(
                respuesta="No puedo responder a esa pregunta con los documentos disponibles.",
                citas_usadas=[],
                encontrado=False,
            )
        return mensaje.parsed
