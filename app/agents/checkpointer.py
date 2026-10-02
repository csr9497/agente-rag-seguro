"""Checkpointer de los agentes: PostgreSQL con serializador cifrado (AES-EAX, autenticado).

Necesario para `interrupt`: una aprobación pendiente sobrevive a un reinicio del proceso. El
estado se guarda cifrado (decisión del 2026-10-01: sin PII ni fragmentos en claro en reposo);
la clave (32 bytes, en hex) vive en Key Vault / `.env` (CHECKPOINT_CLAVE), nunca en el repo.
Sin clave válida no se arranca: fallo cerrado.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row


def clave_checkpointer(hexadecimal: str | None) -> bytes:
    try:
        clave = bytes.fromhex(hexadecimal or "")
    except ValueError as exc:
        raise ValueError("CHECKPOINT_CLAVE debe ser hexadecimal") from exc
    if len(clave) != 32:
        raise ValueError("CHECKPOINT_CLAVE debe tener 32 bytes (64 caracteres hex)")
    return clave


# Únicos tipos propios que el checkpoint puede reconstruir (además de los de LangGraph): con una
# lista explícita, un checkpoint manipulado no puede instanciar clases arbitrarias.
TIPOS_PERMITIDOS = [
    ("app.agents.orquestador", "EnvioAgente"),
    ("app.agents.orquestador", "ResultadoSub"),
    ("app.agents.policy_gate", "DecisionGate"),
    ("app.agents.scopes", "UserContext"),
    ("app.agents.subgraph", "TextoPrivado"),
    ("app.models.schemas", "Aclaracion"),
    ("app.models.schemas", "Cita"),
    ("app.models.schemas", "Hallazgo"),
    ("app.models.schemas", "RespuestaConsulta"),
    ("app.models.schemas", "ToolCall"),
    ("app.models.schemas", "Turno"),
    ("app.models.schemas", "Usuario"),
]


def serializador_cifrado(clave: bytes) -> EncryptedSerializer:
    return EncryptedSerializer.from_pycryptodome_aes(
        serde=JsonPlusSerializer(allowed_msgpack_modules=TIPOS_PERMITIDOS), key=clave
    )


def url_psycopg(url: str) -> str:
    """La URL de SQLAlchemy (postgresql+psycopg://) en el formato de psycopg."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@contextmanager
def checkpointer_postgres(url: str, clave: bytes) -> Iterator[PostgresSaver]:
    with psycopg.Connection.connect(
        url_psycopg(url), autocommit=True, prepare_threshold=0, row_factory=dict_row
    ) as conexion:
        saver = PostgresSaver(conexion, serde=serializador_cifrado(clave))
        saver.setup()  # idempotente: crea sus tablas si faltan
        yield saver
