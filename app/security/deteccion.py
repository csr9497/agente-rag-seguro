"""Detectores compartidos por la ingesta y los guardrails: inyección de prompt, texto oculto
y datos personales (PII). Heurísticos y deterministas; en Azure se complementan con
Content Safety (Prompt Shields) detrás de la misma interfaz de guardrails."""

import re
from collections.abc import Callable
from typing import Literal

# Rangos de caracteres invisibles: zero-width (200B-200F), controles bidi (202A-202E,
# 2066-2069), word joiner e invisibles matemáticos (2060-2064), BOM (FEFF) y el bloque
# "tags" de Unicode (E0000-E007F). Se construye desde códigos para que el fuente sea ASCII.
_RANGOS_INVISIBLES = [
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x2064),
    (0x2066, 0x2069),
    (0xFEFF, 0xFEFF),
    (0xE0000, 0xE007F),
]
INVISIBLES = re.compile(
    "[" + "".join(f"{re.escape(chr(a))}-{re.escape(chr(b))}" for a, b in _RANGOS_INVISIBLES) + "]"
)
# Controles C0/C1 salvo tabulador y saltos de línea.
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

PATRONES_INYECCION: dict[str, re.Pattern[str]] = {
    nombre: re.compile(patron, re.IGNORECASE | re.MULTILINE)
    for nombre, patron in {
        "ignorar_instrucciones_es": (
            r"\b(ignora|olvida|omite|descarta)\b[^.\n]{0,40}\b(instrucciones|reglas|"
            r"indicaciones|directrices)\b"
        ),
        "ignorar_instrucciones_en": (
            r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(instructions|rules|"
            r"guidelines|prompts?)\b"
        ),
        "cambio_de_rol": (
            r"\b(ahora eres|a partir de ahora eres|actúa como|you are now|act as)\b[^.\n]{0,40}"
            r"\b(administrador|admin|root|sistema|system|desarrollador|developer|dan)\b"
        ),
        "prompt_de_sistema": (
            r"(\bsystem prompt\b|\bprompt del sistema\b|\bmensaje de sistema\b|"
            r"^\s*#{1,6}\s*(system|sistema)\s*:?\s*$)"
        ),
        "tokens_de_chat": r"(<\|im_(start|end)\|>|<\|endoftext\|>|\[/?INST\]|<</?SYS>>)",
        "exfiltracion": (
            r"\b(revela|muestra|imprime|reveal|print|show)\b[^.\n]{0,40}"
            r"\b(tus instrucciones|your instructions|system prompt|contraseñas?|passwords?|"
            r"api[ _-]?keys?|claves?)\b"
        ),
    }.items()
}


def detectar_inyeccion(texto: str) -> list[str]:
    """Nombres de los patrones de inyección de prompt presentes en el texto."""
    return [n for n, p in PATRONES_INYECCION.items() if p.search(texto)]


# ---------------------------------------------------------------------------------- PII

TipoPII = Literal["email", "telefono", "iban", "tarjeta", "dni_es"]
TIPOS_PII: tuple[TipoPII, ...] = ("email", "telefono", "iban", "tarjeta", "dni_es")

_LETRAS_DNI = "TRWAGMYFPDXBNJZSQVHLCKE"


def _solo_alnum(s: str) -> str:
    return re.sub(r"[\s.-]", "", s)


def _luhn(numero: str) -> bool:
    digitos = [int(d) for d in numero][::-1]
    total = sum(
        d if i % 2 == 0 else (d * 2 - 9 if d * 2 > 9 else d * 2) for i, d in enumerate(digitos)
    )
    return total % 10 == 0


def _iban_valido(iban: str) -> bool:
    iban = iban.upper()
    reordenado = iban[4:] + iban[:4]
    return 15 <= len(iban) <= 34 and int("".join(str(int(c, 36)) for c in reordenado)) % 97 == 1


def _dni_valido(doc: str) -> bool:
    doc = doc.upper()
    numero = doc[:-1].replace("X", "0").replace("Y", "1").replace("Z", "2")
    return numero.isdigit() and _LETRAS_DNI[int(numero) % 23] == doc[-1]


_PII: dict[TipoPII, tuple[re.Pattern[str], Callable[[str], bool]]] = {
    "email": (re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), lambda _: True),
    "iban": (
        re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?\b"),
        lambda m: _iban_valido(_solo_alnum(m)),
    ),
    "tarjeta": (
        re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"),
        lambda m: 13 <= len(_solo_alnum(m)) <= 19 and _luhn(_solo_alnum(m)),
    ),
    "dni_es": (re.compile(r"\b[XYZ]?\d{7,8}[A-Z]\b", re.IGNORECASE), _dni_valido),
    "telefono": (
        re.compile(r"(?<![\w+])(?:\+\d{1,3}[ .-]?)?[6789]\d{2}[ .-]?\d{3}[ .-]?\d{3}(?!\d)"),
        lambda _: True,
    ),
}


def enmascarar_pii(
    texto: str, tipos: tuple[TipoPII, ...] | list[TipoPII]
) -> tuple[str, list[TipoPII]]:
    """Sustituye la PII por marcadores [TIPO] y devuelve (texto, tipos encontrados).

    El orden importa: IBAN y tarjeta antes que teléfono, para no enmascarar a medias."""
    encontrados: list[TipoPII] = []
    for tipo, (patron, valido) in _PII.items():
        if tipo not in tipos:
            continue

        def _sustituir(m: re.Match[str], tipo: TipoPII = tipo, valido=valido) -> str:
            if not valido(m.group(0)):
                return m.group(0)
            if tipo not in encontrados:
                encontrados.append(tipo)
            return f"[{tipo.upper()}]"

        texto = patron.sub(_sustituir, texto)
    return texto, encontrados
