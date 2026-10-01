"""Publica los prompts del repositorio en LangSmith (Prompt Hub).

    uv run python -m app.prompts.publicar                # etiqueta «dev»
    uv run python -m app.prompts.publicar --etiqueta prod

Cada prompt (app/prompts/*.md) se sube con su nombre de LangSmith (ver PROMPTS). Si el texto
no cambió no se crea un commit nuevo, pero la etiqueta se mueve al último: repetirlo es
inocuo. Lo ejecutan `make prompts-langsmith`, `make levantar`, `make studio`,
`make local-nube` y `make desplegar` cuando hay LANGSMITH_API_KEY en .env.
"""

import argparse
import logging
import re
import sys
from typing import Any, Literal

from langsmith.utils import LangSmithNotFoundError
from pydantic import BaseModel

from app.config import get_settings
from app.prompts import PROMPTS, local

ETIQUETA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class PromptPublicado(BaseModel):
    nombre: str
    estado: Literal["nuevo", "actualizado", "sin cambios"]
    url: str


def plantilla(texto: str) -> Any:
    """Prompt de sistema tal cual (mustache: las llaves del texto no son variables)."""
    from langchain_core.prompts import ChatPromptTemplate

    return ChatPromptTemplate.from_messages([("system", texto)], template_format="mustache")


def texto_remoto(cliente: Any, nombre: str) -> str | None:
    """Texto del último commit, o None si el prompt aún no existe."""
    if not cliente._prompt_exists(nombre):
        return None
    return cliente.pull_prompt(nombre).messages[0].prompt.template


def _etiquetar(cliente: Any, nombre: str, etiquetas: list[str]) -> None:
    """Mueve (o crea) cada etiqueta al último commit del prompt."""
    ultimo = next(iter(cliente.list_prompt_commits(nombre, limit=1)))
    repo = f"{ultimo.owner}/{ultimo.repo}"
    for etiqueta in etiquetas:
        try:
            cliente.request_with_retries(
                "PATCH", f"/repos/{repo}/tags/{etiqueta}", json={"commit_id": str(ultimo.id)}
            )
        except LangSmithNotFoundError:  # la etiqueta aún no existe
            cliente.request_with_retries(
                "POST", f"/repos/{repo}/tags",
                json={"tag_name": etiqueta, "commit_id": str(ultimo.id)},
            )  # fmt: skip


def publicar(cliente: Any, etiquetas: list[str]) -> list[PromptPublicado]:
    if invalidas := [e for e in etiquetas if not ETIQUETA.fullmatch(e)]:
        raise ValueError(f"Etiquetas no válidas: {invalidas}")
    publicados: list[PromptPublicado] = []
    for clave, (nombre, fichero) in PROMPTS.items():
        texto = local(clave)
        previo = texto_remoto(cliente, nombre)
        if previo == texto:
            _etiquetar(cliente, nombre, etiquetas)
            url = cliente._get_prompt_url(nombre)
            estado: Literal["nuevo", "actualizado", "sin cambios"] = "sin cambios"
        else:
            url = cliente.push_prompt(
                nombre,
                object=plantilla(texto),
                description=f"Prompt de sistema del agente RAG (app/prompts/{fichero})",
                commit_tags=etiquetas,
            )
            estado = "nuevo" if previo is None else "actualizado"
        publicados.append(PromptPublicado(nombre=nombre, estado=estado, url=url))
    return publicados


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--etiqueta", action="append", help="Etiqueta del commit (defecto: dev)")
    args = parser.parse_args()
    logging.getLogger("langsmith").setLevel(logging.CRITICAL)  # el error se resume abajo
    settings = get_settings()
    clave = settings.langsmith_api_key.get_secret_value() if settings.langsmith_api_key else ""
    if not clave:
        print("Prompts no publicados: falta LANGSMITH_API_KEY en .env.")
        return 0
    from langsmith import Client

    cliente = Client(api_key=clave)
    try:
        publicados = publicar(cliente, args.etiqueta or ["dev"])
    except Exception as exc:  # noqa: BLE001 — informar sin traza larga; la app no depende de esto
        detalle = str(exc).splitlines()[0][:200]
        print(f"⛔ Prompts no publicados en LangSmith: {type(exc).__name__}: {detalle}")
        return 1
    etiquetas = ", ".join(args.etiqueta or ["dev"])
    for p in publicados:
        print(f"✅ {p.nombre} ({p.estado}, etiqueta {etiquetas}): {p.url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
