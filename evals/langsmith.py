"""Evaluaciones en LangSmith: dataset generado desde la matriz y un experimento con dos tipos
de evaluadores, en una sola pasada:

- Por capas (código propio, deterministas): contrato, expectativas, recuperación y seguridad.
- Prebuilt de LangSmith (`openevals`, LLM-as-judge con gpt-4o): groundedness, helpfulness y
  retrieval relevance. Solo en respuestas fundamentadas (no en bloqueos ni "sin contexto").

Las métricas quedan como feedback del experimento y se devuelven también para el informe local.
"""

import time
from typing import Any

import httpx
from langsmith import Client
from openevals.llm import create_llm_as_judge
from openevals.prompts import (
    RAG_GROUNDEDNESS_PROMPT,
    RAG_HELPFULNESS_PROMPT,
    RAG_RETRIEVAL_RELEVANCE_PROMPT,
)

from evals.ejecutar import evaluar_respuesta, llamar
from evals.modelos import Metrica, ResultadoEvaluacion
from tests.integration.evaluador import Escenario, Matriz

DATASET = "matriz-escenarios"

# feedback_key → (prompt prebuilt, variables que usa)
EVALUADORES_RAG = {
    "rag_groundedness": (RAG_GROUNDEDNESS_PROMPT, ("context", "outputs")),
    "rag_helpfulness": (RAG_HELPFULNESS_PROMPT, ("inputs", "outputs")),
    "rag_retrieval_relevance": (RAG_RETRIEVAL_RELEVANCE_PROMPT, ("inputs", "context")),
}


def construir_ejemplos(matriz: Matriz) -> list[dict[str, Any]]:
    """Un ejemplo por escenario: inputs (rol y pregunta/payload), referencia (escenario
    completo) y metadata para filtrar por capacidad en LangSmith."""
    return [
        {
            "inputs": {"rol": e.rol, "cuerpo": e.cuerpo(), "turnos_previos": e.turnos_previos},
            "outputs": {"escenario": e.model_dump(mode="json")},
            "metadata": {"escenario_id": e.id, "capacidades": e.capacidades, "rol": e.rol},
        }
        for e in matriz.escenarios
    ]


def sincronizar_dataset(cliente: Client, matriz: Matriz, nombre: str = DATASET) -> str:
    """Idempotente: recrea los ejemplos a partir de la matriz (la matriz es la fuente)."""
    if cliente.has_dataset(dataset_name=nombre):
        dataset = cliente.read_dataset(dataset_name=nombre)
        ids = [ej.id for ej in cliente.list_examples(dataset_id=dataset.id)]
        if ids:
            cliente.delete_examples(ids)
    else:
        dataset = cliente.create_dataset(
            nombre, description="Matriz de escenarios del asistente RAG (tests/integration)"
        )
    cliente.create_examples(dataset_id=dataset.id, examples=construir_ejemplos(matriz))
    return str(dataset.id)


def cliente_langsmith() -> Client:
    """Cliente con la clave de la configuración (.env en local, Key Vault en Azure)."""
    from app.config import get_settings

    settings = get_settings()
    if settings.langsmith_api_key is None:
        raise RuntimeError("Falta LANGSMITH_API_KEY para ejecutar el experimento en LangSmith")
    return Client(api_key=settings.langsmith_api_key.get_secret_value())


def respuesta_fundamentada(outputs: dict[str, Any]) -> bool:
    cuerpo = outputs.get("cuerpo") or {}
    return (
        outputs.get("status") == 200
        and isinstance(cuerpo, dict)
        and not cuerpo.get("sin_contexto", True)
        and bool(cuerpo.get("citas"))
    )


def evaluadores_rag(juez: Any, modelo: str) -> list:
    """Evaluadores prebuilt (openevals) con gpt-4o como juez; se omiten si no aplican."""
    evaluadores = []
    for clave, (prompt, variables) in EVALUADORES_RAG.items():
        juzgar = create_llm_as_judge(prompt=prompt, feedback_key=clave, judge=juez, model=modelo)

        def evaluador(
            inputs: dict[str, Any], outputs: dict[str, Any], _j=juzgar, _v=variables, _k=clave
        ) -> dict[str, Any]:
            if not respuesta_fundamentada(outputs):
                return {"results": []}  # bloqueos, "sin contexto", errores: no aplica
            cuerpo = outputs["cuerpo"]
            valores = {
                "inputs": inputs["cuerpo"].get("pregunta", ""),
                "outputs": cuerpo["respuesta"],
                "context": "\n\n".join(
                    f"[{i}] {c['fragmento']}" for i, c in enumerate(cuerpo["citas"], start=1)
                ),
            }
            return _j(**{v: valores[v] for v in _v})

        evaluador.__name__ = clave
        evaluadores.append(evaluador)
    return evaluadores


def _metricas_rag(resultado: Any) -> list[Metrica]:
    """Feedback de los evaluadores prebuilt de una fila del experimento → métricas locales."""
    metricas = []
    for r in resultado["evaluation_results"]["results"]:
        if r.key in EVALUADORES_RAG and r.score is not None:
            metricas.append(
                Metrica(capa="juez", nombre=r.key, valor=float(r.score), detalle=r.comment or "")
            )
    return metricas


def ejecutar_experimento(
    base_url: str, matriz: Matriz, pausa: float = 0.0
) -> tuple[str, list[ResultadoEvaluacion]]:
    from app.config import get_settings
    from app.retrieval.azure_openai import build_client

    settings = get_settings()
    cliente = cliente_langsmith()
    sincronizar_dataset(cliente, matriz)
    http = httpx.Client(base_url=base_url.rstrip("/"), timeout=180)
    por_escenario: dict[str, ResultadoEvaluacion] = {}

    def objetivo(inputs: dict[str, Any]) -> dict[str, Any]:
        if pausa:
            time.sleep(pausa)
        escenario = Escenario(
            id="ls", descripcion="ls", capacidades=["x"], rol=inputs["rol"], esperado={},
            turnos_previos=inputs.get("turnos_previos", []),
            **({"payload": inputs["cuerpo"]} if "pregunta" not in inputs["cuerpo"]
               else {"pregunta": inputs["cuerpo"]["pregunta"]}),
        )  # fmt: skip
        status, cuerpo = llamar(http, escenario)
        return {"status": status, "cuerpo": cuerpo}

    def por_capas(
        inputs: dict[str, Any], outputs: dict[str, Any], reference_outputs: dict[str, Any]
    ) -> dict[str, Any]:
        escenario = Escenario.model_validate(reference_outputs["escenario"])
        r = evaluar_respuesta(escenario, outputs["status"], outputs["cuerpo"], matriz, None)
        por_escenario[escenario.id] = r
        return {
            "results": [
                {"key": m.nombre, "score": m.valor, "comment": m.detalle or None}
                for m in r.metricas
            ]
        }

    resultados = cliente.evaluate(
        objetivo,
        data=DATASET,
        evaluators=[
            por_capas,
            *evaluadores_rag(build_client(settings), settings.azure_openai_chat_deployment),
        ],
        experiment_prefix="etapa-a",
        metadata={"destino": base_url, "modelo": settings.azure_openai_chat_deployment},
        max_concurrency=1,
    )
    for fila in resultados:
        escenario_id = fila["example"].metadata["escenario_id"]
        if escenario_id in por_escenario:
            por_escenario[escenario_id].metricas += _metricas_rag(fila)
    ordenados = [por_escenario[e.id] for e in matriz.escenarios if e.id in por_escenario]
    return resultados.experiment_name, ordenados
