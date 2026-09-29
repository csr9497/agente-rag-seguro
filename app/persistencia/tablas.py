"""Esquema relacional (SQLAlchemy Core). Mismo esquema en SQLite y PostgreSQL."""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

metadata = MetaData()

roles = Table(
    "roles",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("nombre", String(80), nullable=False),
    Column("descripcion", String(300), nullable=False, default=""),
    Column("activo", Boolean, nullable=False, default=True),
    Column("creado_en", String(32), nullable=False),
)

rol_permisos = Table(
    "rol_permisos",
    metadata,
    Column("rol_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("permiso", String(64), primary_key=True),
)

rol_publica_para = Table(
    "rol_publica_para",
    metadata,
    Column("rol_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("destino_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)

documentos = Table(
    "documentos",
    metadata,
    Column("doc_id", String(400), primary_key=True),
    Column("titulo", String(200), nullable=False),
    Column("doc_hash", String(64), nullable=False),
    Column("chunks", Integer, nullable=False),
    Column("estado", String(16), nullable=False),
    Column("motivo_estado", Text),
    Column("subido_por", String(64)),
    Column("indexado_en", String(32), nullable=False),
)

documento_roles = Table(
    "documento_roles",
    metadata,
    Column("doc_id", ForeignKey("documentos.doc_id", ondelete="CASCADE"), primary_key=True),
    Column("rol_id", ForeignKey("roles.id"), primary_key=True),
)

conversaciones = Table(
    "conversaciones",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("rol_id", ForeignKey("roles.id"), nullable=False),
    Column("creada_en", String(32), nullable=False),
    # Propietario: solo esa persona ve la conversación (además de necesitar el rol).
    Column("usuario_id", String(128), nullable=True, index=True),
)

mensajes = Table(
    "mensajes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "conversacion_id",
        ForeignKey("conversaciones.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("pregunta", Text, nullable=False),
    Column("respuesta", Text, nullable=False),
    Column("sin_contexto", Boolean, nullable=False),
    Column("datos", JSON, nullable=False),  # citas, consultados, descartados, hallazgos
    Column("creado_en", String(32), nullable=False),
)

# ------------------------------------------------------------------ datos internos (data_query)
festivos = Table(
    "festivos",
    metadata,
    Column("fecha", String(10), primary_key=True),
    Column("nombre", String(120), nullable=False),
)

departamentos = Table(
    "departamentos",
    metadata,
    Column("id", String(40), primary_key=True),
    Column("nombre", String(120), nullable=False),
    Column("plantilla", Integer, nullable=False),
    Column("presupuesto_formacion_eur", Integer, nullable=False),
)

# ------------------------------------------------------------------ acciones (action_tool)
acciones = Table(
    "acciones",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("tipo", String(40), nullable=False),
    Column("rol_id", ForeignKey("roles.id"), nullable=False),
    Column("usuario", String(120), nullable=False),
    Column("datos", JSON, nullable=False),
    Column("estado", String(16), nullable=False),  # pendiente | ejecutada | rechazada | error
    Column("resultado", Text),
    Column("creada_en", String(32), nullable=False),
    Column("decidida_en", String(32)),
)
