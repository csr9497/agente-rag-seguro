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
    false,
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

# Roles asignados a personas desde la app (login sin roles en el token, p. ej. GitHub). Se
# suman a los roles del token (app roles de Entra ID), nunca los sustituyen.
usuario_roles = Table(
    "usuario_roles",
    metadata,
    Column("usuario_id", String(160), primary_key=True),
    Column("rol_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("asignado_por", String(160), nullable=False),
    Column("asignado_en", String(32), nullable=False),
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
    # Re-chequeo antes de generar: un documento revocado o caducado deja de verse aunque
    # siga en el índice.
    Column("revocado", Boolean, nullable=False, server_default=false()),
    Column("expira_en", String(32)),
)

# ACL del documento (app/security/acl.py): roles (confidencial), departamentos (interno) y
# usuarios (restringido). En el índice van juntos en acl_groups («dept:<d>», «user:<id>»).
documento_roles = Table(
    "documento_roles",
    metadata,
    Column("doc_id", ForeignKey("documentos.doc_id", ondelete="CASCADE"), primary_key=True),
    Column("rol_id", ForeignKey("roles.id"), primary_key=True),
)

documento_departamentos = Table(
    "documento_departamentos",
    metadata,
    Column("doc_id", ForeignKey("documentos.doc_id", ondelete="CASCADE"), primary_key=True),
    Column("departamento_id", ForeignKey("departamentos.id"), primary_key=True),
)

documento_usuarios = Table(
    "documento_usuarios",
    metadata,
    Column("doc_id", ForeignKey("documentos.doc_id", ondelete="CASCADE"), primary_key=True),
    Column("usuario_id", String(128), primary_key=True),
)

# Solicitudes de acceso a documentos (rag_agent.request_document_access). doc_id y
# propietario solo se rellenan si el título coincide: la respuesta al usuario es la misma.
solicitudes_acceso = Table(
    "solicitudes_acceso",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("solicitante_id", String(160), nullable=False, index=True),
    Column("titulo", String(200), nullable=False),
    Column("motivo", Text, nullable=False),
    Column("doc_id", String(400)),
    Column("propietario_id", String(160)),
    Column("estado", String(16), nullable=False),  # pendiente | concedida | denegada
    Column("trace_id", String(64)),
    Column("creada_en", String(32), nullable=False),
)

# Auditoría de las tools de los agentes: una fila por llamada con su decisión. Solo inserción:
# un trigger impide UPDATE y DELETE (también al dueño de la tabla).
audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("trace_id", String(64), nullable=False, index=True),
    Column("user_id", String(160), nullable=False, index=True),
    Column("agent", String(64), nullable=False),
    Column("tool", String(64), nullable=False),
    Column("args_hash", String(64), nullable=False),
    Column("decision", String(16), nullable=False),  # allow | deny | approved | rejected
    Column("approver_id", String(160)),
    Column("reason", Text),
    Column("fecha", String(32), nullable=False),
)

# Aprobaciones humanas pendientes (interrupt de los agentes). Vencen a las 24 h.
approvals = Table(
    "approvals",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("thread_id", String(160), nullable=False),
    Column("trace_id", String(64), nullable=False),
    Column("agent", String(64), nullable=False),
    Column("tool", String(64), nullable=False),
    Column("user_id", String(160), nullable=False, index=True),  # solicitante
    Column("tipo", String(16), nullable=False),  # confirm_user | approve_staff
    Column("approver_role", String(64)),
    Column("args_preview", Text, nullable=False),
    Column("risk", String(16), nullable=False),
    Column("estado", String(16), nullable=False, index=True),
    Column("approver_id", String(160)),
    Column("motivo", Text),
    Column("created_at", String(32), nullable=False),
    Column("expires_at", String(32), nullable=False),
    Column("decided_at", String(32)),
)

# Casos de RR.HH. (hr_agent). En PostgreSQL con Row Level Security (app/persistencia/rls.py):
# cada fila la ve su solicitante, hr_staff las de sensibilidad normal y hr_specialist todas.
hr_cases = Table(
    "hr_cases",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("requester_id", String(160), nullable=False, index=True),  # del token, nunca del LLM
    Column("category", String(40), nullable=False),
    Column("sensitivity", String(16), nullable=False),  # normal | confidential (por categoría)
    Column("summary", Text, nullable=False),
    Column("status", String(16), nullable=False),  # open | closed (lo cierra RR.HH., no un agente)
    Column("created_by_agent", String(64), nullable=False),
    Column("trace_id", String(64), nullable=False),  # enlaza con audit_log y la traza
    Column("created_at", String(32), nullable=False),
)

hr_case_notes = Table(
    "hr_case_notes",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("case_id", ForeignKey("hr_cases.id"), nullable=False, index=True),
    Column("author_id", String(160), nullable=False),
    Column("note", Text, nullable=False),
    Column("created_at", String(32), nullable=False),
)

# Tickets de soporte (support_agent), con RLS: su solicitante y la cola de it_support.
tickets = Table(
    "tickets",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("requester_id", String(160), nullable=False, index=True),  # del token
    Column("category", String(40), nullable=False),
    Column("priority", String(4), nullable=False),  # P1 (crítico, lo aprueba it_support) … P4
    Column("title", String(200), nullable=False),
    Column("steps", Text, nullable=False),  # qué pasa y qué se probó
    Column("status", String(16), nullable=False),  # open | closed (lo cierra soporte, no un agente)
    Column("created_by_agent", String(64), nullable=False),
    Column("trace_id", String(64), nullable=False),
    Column("created_at", String(32), nullable=False),
)

ticket_comments = Table(
    "ticket_comments",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("ticket_id", ForeignKey("tickets.id"), nullable=False, index=True),
    Column("author_id", String(160), nullable=False),
    Column("comment", Text, nullable=False),
    Column("created_at", String(32), nullable=False),
)

# Departamento(s) de cada persona: dan acceso a los documentos internos de ese departamento.
usuario_departamentos = Table(
    "usuario_departamentos",
    metadata,
    Column("usuario_id", String(160), primary_key=True),
    Column("departamento_id", ForeignKey("departamentos.id"), primary_key=True),
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
