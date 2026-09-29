"""Tool `data_query`: consultas predefinidas a datos internos, con permisos por rol."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Engine

from app.datos.catalogo import CONSULTAS, PREFIJO
from app.models.schemas import Chunk, ChunkRecuperado, Usuario
from app.tools.base import ResultadoHerramienta

SIN_ACCESO_DATOS = "La consulta no existe o no tienes acceso a ella."
MAX_FILAS = 50

IdConsulta = Literal[tuple(CONSULTAS)]  # type: ignore[valid-type]


class DataQueryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consulta: IdConsulta = Field(description="Consulta del catálogo")
    anio: int | None = Field(default=None, ge=2000, le=2100)
    departamento: str | None = Field(default=None, pattern=r"^[a-z0-9_\-]{1,40}$")


class DataQuery:
    nombre = "data_query"
    descripcion = "Consulta datos internos estructurados (no documentos). Consultas: " + "; ".join(
        f"{c.id}: {c.descripcion}" for c in CONSULTAS.values()
    )
    args_model = DataQueryArgs

    def __init__(self, motor: Engine) -> None:
        self._motor = motor

    def ejecutar(self, args: DataQueryArgs, usuario: Usuario, top_k: int) -> ResultadoHerramienta:
        consulta = CONSULTAS[args.consulta]
        roles = [r for r in usuario.groups if r in consulta.roles]
        if not roles:
            return ResultadoHerramienta(nota=SIN_ACCESO_DATOS)
        try:
            parametros = consulta.parametros.model_validate(
                args.model_dump(exclude={"consulta"}, exclude_none=True)
            )
        except ValidationError as exc:
            return ResultadoHerramienta(
                nota=f"Parámetros no válidos para {consulta.id} ({exc.error_count()} errores)."
            )
        filas = consulta.ejecutar(self._motor, parametros)[:MAX_FILAS]
        if not filas:
            return ResultadoHerramienta(nota=f"{consulta.id}: sin resultados.")
        cabecera = " | ".join(filas[0])
        cuerpo = "\n".join(" | ".join(str(v) for v in f.values()) for f in filas)
        chunk = Chunk(
            chunk_id=f"{PREFIJO}{consulta.id}#{parametros.model_dump_json()}",
            doc_id=f"{PREFIJO}{consulta.id}",
            fuente=f"datos internos: {consulta.id}",
            contenido=f"{consulta.descripcion}\n{cabecera}\n{cuerpo}",
            acl_groups=roles,
        )
        return ResultadoHerramienta(chunks=[ChunkRecuperado(chunk=chunk, score=1.0)])
