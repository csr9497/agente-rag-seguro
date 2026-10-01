"""Errores del proveedor de modelos, tipificados.

Cada fallo del SDK (`openai`, también para Azure) o de la credencial de Azure se traduce a un
`ModeloError` con un `tipo` estable. La API responde con un mensaje para el usuario (sin
detalles internos) y el log guarda el detalle y la pista para quien opera el servicio.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

import openai
from azure.core.exceptions import ClientAuthenticationError

Proveedor = Literal["azure", "openai"]
Capacidad = Literal["chat", "tool_calling", "salida_estructurada", "embeddings"]
TipoError = Literal[
    "credenciales_invalidas",
    "sin_permiso",
    "modelo_no_encontrado",
    "saldo_agotado",
    "limite_de_peticiones",
    "capacidad_no_soportada",
    "contexto_excedido",
    "filtro_contenido",
    "conexion",
    "servicio_no_disponible",
    "desconocido",
]

CAPACIDADES: dict[Capacidad, str] = {
    "chat": "chat completions",
    "tool_calling": "llamadas a herramientas (tool calling), que usa el supervisor",
    "salida_estructurada": "salida estructurada (JSON schema), que usa la generación",
    "embeddings": "embeddings, que usan la búsqueda y la ingesta",
}

# Mensaje para el usuario final: sin nombres de variables ni detalles del proveedor.
MENSAJES_USUARIO: dict[TipoError, str] = {
    "credenciales_invalidas": "El servicio de IA rechazó las credenciales. Avisa al administrador.",
    "sin_permiso": "El servicio no puede usar el modelo de IA (permisos). Avisa al administrador.",
    "modelo_no_encontrado": "El modelo de IA configurado no existe. Avisa al administrador.",
    "saldo_agotado": "El servicio de IA no tiene saldo o cuota disponible. Avisa al administrador.",
    "limite_de_peticiones": "El servicio de IA está saturado. Vuelve a intentarlo en un momento.",
    "capacidad_no_soportada": "El modelo de IA configurado no admite una función necesaria. "
    "Avisa al administrador.",
    "contexto_excedido": "La consulta es demasiado larga para el modelo. Acórtala o divídela.",
    "filtro_contenido": "El proveedor de IA bloqueó la consulta por su política de contenido.",
    "conexion": "No se puede conectar con el servicio de IA. Vuelve a intentarlo.",
    "servicio_no_disponible": "El servicio de IA no está disponible. Vuelve a intentarlo.",
    "desconocido": "Error del proveedor de IA.",
}

ESTADO_HTTP: dict[TipoError, int] = {
    "limite_de_peticiones": 429,
    "contexto_excedido": 422,
    "filtro_contenido": 422,
    "servicio_no_disponible": 502,
    "desconocido": 502,
}  # el resto (configuración, saldo, conexión): 503


class ModeloError(Exception):
    def __init__(
        self,
        tipo: TipoError,
        proveedor: Proveedor,
        modelo: str,
        detalle: str,
        capacidad: Capacidad | None = None,
        pista: str | None = None,
    ) -> None:
        self._pista = pista
        self.tipo = tipo
        self.proveedor = proveedor
        self.modelo = modelo
        self.detalle = detalle[:300]
        self.capacidad = capacidad
        super().__init__(f"[{tipo}] {proveedor}/{modelo}: {self.detalle}")

    @property
    def mensaje_usuario(self) -> str:
        return MENSAJES_USUARIO[self.tipo]

    @property
    def estado_http(self) -> int:
        return ESTADO_HTTP.get(self.tipo, 503)

    @property
    def pista(self) -> str:
        """Qué revisar (para el log y el diagnóstico, no para el usuario final)."""
        return self._pista or pista(self.tipo, self.proveedor, self.modelo, self.capacidad)


def pista(
    tipo: TipoError, proveedor: Proveedor, modelo: str, capacidad: Capacidad | None = None
) -> str:
    azure = proveedor == "azure"
    match tipo:
        case "credenciales_invalidas":
            return (
                "Revisa AZURE_OPENAI_API_KEY o, sin clave, ejecuta `az login` (local) o la "
                "Managed Identity (Azure)."
                if azure
                else "Revisa OPENAI_API_KEY (y OPENAI_BASE_URL si no es OpenAI)."
            )
        case "sin_permiso":
            return (
                "Tu identidad necesita el rol «Cognitive Services OpenAI User» sobre el recurso; "
                "si el recurso no es público, revisa la red (firewall / private endpoint)."
                if azure
                else "La clave no tiene acceso a ese modelo (proyecto u organización)."
            )
        case "modelo_no_encontrado":
            return (
                f"No existe el deployment '{modelo}': AZURE_OPENAI_*_DEPLOYMENT es el nombre del "
                "deployment (no del modelo); revisa también AZURE_OPENAI_ENDPOINT y "
                "AZURE_OPENAI_API_VERSION, o despliega los modelos (make levantar)."
                if azure
                else f"El modelo '{modelo}' no existe en el endpoint o la cuenta no tiene acceso:"
                " revisa OPENAI_CHAT_MODEL / OPENAI_EMBEDDING_MODEL / OPENAI_LIGERO_MODEL."
            )
        case "saldo_agotado":
            return (
                "Crédito o cuota agotados: revisa que la suscripción esté activa (Azure for "
                "Students se deshabilita al acabar el crédito) y la cuota TPM del deployment."
                if azure
                else "Sin saldo: revisa la facturación o los créditos de la cuenta del proveedor."
            )
        case "limite_de_peticiones":
            return "Límite de peticiones o tokens por minuto: espera o aumenta la cuota (TPM)."
        case "capacidad_no_soportada":
            que = CAPACIDADES[capacidad] if capacidad else "una función que pide el código"
            return (
                f"El modelo '{modelo}' no admite {que}. Usa un modelo que la soporte "
                "(p. ej. gpt-4o o gpt-4.1-mini para chat; text-embedding-3-small o ada-002 "
                "para embeddings) o revisa la versión de la API."
            )
        case "contexto_excedido":
            return "El prompt supera la ventana de contexto: reduce RETRIEVAL_TOP_K o el historial."
        case "filtro_contenido":
            return "El filtro de contenido del proveedor rechazó la petición."
        case "conexion":
            return (
                "No hay conexión con el endpoint: revisa la URL "
                + ("(AZURE_OPENAI_ENDPOINT)" if azure else "(OPENAI_BASE_URL)")
                + ", la red y el proxy."
            )
        case "servicio_no_disponible":
            return "El proveedor devolvió un error interno: reintenta o revisa su página de estado."
        case _:
            return "Error no clasificado: revisa el detalle en el log."


_SALDO = (
    "insufficient_quota",
    "exceeded your current quota",
    "billing",
    "credit",
    "payment",
    "subscriptiondisabled",
    "subscription is disabled",
    "subscription has been disabled",
)
_NO_SOPORTADO = (
    "operationnotsupported",
    "not supported",
    "unsupported",
    "does not support",
    "is not available for",
    "does not work with the specified model",
    "invalid_request_error: tools",
)
_CONTEXTO = ("context_length_exceeded", "maximum context length", "too many tokens")


def _codigo_y_mensaje(exc: openai.APIStatusError) -> str:
    """Código y mensaje del cuerpo (OpenAI: {"error": {"code", "message"}}; Azure: {"code",
    "message"}): el texto de la excepción no siempre los incluye."""
    cuerpo = exc.body if isinstance(exc.body, dict) else {}
    interno = cuerpo.get("error") if isinstance(cuerpo.get("error"), dict) else cuerpo
    partes = [getattr(exc, "code", None), interno.get("code"), interno.get("type")]
    return " ".join(str(p) for p in [*partes, interno.get("message")] if p)


def clasificar(
    exc: Exception, proveedor: Proveedor, modelo: str, capacidad: Capacidad | None = None
) -> ModeloError:
    if isinstance(exc, ModeloError):
        return exc
    cuerpo = _codigo_y_mensaje(exc) if isinstance(exc, openai.APIStatusError) else ""
    texto = f"{cuerpo} {exc}".lower()

    def error(tipo: TipoError) -> ModeloError:
        detalle = f"{type(exc).__name__}: {exc} {cuerpo}".strip()
        return ModeloError(tipo, proveedor, modelo, detalle, capacidad)

    if isinstance(exc, ClientAuthenticationError):  # sin az login / Managed Identity
        return error("credenciales_invalidas")
    if isinstance(exc, openai.APITimeoutError | openai.APIConnectionError):
        return error("conexion")
    if not isinstance(exc, openai.APIStatusError):
        return error("desconocido")
    estado = exc.status_code
    if "content_filter" in texto:
        return error("filtro_contenido")
    if estado == 402 or any(s in texto for s in _SALDO):
        return error("saldo_agotado")
    if estado == 401:
        return error("credenciales_invalidas")
    if estado == 403:
        return error("sin_permiso")
    if estado == 404 or "deploymentnotfound" in texto or "model_not_found" in texto:
        return error("modelo_no_encontrado")
    if estado == 429:
        return error("limite_de_peticiones")
    if any(s in texto for s in _CONTEXTO):
        return error("contexto_excedido")
    if estado in (400, 422) and any(s in texto for s in _NO_SOPORTADO):
        return error("capacidad_no_soportada")
    if estado >= 500:
        return error("servicio_no_disponible")
    return error("desconocido")


@contextmanager
def traducir_errores(
    proveedor: Proveedor, modelo: str, capacidad: Capacidad | None = None
) -> Iterator[None]:
    """Convierte cualquier error del SDK o de la credencial en ModeloError."""
    try:
        yield
    except (openai.APIError, ClientAuthenticationError) as exc:
        raise clasificar(exc, proveedor, modelo, capacidad) from exc


def es_filtro_de_contenido(exc: Exception) -> bool:
    if isinstance(exc, ModeloError):
        return exc.tipo == "filtro_contenido"
    return isinstance(exc, openai.BadRequestError) and (
        getattr(exc, "code", None) == "content_filter" or "content_filter" in str(exc)
    )
