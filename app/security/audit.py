"""Auditoría de consultas. Fase 1: log JSON estructurado (stdout → Log Analytics en Azure).
En la fase de PostgreSQL se persistirá en tabla."""

import logging

from app.models.schemas import RegistroAuditoria, RespuestaConsulta, Usuario

logger = logging.getLogger("audit")


def registrar_consulta(usuario: Usuario, pregunta: str, respuesta: RespuestaConsulta) -> None:
    registro = RegistroAuditoria(
        usuario=usuario.id,
        grupos=usuario.groups,
        pregunta=pregunta,
        fuentes=sorted({c.chunk_id for c in respuesta.citas}),
        respuesta=respuesta.respuesta,
        sin_contexto=respuesta.sin_contexto,
    )
    logger.info(registro.model_dump_json())
