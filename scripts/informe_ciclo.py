"""Informe final de un ciclo de pruebas (reports/ciclos/<id>/informe.md) a partir de las
evidencias que deja scripts/ciclo_pruebas.sh.

    uv run python scripts/informe_ciclo.py reports/ciclos/<id>
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def _json(ruta: Path) -> Any:
    return json.loads(ruta.read_text()) if ruta.exists() else None


def _texto(ruta: Path) -> str:
    return ruta.read_text() if ruta.exists() else ""


def seccion_infra(run: Path) -> list[str]:
    v = _json(run / "validacion-infra.json")
    if not v:
        return ["Sin validación de infraestructura."]
    filas = [
        f"| {c['servicio']} | {c['prueba']} | {'✅' if c['ok'] else '❌'} | {c['segundos']} "
        f"| {c['detalle'][:120]} |"
        for c in v["comprobaciones"]
    ]
    ok = all(c["ok"] for c in v["comprobaciones"])
    return [
        f"Resultado: **{'correcta' if ok else 'con fallos'}** · grupo `{v['grupo_de_recursos']}`",
        "",
        "| Servicio | Prueba | OK | s | Detalle |",
        "|---|---|---|---|---|",
        *filas,
    ]


def seccion_evaluacion(run: Path) -> list[str]:
    ev = _json(run / "evaluacion.json")
    if not ev:
        return ["Sin evaluación."]
    aprobado = all(u["aprobado"] for u in ev["umbrales"] if u["umbral"]["bloqueante"])
    experimento = re.search(r"Experimento en LangSmith: (\S+)", _texto(run / "evaluacion.txt"))
    lineas = [
        f"Resultado: **{'✅ aprobado' if aprobado else '❌ no aprobado'}** · "
        f"{len(ev['resultados'])} escenarios · experimento LangSmith: "
        f"`{experimento.group(1) if experimento else '—'}`",
        "",
        "| Métrica | Agregación | Mínimo | Valor | Muestras | Bloqueante | Estado |",
        "|---|---|---|---|---|---|---|",
    ]
    for u in ev["umbrales"]:
        valor = "—" if u["valor"] is None else f"{u['valor']:.2f}"
        estado = "sin datos" if u["muestras"] == 0 else ("✅" if u["aprobado"] else "❌")
        lineas.append(
            f"| {u['umbral']['metrica']} | {u['umbral']['agregacion']} | {u['umbral']['minimo']} "
            f"| {valor} | {u['muestras']} | {'sí' if u['umbral']['bloqueante'] else 'no'} "
            f"| {estado} |"
        )
    fallidos = [r for r in ev["resultados"] if r["fallos"]]
    if fallidos:
        lineas += ["", "Escenarios con fallos:", ""]
        lineas += [
            f"- **{r['escenario_id']}** ({r['rol']}): {'; '.join(r['fallos'])}" for r in fallidos
        ]
    return lineas


def seccion_auditoria(run: Path) -> list[str]:
    ruta = run / "auditoria.jsonl"
    registros = [json.loads(ln) for ln in _texto(ruta).splitlines() if ln.strip().startswith("{")]
    if not registros:
        return ["Sin registros de auditoría."]
    hallazgos = Counter(h["tipo"] for r in registros for h in r.get("hallazgos", []))
    return [
        f"- Consultas auditadas: {len(registros)}",
        f"- Sin contexto o bloqueadas: {sum(r.get('sin_contexto', False) for r in registros)}",
        f"- Con fuentes citadas: {sum(bool(r.get('fuentes')) for r in registros)}",
        f"- Hallazgos de guardrails: {dict(hallazgos) or 'ninguno'}",
    ]


def seccion_datos(run: Path) -> list[str]:
    bd = _json(run / "base-de-datos.json") or {}
    recursos = _json(run / "recursos-azure.json") or []
    lineas = [f"- Tablas: {', '.join(f'{t} ({len(f)})' for t, f in bd.items()) or '—'}"]
    lineas += [f"- Siembra: {_texto(run / 'siembra.txt').strip() or '—'}"]
    lineas += ["", "| Recurso | Tipo | SKU | Región |", "|---|---|---|---|"]
    lineas += [
        f"| {r['nombre']} | {r['tipo']} | {r.get('sku') or '—'} | {r['region']} |" for r in recursos
    ]
    return lineas


def seccion_apagado(run: Path) -> list[str]:
    log = _texto(run / "terraform-destroy.log")
    m = re.search(r"Destroy complete! Resources: (\d+) destroyed", log)
    if m:
        return [f"✅ `terraform destroy`: {m.group(1)} recursos eliminados."]
    return ["⚠️ No consta un `terraform destroy` completo: revisar terraform-destroy.log."]


def main(run: Path) -> None:
    partes = [
        f"# Informe del ciclo de pruebas · {run.name}",
        "",
        f"Commit: `{_texto(run / 'commit.txt').strip()[:12] or '—'}`",
        "",
        "## 1. Infraestructura (validación tras el despliegue)",
        "",
        *seccion_infra(run),
        "",
        "## 2. Test de la app (LangSmith: evaluadores por capas + prebuilt)",
        "",
        *seccion_evaluacion(run),
        "",
        "## 3. Auditoría (fuera de LangSmith)",
        "",
        *seccion_auditoria(run),
        "",
        "## 4. Datos y recursos",
        "",
        *seccion_datos(run),
        "",
        "## 5. Apagado",
        "",
        *seccion_apagado(run),
        "",
        "## Pasos",
        "",
        "```",
        _texto(run / "pasos.log").strip(),
        "```",
        "",
        "Evidencias en esta carpeta: app.log, auditoria.jsonl, base-de-datos.json, evaluacion.*, "
        "validacion-infra.*, terraform-*.log, terraform-outputs.json (sin valores sensibles).",
    ]
    (run / "informe.md").write_text("\n".join(partes) + "\n", encoding="utf-8")
    print(f"Informe: {run / 'informe.md'}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
