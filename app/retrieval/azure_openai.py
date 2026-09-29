from typing import Any

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langsmith.wrappers import wrap_openai
from openai import AzureOpenAI

from app.config import Settings
from app.models.schemas import DecisionSupervisor, RespuestaLLM, ToolCall

_SCOPE_COGNITIVE = "https://cognitiveservices.azure.com/.default"


def build_client(settings: Settings) -> AzureOpenAI:
    """Clave solo en local (desde Key Vault vía .env); en Azure, Managed Identity. Con trazas
    activas, cada llamada (tokens, coste, latencia) aparece en LangSmith."""
    cliente = _cliente_base(settings)
    return cliente if settings.trazas_modo == "apagado" else wrap_openai(cliente)


def _cliente_base(settings: Settings) -> AzureOpenAI:
    if settings.azure_openai_api_key:
        return AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key.get_secret_value(),
            api_version=settings.azure_openai_api_version,
            max_retries=settings.azure_openai_max_reintentos,
            timeout=settings.azure_openai_timeout_s,
        )
    return AzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        azure_ad_token_provider=get_bearer_token_provider(
            DefaultAzureCredential(), _SCOPE_COGNITIVE
        ),
        api_version=settings.azure_openai_api_version,
        max_retries=settings.azure_openai_max_reintentos,
        timeout=settings.azure_openai_timeout_s,
    )


class AzureOpenAIEmbedder:
    def __init__(self, client: AzureOpenAI, deployment: str) -> None:
        self._client = client
        self._deployment = deployment

    def embed(self, textos: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self._deployment, input=textos)
        return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]


class AzureOpenAISupervisor:
    def __init__(self, client: AzureOpenAI, deployment: str) -> None:
        self._client = client
        self._deployment = deployment

    def decidir(
        self,
        mensajes: list[dict[str, Any]],
        herramientas: list[dict[str, Any]],
        obligar_herramienta: bool = False,
    ) -> DecisionSupervisor:
        completion = self._client.chat.completions.create(
            model=self._deployment,
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


class AzureOpenAILLM:
    def __init__(self, client: AzureOpenAI, deployment: str) -> None:
        self._client = client
        self._deployment = deployment

    def responder(self, system: str, user: str) -> RespuestaLLM:
        completion = self._client.chat.completions.parse(
            model=self._deployment,
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
