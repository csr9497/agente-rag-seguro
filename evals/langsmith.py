"""Evaluaciones en LangSmith: dataset generado desde la matriz y experimento con los mismos
evaluadores por capa (métricas como feedback del experimento)."""

from typing import Any

import httpx
from langsmith import Client

from evals.ejecutar import evaluar_respuesta, llamar
from evals.juez import Juez
from tests.integration.evaluador import Escenario, Matriz

DATASET = "matriz-escenarios"


def construir_ejemplos(matriz: Matriz) -> list[dict[str, Any]]:
    """Un ejemplo por escenario: inputs (rol y pregunta/payload), referencia (escenario
    completo) y metadata para filtrar por capacidad en LangSmith."""
    return [
        {
            "inputs": {"rol": e.rol, "cuerpo": e.cuerpo()},
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


def ejecutar_experimento(base_url: str, matriz: Matriz, juez: Juez | None) -> str:
    cliente = cliente_langsmith()
    sincronizar_dataset(cliente, matriz)
    http = httpx.Client(base_url=base_url.rstrip("/"), timeout=120)

    def objetivo(inputs: dict[str, Any]) -> dict[str, Any]:
        escenario = Escenario(
            id="ls", descripcion="ls", capacidades=["x"], rol=inputs["rol"], esperado={},
            **({"payload": inputs["cuerpo"]} if "pregunta" not in inputs["cuerpo"]
               else {"pregunta": inputs["cuerpo"]["pregunta"]}),
        )  # fmt: skip
        status, cuerpo = llamar(http, escenario)
        return {"status": status, "cuerpo": cuerpo}

    def por_capas(
        inputs: dict[str, Any], outputs: dict[str, Any], reference_outputs: dict[str, Any]
    ) -> dict[str, Any]:
        escenario = Escenario.model_validate(reference_outputs["escenario"])
        r = evaluar_respuesta(escenario, outputs["status"], outputs["cuerpo"], matriz, juez)
        return {
            "results": [
                {"key": m.nombre, "score": m.valor, "comment": m.detalle or None}
                for m in r.metricas
            ]
        }

    resultados = cliente.evaluate(
        objetivo,
        data=DATASET,
        evaluators=[por_capas],
        experiment_prefix="capas",
        metadata={"destino": base_url, "juez": juez is not None},
        max_concurrency=1,
    )
    return f"Experimento: {resultados.experiment_name}"
