"""Prompt del supervisor. El texto vive en app/prompts/supervisor.md (versionado también en
LangSmith como «agente-rag-supervisor»)."""

from app.prompts import local

SUPERVISOR_PROMPT = local("supervisor")
