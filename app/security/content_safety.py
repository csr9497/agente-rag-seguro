"""Azure AI Content Safety — Prompt Shields, detrás de la interfaz `Guardrail`.

Se suma a los guardrails locales, no los sustituye: primero se aplica el guardrail local
(heurísticas deterministas, PII enmascarada) y, si deja pasar el texto, se consulta Prompt
Shields con el texto ya saneado, de modo que la PII no sale de la app. Prompt Shields detecta
ataques directos (jailbreak en la pregunta) e indirectos (instrucciones incrustadas en
documentos), por eso también se usa en la ingesta.

Si el servicio falla, `fallo="cerrado"` (por defecto) bloquea y `fallo="abierto"` deja pasar
con un hallazgo registrado en la auditoría.
"""

from collections.abc import Callable
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, Field

from app.models.schemas import Hallazgo
from app.security.guardrails import Guardrail, Veredicto

API_VERSION = "2024-09-01"
SCOPE = "https://cognitiveservices.azure.com/.default"
# Límites del servicio: 10 000 caracteres por petición (pregunta + documentos) y 5 documentos.
MAX_CARACTERES = 10_000
MAX_DOCUMENTOS = 5


class AnalisisShields(BaseModel):
    ataque_en_prompt: bool = False
    ataque_en_documentos: list[bool] = Field(default_factory=list)

    @property
    def ataque(self) -> bool:
        return self.ataque_en_prompt or any(self.ataque_en_documentos)


class ContentSafetyError(RuntimeError):
    pass


class ClienteShields(Protocol):
    def analizar(self, prompt: str, documentos: list[str] | None = None) -> AnalisisShields: ...


class ClientePromptShields:
    """Cliente REST de `text:shieldPrompt`. Autentica con clave (solo local) o con un token de
    Entra ID (Managed Identity en Azure) obtenido por `obtener_token`."""

    def __init__(
        self,
        endpoint: str,
        *,
        api_key: str | None = None,
        obtener_token: Callable[[], str] | None = None,
        timeout: float = 5.0,
        transporte: httpx.BaseTransport | None = None,
    ) -> None:
        if not (api_key or obtener_token):
            raise ValueError("Content Safety necesita api_key u obtener_token")
        self._url = f"{endpoint.rstrip('/')}/contentsafety/text:shieldPrompt"
        self._api_key = api_key
        self._obtener_token = obtener_token
        self._http = httpx.Client(timeout=timeout, transport=transporte)

    def _cabeceras(self) -> dict[str, str]:
        if self._api_key:
            return {"Ocp-Apim-Subscription-Key": self._api_key}
        return {"Authorization": f"Bearer {self._obtener_token()}"}  # type: ignore[misc]

    def analizar(self, prompt: str, documentos: list[str] | None = None) -> AnalisisShields:
        documentos = documentos or []
        if len(documentos) > MAX_DOCUMENTOS:
            raise ContentSafetyError(f"máximo {MAX_DOCUMENTOS} documentos por petición")
        if len(prompt) + sum(map(len, documentos)) > MAX_CARACTERES:
            raise ContentSafetyError(f"máximo {MAX_CARACTERES} caracteres por petición")
        try:
            r = self._http.post(
                self._url,
                params={"api-version": API_VERSION},
                headers=self._cabeceras(),
                json={"userPrompt": prompt, "documents": documentos},
            )
            r.raise_for_status()
            cuerpo = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ContentSafetyError(f"Prompt Shields no disponible: {type(e).__name__}") from e
        return AnalisisShields(
            ataque_en_prompt=bool(cuerpo.get("userPromptAnalysis", {}).get("attackDetected")),
            ataque_en_documentos=[
                bool(d.get("attackDetected")) for d in cuerpo.get("documentsAnalysis", [])
            ],
        )


def _trocear(texto: str, tam: int) -> list[str]:
    return [texto[i : i + tam] for i in range(0, len(texto), tam)] or [""]


class GuardrailPromptShields:
    """Envuelve un guardrail local y añade Prompt Shields sobre el texto que este deja pasar."""

    def __init__(
        self,
        base: Guardrail,
        cliente: ClienteShields,
        fallo: Literal["cerrado", "abierto"] = "cerrado",
    ) -> None:
        self._base, self._cliente, self._fallo = base, cliente, fallo

    def revisar(self, texto: str) -> Veredicto:
        veredicto = self._base.revisar(texto)
        if not veredicto.permitido:
            return veredicto  # ya bloqueado en local: no se envía nada al servicio
        try:
            ataque = any(
                self._cliente.analizar(trozo).ataque
                for trozo in _trocear(veredicto.texto, MAX_CARACTERES)
            )
        except ContentSafetyError as e:
            return _por_fallo(veredicto, self._fallo, str(e))
        if not ataque:
            return veredicto
        hallazgo = Hallazgo(tipo="inyeccion", detalle="prompt_shields", accion="bloquear")
        return veredicto.model_copy(
            update={"permitido": False, "hallazgos": [*veredicto.hallazgos, hallazgo]}
        )


def _por_fallo(veredicto: Veredicto, fallo: str, detalle: str) -> Veredicto:
    accion = "bloquear" if fallo == "cerrado" else "registrar"
    hallazgo = Hallazgo(tipo="servicio_no_disponible", detalle=detalle, accion=accion)
    return veredicto.model_copy(
        update={
            "permitido": fallo == "abierto",
            "hallazgos": [*veredicto.hallazgos, hallazgo],
        }
    )


def detectar_ataque_en_documento(cliente: ClienteShields, texto: str) -> bool:
    """Ataque indirecto (instrucciones incrustadas) en el texto de un documento a indexar.
    Se envía en lotes de hasta 5 trozos que respetan el límite de caracteres por petición."""
    tam = MAX_CARACTERES // MAX_DOCUMENTOS
    trozos = _trocear(texto, tam)
    return any(
        cliente.analizar("", trozos[i : i + MAX_DOCUMENTOS]).ataque
        for i in range(0, len(trozos), MAX_DOCUMENTOS)
    )
