"""Guardrail de políticas de uso (versión v3-politicas).

Añade a los guardrails heurísticos (inyección, texto oculto, PII):

- Daño a personas y autolesión: se bloquea con un mensaje que orienta a quién acudir.
- Acoso y lenguaje ofensivo contra otras personas (código de conducta).
- Datos sensibles sin formato fijo (número de cuenta, contraseña, PIN, CVV, tarjeta): se
  ocultan antes de guardar nada; si el mensaje solo comparte el dato, se corta con un aviso.

Las reglas buscan la INTENCIÓN (primera persona, «cómo puedo…») para no bloquear preguntas
legítimas («¿cómo denuncio un acoso?»). Son deterministas y rápidas; lo que no cubren lo
complementan Prompt Shields (v4) y el filtro de contenido del proveedor.
"""

import re
import unicodedata

from app.models.schemas import Hallazgo
from app.security.guardrails import Guardrail, GuardrailEntrada, GuardrailSalida, Veredicto

MENSAJES: dict[str, str] = {
    "dano_a_personas": (
        "No puedo ayudarte con eso. Si hay una situación de riesgo o un conflicto en el "
        "trabajo, comunícalo a tu responsable, a RRHH o al canal ético del portal de empleado; "
        "si hay peligro inmediato, contacta con los servicios de emergencia."
    ),
    "autolesion": (
        "Siento que estés pasando por esto. Si estás en peligro, contacta ahora con los "
        "servicios de emergencia de tu país. También puedes hablar, de forma confidencial, con "
        "RRHH o con el servicio de salud laboral."
    ),
    "acoso": (
        "Este mensaje va contra el código de conducta, que exige respeto hacia compañeros, "
        "clientes y proveedores. Si estás viviendo un conflicto o una situación de acoso, "
        "puedes comunicarlo de forma anónima en el canal ético del portal de empleado."
    ),
    "dato_sensible": (
        "Por seguridad, no compartas datos bancarios, contraseñas ni claves en el chat. Hemos "
        "ocultado el dato y no se ha guardado. Si necesitas actualizar tus datos bancarios, "
        "hazlo desde el portal de empleado."
    ),
}

# Intención en primera persona o petición de ayuda ("quiero", "cómo puedo"…).
_INTENCION = r"\b(quiero|voy a|pienso|pensando en|me gustaria|como puedo|como|ayudame a)\s+"
_CLITICO = r"(?:(?:lo|la|le|los|las|les|me|te)\s+|a alguien\s+)?"

_PATRONES: dict[str, re.Pattern[str]] = {
    "autolesion": re.compile(
        r"\b(suicid\w*|quitarme la vida|matarme|hacerme dano|autolesion\w*|"
        r"no quiero seguir viviendo|acabar con mi vida)\b"
    ),
    "dano_a_personas": re.compile(
        _INTENCION
        + _CLITICO
        + r"(hacer(?:le|les)? dano|danar|herir|matar|golpear|lastimar|agredir|atacar|"
        r"envenenar|apunalar|disparar)\b"
    ),
    "acoso": re.compile(
        _INTENCION + _CLITICO + r"(humill|acos[ae]|acosar|intimid|ridiculiz|insult|discrimin)\w*"
        r"|\b(eres|es|son|sois) (un |una )?(inutil|idiota|imbecil|estupid[oa]|subnormal|"
        r"gilipollas)\b"
    ),
}

# Datos sensibles sin formato fijo: se oculta el VALOR (grupo "v"), no la etiqueta.
_SENSIBLES: dict[str, re.Pattern[str]] = {
    "cuenta_bancaria": re.compile(
        r"\b(numero de cuenta|n[o°º]\.? de cuenta|cuenta bancaria|cuenta corriente|cuenta|"
        r"cbu|cci|clabe)\b\s*(?:es|:|=)?\s*(?P<v>\d[\d\s-]{4,}\d)"
    ),
    "credencial": re.compile(
        r"\b(contrasena|password|clave|pin|cvv|cvc|token)\b\s*(?:es|:|=)?\s*(?P<v>[^\s,;]{3,})"
    ),
    "tarjeta": re.compile(r"\btarjeta\b\D{0,20}(?P<v>\d[\d\s-]{10,}\d)"),
}

