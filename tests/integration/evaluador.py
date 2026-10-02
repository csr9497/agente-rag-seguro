"""Evaluación de escenarios de integración con Pydantic.

Carga la matriz (escenarios.yaml), evalúa cada respuesta HTTP contra el contrato
`RespuestaConsulta` y las expectativas del escenario, y genera el informe de cobertura.

Informe de cobertura sin ejecutar (solo la matriz):
    uv run python -m tests.integration.evaluador
"""

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.persistencia.modelos import MensajeGuardado

MATRIZ_PATH = Path(__file__).with_name("escenarios.yaml")


class Capacidad(BaseModel):
    model_config = ConfigDict(extra="forbid")

    descripcion: str
    fase: int = Field(ge=1, le=6)
    tests_unitarios: list[str] = Field(
        default_factory=list,
        description="Tests que la cubren fuera de HTTP, como 'tests/archivo.py::test_nombre'",
    )


class Expectativa(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: int = 200
    sin_contexto: bool | None = None
    min_citas: int = 0
    alguna_fuente_de: list[str] = []
    todas_fuentes_de: list[str] = Field(default=[], description="Cada una debe estar citada")
    fuentes_prohibidas: list[str] = Field(default=[], description="Prefijos de fuente")
    no_consultados: list[str] = Field(
        default=[], description="Prefijos de doc_id que no pueden aparecer entre los consultados"
    )
    contiene_alguno: list[str] = []
    no_contiene: list[str] = []
    aclaracion: bool | None = Field(
        default=None, description="True: debe pedir aclaración en vez de buscar"
    )
    aprobacion_pendiente: str | None = Field(
        default=None, description="Tool que debe quedar pendiente de confirmar o aprobar"
    )
    sin_aprobaciones: bool = Field(
        default=False, description="True: no debe proponer nada que el usuario no pidió"
    )


class Escenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    descripcion: str
    capacidades: list[str] = Field(min_length=1)
    rol: str = Field(default="public", description="Rol con el que se inicia la conversación")
    turnos_previos: list[str] = Field(
        default=[], description="Preguntas enviadas antes en la misma conversación (memoria)"
    )
    pregunta: str | None = None
    payload: dict[str, Any] | None = None
    docs_relevantes: list[str] = Field(
        default=[], description="Verdad de referencia para métricas de recuperación"
    )
    esperado: Expectativa

    @model_validator(mode="after")
    def _pregunta_o_payload(self) -> Self:
        if (self.pregunta is None) == (self.payload is None):
            raise ValueError(f"{self.id}: indica exactamente uno de 'pregunta' o 'payload'")
        return self

    def cuerpo(self) -> dict[str, Any]:
        return self.payload if self.payload is not None else {"pregunta": self.pregunta}


class Matriz(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    fase_actual: int
    capacidades: dict[str, Capacidad]
    canarios: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Marcador único → roles que pueden verlo. En cualquier otro rol es una fuga",
    )
    escenarios: list[Escenario]

    @model_validator(mode="after")
    def _coherencia(self) -> Self:
        ids = Counter(e.id for e in self.escenarios)
        if repetidos := [i for i, n in ids.items() if n > 1]:
            raise ValueError(f"IDs de escenario repetidos: {repetidos}")
        for e in self.escenarios:
            if desconocidas := set(e.capacidades) - self.capacidades.keys():
                raise ValueError(f"{e.id}: capacidades no declaradas {sorted(desconocidas)}")
        return self


class ResultadoEscenario(BaseModel):
    id: str
    capacidades: list[str]
    estado: Literal["ok", "fallo", "omitido"]
    fallos: list[str] = []
    respuesta: str | None = None
    metricas: dict[str, float] = {}


def cargar_matriz(path: Path = MATRIZ_PATH) -> Matriz:
    return Matriz.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def evaluar(
    escenario: Escenario, status: int, body: Any, canarios: dict[str, list[str]] | None = None
) -> ResultadoEscenario:
    """Evalúa la respuesta HTTP: contrato `MensajeGuardado`, invariantes, expectativas del
    escenario, canarios (fuga entre roles) y métricas de recuperación."""
    esperado = escenario.esperado
    fallos: list[str] = []
    texto: str | None = None
    metricas: dict[str, float] = {}

    # Canarios: se buscan en todo el cuerpo, sea cual sea el status.
    crudo = json.dumps(body, ensure_ascii=False) if body is not None else ""
    for marcador, roles in (canarios or {}).items():
        if escenario.rol not in roles and marcador in crudo:
            fallos.append(f"FUGA: el canario {marcador} llegó al rol {escenario.rol!r}")

    if status != esperado.status:
        fallos.append(f"status {status} != {esperado.status}")
    elif status == 200:
        try:
            m = MensajeGuardado.model_validate(body)
        except ValidationError as exc:
            fallos.append(f"contrato MensajeGuardado: {exc.error_count()} errores")
        else:
            texto = m.respuesta
            fallos.extend(_invariantes(m))
            fallos.extend(_expectativas(m, esperado))
            if escenario.docs_relevantes:
                relevantes = set(escenario.docs_relevantes)
                metricas["recall_docs"] = len(relevantes & set(m.documentos_consultados)) / len(
                    relevantes
                )

    return ResultadoEscenario(
        id=escenario.id,
        capacidades=escenario.capacidades,
        estado="fallo" if fallos else "ok",
        fallos=fallos,
        respuesta=texto,
        metricas=metricas,
    )


def _invariantes(r: MensajeGuardado) -> list[str]:
    fallos = []
    if r.sin_contexto and r.citas:
        fallos.append("invariante: sin_contexto=true pero hay citas")
    if not r.sin_contexto and not r.citas:
        fallos.append("invariante: respuesta sin citas marcada como fundamentada")
    if r.citas and not any(f"[{c.numero}]" in r.respuesta for c in r.citas):
        fallos.append("invariante: el texto no contiene marcas [n] de las citas")
    return fallos


def _expectativas(r: MensajeGuardado, e: Expectativa) -> list[str]:
    fallos = []
    fuentes = {c.fuente for c in r.citas}
    texto = r.respuesta.lower()
    if e.sin_contexto is not None and r.sin_contexto != e.sin_contexto:
        fallos.append(f"sin_contexto={r.sin_contexto}, esperado {e.sin_contexto}")
    if e.aclaracion is not None and (r.aclaracion is not None) != e.aclaracion:
        fallos.append(f"aclaración={r.aclaracion is not None}, esperado {e.aclaracion}")
    pendientes = [a.tool for a in r.aprobaciones]
    if e.aprobacion_pendiente and e.aprobacion_pendiente not in pendientes:
        fallos.append(f"sin aprobación pendiente de {e.aprobacion_pendiente} (hay: {pendientes})")
    if e.sin_aprobaciones and pendientes:
        fallos.append(f"propone acciones que no se pidieron: {pendientes}")
    if len(r.citas) < e.min_citas:
        fallos.append(f"{len(r.citas)} citas < mínimo {e.min_citas}")
    if e.alguna_fuente_de and not fuentes & set(e.alguna_fuente_de):
        fallos.append(f"ninguna cita de {e.alguna_fuente_de} (citadas: {sorted(fuentes)})")
    if faltan := sorted(set(e.todas_fuentes_de) - fuentes):
        fallos.append(f"faltan citas de {faltan}")
    if prohibidas := sorted(f for f in fuentes if f.startswith(tuple(e.fuentes_prohibidas))):
        fallos.append(f"fuentes prohibidas citadas: {prohibidas}")
    if e.contiene_alguno and not any(s.lower() in texto for s in e.contiene_alguno):
        fallos.append(f"la respuesta no contiene ninguno de {e.contiene_alguno}")
    consultados = [d for d in r.documentos_consultados if d.startswith(tuple(e.no_consultados))]
    if e.no_consultados and consultados:
        fallos.append(f"documentos consultados no permitidos: {consultados}")
    if filtrados := [s for s in e.no_contiene if s.lower() in texto]:
        fallos.append(f"la respuesta contiene texto prohibido: {filtrados}")
    return fallos


def generar_informe(
    matriz: Matriz, resultados: list[ResultadoEscenario], base_url: str | None = None
) -> str:
    por_id = {r.id: r for r in resultados}
    ahora = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lineas = [
        "# Informe de pruebas de integración",
        "",
        f"- Fecha: {ahora}",
        f"- Destino: {base_url or 'no ejecutado (solo matriz)'}",
        f"- Fase actual: {matriz.fase_actual}",
    ]
    if resultados:
        estados = Counter(r.estado for r in resultados)
        lineas.append(
            f"- Resultado: {estados['ok']} ok · {estados['fallo']} fallo · "
            f"{estados['omitido']} omitido de {len(matriz.escenarios)} escenarios"
        )

    lineas += [
        "",
        "## Cobertura por capacidad",
        "",
        "| Capacidad | Fase | Escenarios | OK | Fallo | Omitido | Tests unitarios | Estado |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for nombre, cap in matriz.capacidades.items():
        ids = [e.id for e in matriz.escenarios if nombre in e.capacidades]
        c = Counter(por_id[i].estado for i in ids if i in por_id)
        if not ids and cap.tests_unitarios:
            estado = "🧪 solo unitarios"
        elif not ids:
            estado = "⚠️ sin escenarios" if cap.fase <= matriz.fase_actual else "planificada"
        elif c["fallo"]:
            estado = "❌"
        elif c["ok"]:
            estado = "✅" if not c["omitido"] else "✅ parcial"
        else:
            estado = "—"
        lineas.append(
            f"| {nombre} | {cap.fase} | {len(ids)} | {c['ok']} | {c['fallo']} "
            f"| {c['omitido']} | {len(cap.tests_unitarios)} | {estado} |"
        )

    lineas += [
        "",
        "## Escenarios",
        "",
        "| ID | Descripción | Capacidades | Estado | Detalle |",
        "|---|---|---|---|---|",
    ]
    for e in matriz.escenarios:
        r = por_id.get(e.id)
        estado = r.estado if r else "no ejecutado"
        detalle = "; ".join(r.fallos) if r and r.fallos else ""
        lineas.append(
            f"| {e.id} | {e.descripcion} | {', '.join(e.capacidades)} | {estado} "
            f"| {detalle.replace('|', '/')} |"
        )
    return "\n".join(lineas) + "\n"


if __name__ == "__main__":
    print(generar_informe(cargar_matriz(), []))
