"""Catálogo de consultas a datos internos (tool `data_query`).

El LLM nunca escribe SQL: elige una consulta del catálogo y sus parámetros (validados con
Pydantic). Cada consulta declara qué roles pueden ejecutarla; la tool y access_guardrail lo
comprueban. El resultado entra al contexto como fuente citable `datos:<consulta>`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, insert, select

from app.persistencia import tablas as t

PREFIJO = "datos:"


class ParametrosFestivos(BaseModel):
    model_config = ConfigDict(extra="forbid")
    anio: int = Field(ge=2000, le=2100)


class ParametrosDepartamento(BaseModel):
    model_config = ConfigDict(extra="forbid")
    departamento: str | None = Field(default=None, pattern=r"^[a-z0-9_\-]{1,40}$")


@dataclass(frozen=True)
class Consulta:
    id: str
    descripcion: str
    roles: tuple[str, ...]
    parametros: type[BaseModel]
    ejecutar: Callable[[Engine, Any], list[dict[str, Any]]]


def _festivos(motor: Engine, p: ParametrosFestivos) -> list[dict[str, Any]]:
    with motor.connect() as c:
        filas = c.execute(
            select(t.festivos)
            .where(t.festivos.c.fecha.like(f"{p.anio}-%"))
            .order_by(t.festivos.c.fecha)
        ).mappings()
        return [dict(f) for f in filas]


def _departamentos(columnas: tuple[str, ...]) -> Callable[[Engine, ParametrosDepartamento], list]:
    def ejecutar(motor: Engine, p: ParametrosDepartamento) -> list[dict[str, Any]]:
        consulta = select(*(t.departamentos.c[col] for col in columnas)).order_by(
            t.departamentos.c.id
        )
        if p.departamento:
            consulta = consulta.where(t.departamentos.c.id == p.departamento)
        with motor.connect() as c:
            return [dict(f) for f in c.execute(consulta).mappings()]

    return ejecutar


CONSULTAS: dict[str, Consulta] = {
    c.id: c
    for c in [
        Consulta(
            id="festivos",
            descripcion="Festivos oficiales de la empresa en un año (parámetro: anio)",
            roles=("public", "rrhh"),
            parametros=ParametrosFestivos,
            ejecutar=_festivos,
        ),
        Consulta(
            id="plantilla_por_departamento",
            descripcion="Número de personas por departamento (parámetro opcional: departamento)",
            roles=("rrhh",),
            parametros=ParametrosDepartamento,
            ejecutar=_departamentos(("id", "nombre", "plantilla")),
        ),
        Consulta(
            id="presupuesto_formacion",
            descripcion="Presupuesto anual de formación por departamento, en euros",
            roles=("rrhh",),
            parametros=ParametrosDepartamento,
            ejecutar=_departamentos(("id", "nombre", "presupuesto_formacion_eur")),
        ),
    ]
}


def permisos_por_consulta() -> dict[str, list[str]]:
    """Para access_guardrail: doc_id sintético → roles autorizados."""
    return {f"{PREFIJO}{c.id}": list(c.roles) for c in CONSULTAS.values()}


def sembrar_datos_ejemplo(motor: Engine) -> None:
    with motor.begin() as c:
        if c.execute(select(t.festivos).limit(1)).first() is None:
            c.execute(
                insert(t.festivos),
                [
                    {"fecha": "2026-01-01", "nombre": "Año Nuevo"},
                    {"fecha": "2026-05-01", "nombre": "Día del Trabajo"},
                    {"fecha": "2026-07-28", "nombre": "Fiestas Patrias"},
                    {"fecha": "2026-12-25", "nombre": "Navidad"},
                    {"fecha": "2027-01-01", "nombre": "Año Nuevo"},
                ],
            )
        if c.execute(select(t.departamentos).limit(1)).first() is None:
            c.execute(
                insert(t.departamentos),
                [
                    {"id": "it", "nombre": "Tecnología", "plantilla": 42,
                     "presupuesto_formacion_eur": 60000},
                    {"id": "rrhh", "nombre": "Recursos Humanos", "plantilla": 9,
                     "presupuesto_formacion_eur": 12000},
                    {"id": "ventas", "nombre": "Ventas", "plantilla": 31,
                     "presupuesto_formacion_eur": 25000},
                ],
            )  # fmt: skip