_PREGUNTA = re.compile(
    r"\?|^\s*(como|cuando|donde|que|cual|cuanto|cuantos|cuantas|quien|por que|puedo|"
    r"necesito|quiero saber|dime)\b"
)


def _plano(texto: str) -> str:
    """Minúsculas y sin tildes, con la MISMA longitud que el original (posiciones válidas)."""
    return "".join(unicodedata.normalize("NFD", c)[0].lower() for c in texto)


def detectar_politicas(texto: str) -> list[str]:
    plano = _plano(texto)
    return [tipo for tipo, patron in _PATRONES.items() if patron.search(plano)]


def enmascarar_datos_sensibles(texto: str) -> tuple[str, list[str]]:
    """Oculta los valores sensibles; devuelve (texto, tipos encontrados)."""
    tramos: list[tuple[int, int, str]] = []
    plano = _plano(texto)
    for tipo, patron in _SENSIBLES.items():
        for m in patron.finditer(plano):
            if not any(a <= m.start("v") < b for a, b, _ in tramos):
                tramos.append((m.start("v"), m.end("v"), tipo))
    for inicio, fin, _ in sorted(tramos, reverse=True):
        texto = texto[:inicio] + "[DATO_SENSIBLE]" + texto[fin:]
    return texto, sorted({t for *_, t in tramos})


class GuardrailPoliticas:
    """v3-politicas: v1 (inyección, texto oculto, PII) + políticas de uso y datos sensibles."""

    def __init__(self, base: Guardrail | None = None) -> None:
        self._base = base or GuardrailEntrada()

    def revisar(self, texto: str) -> Veredicto:
        texto_seguro, sensibles = enmascarar_datos_sensibles(texto)
        hallazgos_sensibles = [
            Hallazgo(tipo="dato_sensible", detalle=t, accion="enmascarar") for t in sensibles
        ]
        veredicto = self._base.revisar(texto_seguro)
        hallazgos = [*hallazgos_sensibles, *veredicto.hallazgos]
        if not veredicto.permitido:
            return veredicto.model_copy(update={"hallazgos": hallazgos})

        if politicas := detectar_politicas(texto):
            tipo = politicas[0]  # la más grave primero (orden de _PATRONES)
            hallazgos += [Hallazgo(tipo=t, detalle="politica_de_uso", accion="bloquear")
                          for t in politicas]  # fmt: skip
            return Veredicto(
                permitido=False, texto=veredicto.texto, hallazgos=hallazgos, mensaje=MENSAJES[tipo]
            )

        if sensibles and not _PREGUNTA.search(_plano(texto_seguro)):
            # Solo comparte el dato: no hay nada que responder salvo el aviso.
            hallazgos.append(Hallazgo(tipo="dato_sensible", detalle="aviso", accion="bloquear"))
            return Veredicto(
                permitido=False,
                texto=veredicto.texto,
                hallazgos=hallazgos,
                mensaje=MENSAJES["dato_sensible"],
            )
        return veredicto.model_copy(update={"hallazgos": hallazgos})


class GuardrailSalidaSensibles(GuardrailSalida):
    """v2-fuga-sensibles: v1 (fugas del prompt, etiquetas, PII) + datos sensibles."""

    def revisar(self, texto: str) -> Veredicto:
        veredicto = super().revisar(texto)
        if not veredicto.permitido:
            return veredicto
        limpio, sensibles = enmascarar_datos_sensibles(veredicto.texto)
        extra = [Hallazgo(tipo="dato_sensible", detalle=t, accion="enmascarar") for t in sensibles]
        return veredicto.model_copy(
            update={"texto": limpio, "hallazgos": [*veredicto.hallazgos, *extra]}
        )
