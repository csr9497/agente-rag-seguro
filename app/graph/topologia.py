"""Visualización de la topología del grafo (Mermaid renderizado en el navegador)."""

import html

from app.graph.agente import Agente

DESCRIPCIONES = {
    "authorize": "Deny by default: sin grupos no hay contexto visible",
    "input_guardrail": "Bloquea inyección/texto oculto; enmascara PII",
    "cache_lookup": "Caché semántica; clave = rol + huella de sus documentos visibles",
    "supervisor": "gpt-4o con tool-calling: decide qué buscar",
    "tools": "rag_retrieve, documentos, data_query, proponer_accion (grupos del estado)",
    "access_guardrail": "Contrasta cada fragmento con el registro de permisos; descarta el resto",
    "generate": "Respuesta con citas [n] y salida estructurada",
    "output_guardrail": "Bloquea fuga del prompt; enmascara PII sensible",
    "cache_store": "Guarda la respuesta (no bloqueadas ni con historial)",
    "audit": "Registra usuario, pregunta, fuentes y respuesta",
}


def mermaid(agente: Agente) -> str:
    return agente.grafo.get_graph().draw_mermaid()


def pagina_html(agente: Agente) -> str:
    filas = "".join(
        f"<tr><td><code>{n}</code></td><td>{html.escape(d)}</td></tr>"
        for n, d in DESCRIPCIONES.items()
    )
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Topología del agente</title>
<style>
  :root {{ --bg:#f7f7f8; --fg:#1d1d1f; --muted:#6b6b73; --card:#fff; --border:#e2e2e6; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#141416; --fg:#ececf1; --muted:#9a9aa3; --card:#1e1e22; --border:#2e2e34; }}
  }}
  body {{ margin:0; font:15px/1.5 system-ui,sans-serif; background:var(--bg); color:var(--fg); }}
  main {{ max-width:900px; margin:0 auto; padding:32px 16px; }}
  .card {{ background:var(--card); border:1px solid var(--border); border-radius:8px;
    padding:16px; margin-bottom:16px; overflow-x:auto; }}
  p {{ color:var(--muted); }} table {{ border-collapse:collapse; width:100%; }}
  td {{ padding:6px 8px; border-top:1px solid var(--border); vertical-align:top; }}
</style></head>
<body><main>
  <h1>Topología del agente (LangGraph)</h1>
  <p>Generada del grafo compilado en ejecución. Línea discontinua = arista condicional.
     Fuente Mermaid: <a href="grafo.mmd">grafo.mmd</a></p>
  <div class="card"><pre class="mermaid">{html.escape(mermaid(agente))}</pre></div>
  <div class="card"><table>{filas}</table></div>
</main>
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  const oscuro = matchMedia("(prefers-color-scheme: dark)").matches;
  mermaid.initialize({{ startOnLoad: true, theme: oscuro ? "dark" : "default" }});
</script>
</body></html>"""
