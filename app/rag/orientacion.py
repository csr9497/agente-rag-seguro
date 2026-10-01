"""Respuesta cuando no hay información: en lugar de una frase fija, el LLM orienta al usuario
con el catálogo de su rol (qué sí puede consultar y preguntas de ejemplo).

Seguridad: el modelo solo recibe el catálogo (títulos visibles para el rol, ya filtrados por
permisos), el motivo y la pregunta; nunca fragmentos. La respuesta va sin citas y marcada
sin_contexto (no se cachea), y pasa por el guardrail de salida como cualquier otra.
"""

import logging
import re
from typing import Literal

import openai

from app.modelos.errores import ModeloError
from app.models.schemas import RespuestaConsulta
from app.rag.catalogo import CatalogoRol
from app.rag.prompts import ORIENTACION_PROMPT, SIN_CONTEXTO, build_orientacion_prompt
from app.retrieval.base import LLM

logger = logging.getLogger(__name__)

Motivo = Literal["sin_resultados", "no_en_contexto", "fuera_de_ambito", "ayuda"]

# El esquema de salida (RespuestaLLM) pide marcas [n] de fragmento; aquí no hay fragmentos.
_MARCAS = re.compile(r"\s*(\[\d+\])+")


def responder_sin_informacion(
    llm: LLM,
    pregunta: str,
    catalogo: CatalogoRol,
    motivo: Motivo,
    system_prompt: str = ORIENTACION_PROMPT,
    respaldo: str = SIN_CONTEXTO,
) -> RespuestaConsulta:
    """Si el modelo falla o no dice nada, `respaldo` (por defecto la frase fija de siempre): la
    app nunca se queda sin respuesta por esto."""
    try:
        salida = llm.responder(
            system_prompt, build_orientacion_prompt(pregunta, catalogo.como_texto(), motivo)
        )
    except (ModeloError, openai.OpenAIError) as exc:
        logger.warning("Orientación no disponible (%s): se usa el texto fijo", exc)
        return RespuestaConsulta(respuesta=respaldo, citas=[], sin_contexto=True)
    texto = _MARCAS.sub("", salida.respuesta).strip() or respaldo
    return RespuestaConsulta(respuesta=texto, citas=[], sin_contexto=True)
