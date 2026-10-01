"""Proveedor de modelos (LLM, supervisor y embeddings), independiente de Azure.

`MODELOS_PROVEEDOR`:
- azure: Azure OpenAI (clave en local; Managed Identity / `az login` sin clave).
- openai: OpenAI o cualquier endpoint compatible con su API (OPENAI_BASE_URL + OPENAI_API_KEY).

El grafo solo conoce las interfaces de app/retrieval/base.py.
"""
