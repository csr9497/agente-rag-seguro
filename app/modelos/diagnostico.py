"""Diagnóstico del proveedor de modelos: requisitos y capacidades, antes de arrancar.

    uv run python -m app.modelos.diagnostico               # requisitos + llamadas de prueba
    uv run python -m app.modelos.diagnostico --sin-llamadas  # solo requisitos (sin coste)

Azure (MODELOS_PROVEEDOR=azure): endpoint, autenticación (clave, Managed Identity o Azure CLI
con sesión iniciada y suscripción activa) y nombres de los deployments.
OpenAI / compatible (MODELOS_PROVEEDOR=openai): clave o URL propia y nombres de los modelos.

Las llamadas de prueba comprueban lo que el código pide a cada modelo: chat, tool calling
(supervisor), salida estructurada (generación) y embeddings con la dimensión del índice.
Cada fallo se clasifica (credenciales, saldo, modelo inexistente, capacidad…) con qué revisar.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.modelos.errores import ModeloError, traducir_errores
from app.modelos.openai_compat import EmbedderOpenAI, LLMOpenAI, SupervisorOpenAI, build_client

Estado = Literal["ok", "error", "aviso", "omitida"]
ICONOS: dict[Estado, str] = {"ok": "✅", "error": "⛔", "aviso": "⚠️ ", "omitida": "⏭️ "}
# Tras estos fallos el resto de llamadas fallaría igual: se omiten.
AVISO = {"Warned", "PastDue"}
FATALES = {"credenciales_invalidas", "sin_permiso", "saldo_agotado", "conexion"}


class Comprobacion(BaseModel):
    nombre: str
    estado: Estado
    detalle: str = ""
    pista: str = Field(default="", description="Qué hacer si falla")


class Diagnostico(BaseModel):
    proveedor: str
    comprobaciones: list[Comprobacion]

    @property
    def ok(self) -> bool:
        return all(c.estado != "error" for c in self.comprobaciones)


# ------------------------------------------------------------------ requisitos
def _en_azure() -> bool:
    """Container Apps / App Service exponen el endpoint de la Managed Identity."""
    return bool(os.environ.get("IDENTITY_ENDPOINT") or os.environ.get("MSI_ENDPOINT"))


def requisitos_azure(
    settings: Settings,
    ejecutar: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    buscar: Callable[[str], str | None] = shutil.which,
) -> list[Comprobacion]:
    c: list[Comprobacion] = []
    if settings.azure_openai_endpoint:
        c.append(
            Comprobacion(nombre="Endpoint", estado="ok", detalle=settings.azure_openai_endpoint)
        )
    else:
        c.append(Comprobacion(
            nombre="Endpoint", estado="error", detalle="AZURE_OPENAI_ENDPOINT vacío",
            pista="Despliega los modelos (make up) o copia el endpoint del recurso de "
            "Azure OpenAI (portal → Keys and Endpoint) a .env",
        ))  # fmt: skip

    if settings.azure_openai_api_key:
        c.append(Comprobacion(
            nombre="Autenticación", estado="ok", detalle="clave (AZURE_OPENAI_API_KEY)",
            pista="Solo para local: en Azure se usa Managed Identity",
        ))  # fmt: skip
    elif _en_azure():
        c.append(Comprobacion(nombre="Autenticación", estado="ok", detalle="Managed Identity"))
    else:
        c += _azure_cli(ejecutar, buscar)

    for nombre, valor in [
        ("Deployment de chat", settings.azure_openai_chat_deployment),
        ("Deployment de embeddings", settings.azure_openai_embedding_deployment),
    ]:
        c.append(Comprobacion(
            nombre=nombre, estado="ok" if valor else "error", detalle=valor or "vacío",
            pista="Es el nombre del deployment en el recurso, no el del modelo",
        ))  # fmt: skip
    return c


def _azure_cli(
    ejecutar: Callable[..., subprocess.CompletedProcess], buscar: Callable[[str], str | None]
) -> list[Comprobacion]:
    """Sin clave en local, DefaultAzureCredential usa la sesión de Azure CLI."""
    az = buscar("az")
    if not az:
        return [Comprobacion(
            nombre="Azure CLI", estado="error", detalle="no instalado",
            pista="Instálalo (https://learn.microsoft.com/cli/azure/install-azure-cli) y "
            "ejecuta `az login`, o define AZURE_OPENAI_API_KEY",
        )]  # fmt: skip
    c = [Comprobacion(nombre="Azure CLI", estado="ok", detalle=az)]
    r = ejecutar([az, "account", "show", "-o", "json"], capture_output=True, text=True)
    if r.returncode != 0:
        return [*c, Comprobacion(
            nombre="Sesión (az login)", estado="error", detalle="sin sesión iniciada",
            pista="Ejecuta `az login` (y `az account set -s <suscripción>` si tienes varias)",
        )]  # fmt: skip
    cuenta = json.loads(r.stdout)
    usuario = cuenta.get("user", {}).get("name", "?")
    c.append(Comprobacion(nombre="Sesión (az login)", estado="ok", detalle=usuario))
    estado = cuenta.get("state", "?")
    suscripcion = f"{cuenta.get('name', '?')} ({cuenta.get('id', '?')}) · {estado}"
    c.append(Comprobacion(
        nombre="Suscripción",
        # Warned / PastDue: aún funciona, pero se deshabilitará si no se resuelve.
        estado="ok" if estado == "Enabled" else "aviso" if estado in AVISO else "error",
        detalle=suscripcion,
        pista="La suscripción no está activa: crédito agotado (Azure for Students), pago "
        "pendiente o deshabilitada. Revísala en el portal (Subscriptions)",
    ))  # fmt: skip
    c.append(Comprobacion(
        nombre="Permiso sobre el recurso", estado="aviso", detalle="no verificable sin llamar",
        pista="Tu usuario necesita el rol «Cognitive Services OpenAI User» en el recurso "
        "(Terraform lo asigna a developer_principal_ids)",
    ))  # fmt: skip
    return c


def requisitos_openai(settings: Settings) -> list[Comprobacion]:
    url = settings.openai_base_url or "https://api.openai.com/v1 (OpenAI)"
    c = [Comprobacion(nombre="Endpoint", estado="ok", detalle=url)]
    if settings.openai_api_key:
        c.append(Comprobacion(nombre="Clave", estado="ok", detalle="OPENAI_API_KEY definida"))
    elif settings.openai_base_url:
        c.append(Comprobacion(
            nombre="Clave", estado="aviso", detalle="sin OPENAI_API_KEY",
            pista="Válido para endpoints locales sin autenticación (Ollama, vLLM)",
        ))  # fmt: skip
    else:
        c.append(Comprobacion(
            nombre="Clave", estado="error", detalle="OPENAI_API_KEY vacía",
            pista="Crea una clave en el proveedor y ponla en .env (nunca en el repositorio)",
        ))  # fmt: skip
    for nombre, valor in [
        ("Modelo de chat", settings.openai_chat_model),
        ("Modelo de embeddings", settings.openai_embedding_model),
    ]:
        c.append(Comprobacion(nombre=nombre, estado="ok" if valor else "error", detalle=valor))
    return c


# ------------------------------------------------------------------ capacidades
_HERRAMIENTA = {
    "type": "function",
    "function": {
        "name": "buscar",
        "description": "Busca en los documentos",
        "parameters": {
            "type": "object",
            "properties": {"consulta": {"type": "string"}},
            "required": ["consulta"],
        },
    },
}


def pruebas_modelos(settings: Settings) -> list[Comprobacion]:
    # Sin reintentos largos: un 429 por saldo no debe dejar el diagnóstico esperando minutos.
    settings = settings.model_copy(update={"modelos_max_reintentos": 1, "trazas_modo": "apagado"})
    cliente = build_client(settings)
    proveedor, chat = settings.modelos_proveedor, settings.modelo_chat

    def basico() -> str:
        with traducir_errores(proveedor, chat, "chat"):
            r = cliente.chat.completions.create(
                model=chat,
                messages=[{"role": "user", "content": "Responde solo con la palabra OK."}],
                temperature=0,
            )
        return f"{r.model} · {r.usage.total_tokens if r.usage else '?'} tokens"

    def herramientas() -> str:
        d = SupervisorOpenAI(cliente, chat, proveedor).decidir(
            [{"role": "user", "content": "Busca la política de vacaciones."}],
            [_HERRAMIENTA],
            obligar_herramienta=True,
        )
        if not d.tool_calls:
            raise ModeloError(
                "capacidad_no_soportada", proveedor, chat,
                "no devolvió ninguna llamada con tool_choice=required", "tool_calling",
            )  # fmt: skip
        return f"llamó a {d.tool_calls[0].nombre}"

    def estructurada() -> str:
        r = LLMOpenAI(cliente, chat, proveedor).responder(
            "Responde en JSON según el esquema. encontrado=true.", "¿Cuánto es 2+2?"
        )
        return f"encontrado={r.encontrado}"

    def embeddings() -> str:
        modelo = settings.modelo_embeddings
        dims = settings.embedding_dimensions
        [v] = EmbedderOpenAI(cliente, modelo, proveedor, dims).embed(["prueba"])
        return f"{modelo} · {len(v)} dimensiones"

    pruebas: list[tuple[str, Callable[[], str]]] = [
        ("Chat", basico),
        ("Tool calling (supervisor)", herramientas),
        ("Salida estructurada (generación)", estructurada),
        ("Embeddings (búsqueda e ingesta)", embeddings),
    ]
    if ligero := settings.modelo_ligero:

        def modelo_ligero() -> str:
            with traducir_errores(proveedor, ligero, "chat"):
                cliente.chat.completions.create(
                    model=ligero, messages=[{"role": "user", "content": "OK"}], temperature=0
                )
            return ligero

        pruebas.append(("Modelo ligero (guardián)", modelo_ligero))

    resultados: list[Comprobacion] = []
    fatal: str | None = None
    for nombre, prueba in pruebas:
        if fatal:
            resultados.append(Comprobacion(nombre=nombre, estado="omitida", detalle=fatal))
            continue
        try:
            resultados.append(Comprobacion(nombre=nombre, estado="ok", detalle=prueba()))
        except ModeloError as e:
            resultados.append(
                Comprobacion(nombre=nombre, estado="error", detalle=e.tipo, pista=e.pista)
            )
            if e.tipo in FATALES:
                fatal = f"omitida tras {e.tipo}"
    return resultados


# ------------------------------------------------------------------ CLI
def diagnosticar(settings: Settings, llamadas: bool = True) -> Diagnostico:
    if settings.modelos_proveedor == "openai":
        comprobaciones = requisitos_openai(settings)
    else:
        comprobaciones = requisitos_azure(settings)
    if llamadas and all(c.estado != "error" for c in comprobaciones):
        comprobaciones += pruebas_modelos(settings)
    return Diagnostico(proveedor=settings.modelos_proveedor, comprobaciones=comprobaciones)


def imprimir(d: Diagnostico) -> None:
    print(f"Proveedor de modelos: {d.proveedor} (MODELOS_PROVEEDOR)\n")
    for c in d.comprobaciones:
        print(f"{ICONOS[c.estado]} {c.nombre:34} {c.detalle}")
        if c.estado in ("error", "aviso") and c.pista:
            print(f"   → {c.pista}")
    print("\nListo para usar los modelos." if d.ok else "\nResuelve lo marcado con ⛔ y repite.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--sin-llamadas", action="store_true", help="solo requisitos")
    parser.add_argument("--json", action="store_true", help="salida en JSON")
    args = parser.parse_args(argv)
    d = diagnosticar(get_settings(), llamadas=not args.sin_llamadas)
    if args.json:
        print(d.model_dump_json(indent=2))
    else:
        imprimir(d)
    return 0 if d.ok else 1


if __name__ == "__main__":
    sys.exit(main())
