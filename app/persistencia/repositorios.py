"""Repositorios de roles, documentos y conversaciones.

Interfaces (Protocol) + implementación SQLAlchemy Core válida para SQLite (local) y
PostgreSQL (Azure). La lógica de negocio (quién puede qué) vive en los servicios, no aquí.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy import (
    Engine,
    create_engine,
    delete,
    event,
    func,
    insert,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.pool import StaticPool

from app.persistencia import tablas as t
from app.persistencia.modelos import (
    Conversacion,
    DocumentoRegistrado,
    EstadoDocumento,
    Feedback,
    MensajeGuardado,
    ResumenConversacion,
    Rol,
    SolicitudAcceso,
)
from app.security.acl import es_rol

_CAMPOS_DATOS = {
    "citas",
    "documentos_consultados",
    "fragmentos_descartados",
    "hallazgos",
    "traza_id",
    "feedback",
    "desde_cache",
    "conversacional",
    "aclaracion",
    "consultas",
    "acciones",
}


def ahora() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ------------------------------------------------------------------ interfaces
class RepositorioRoles(Protocol):
    def listar(self, incluir_inactivos: bool = True) -> list[Rol]: ...
    def obtener(self, rol_id: str) -> Rol | None: ...
    def guardar(self, rol: Rol) -> Rol: ...
    def roles_de_usuario(self, usuario_id: str) -> list[str]: ...
    def asignaciones(self) -> dict[str, list[str]]: ...
    def asignar(self, usuario_id: str, roles: list[str], por: str) -> None: ...


class RepositorioDocumentos(Protocol):
    def registrar(self, doc: DocumentoRegistrado) -> None: ...
    def obtener(self, doc_id: str) -> DocumentoRegistrado | None: ...
    def listar(
        self, rol_id: str | None = None, estado: EstadoDocumento | None = None
    ) -> list[DocumentoRegistrado]: ...
    def marcar_estado(self, doc_id: str, estado: EstadoDocumento, motivo: str | None) -> None: ...
    def marcar_revocado(self, doc_id: str, revocado: bool) -> None: ...
    def eliminar(self, doc_id: str) -> bool: ...


class RepositorioConversaciones(Protocol):
    def crear(self, rol_id: str, usuario_id: str | None = None) -> Conversacion: ...
    def obtener(self, conversacion_id: str) -> Conversacion | None: ...
    def listar(
        self, usuario_id: str, rol_id: str, limite: int = 20
    ) -> list[ResumenConversacion]: ...
    def agregar_mensaje(
        self, conversacion_id: str, mensaje: MensajeGuardado
    ) -> MensajeGuardado: ...
    def registrar_feedback(
        self, conversacion_id: str, mensaje_id: int, feedback: Feedback
    ) -> MensajeGuardado | None: ...


# ------------------------------------------------------------------ motor
def crear_motor(url: str) -> Engine:
    """SQLite con claves foráneas activas. `sqlite://` (memoria) comparte UNA conexión: solo
    para tests secuenciales; con peticiones concurrentes usar un archivo SQLite o PostgreSQL."""
    if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    kwargs = (
        {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool}
        if url in {"sqlite://", "sqlite:///:memory:"}
        else {"connect_args": {"check_same_thread": False}}
        if url.startswith("sqlite")
        else {}
    )
    motor = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(motor, "connect")
        def _fk(conexion, _registro) -> None:
            conexion.execute("PRAGMA foreign_keys=ON")

    return motor


ROLES_INICIALES = [
    Rol(
        id="administrador",
        nombre="Administrador",
        descripcion="Gestiona roles y permisos. Sin acceso a contenido por defecto.",
        permisos=["administrar_roles"],
    ),
    Rol(
        id="rrhh",
        nombre="Recursos Humanos",
        descripcion="Contratación, bandas salariales y procesos de personas.",
        permisos=["gestionar_documentos"],
        publica_para=["public", "rrhh"],
    ),
    Rol(
        id="finanzas",
        nombre="Finanzas",
        descripcion="Nóminas, presupuestos y facturación.",
        permisos=["gestionar_documentos"],
        publica_para=["public"],
    ),
    Rol(
        id="public",
        nombre="Empleado general",
        descripcion="Políticas de la empresa: vacaciones, teletrabajo, conducta, beneficios.",
    ),
    # Roles de la orquestación multiagente (sus scopes: app/agents/scopes.py).
    Rol(id="hr_staff", nombre="Personal de RR.HH.",
        descripcion="Cola de casos de RR.HH. de sensibilidad normal."),
    Rol(id="hr_specialist", nombre="Especialista de RR.HH.",
        descripcion="Todos los casos de RR.HH., también los confidenciales."),
    Rol(id="it_support", nombre="Soporte IT",
        descripcion="Cola de tickets de soporte; aprueba los P1."),
    Rol(id="auditor", nombre="Auditor",
        descripcion="Lectura del registro de auditoría de los agentes."),
]  # fmt: skip


def _auditoria_solo_insercion(motor: Engine) -> None:
    """audit_log no admite UPDATE ni DELETE, ni siquiera del dueño de la tabla (idempotente)."""
    with motor.begin() as c:
        if motor.dialect.name == "postgresql":
            c.execute(text(
                "CREATE OR REPLACE FUNCTION audit_log_inmutable() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN "
                "RAISE EXCEPTION 'audit_log es de solo inserción'; END $$"
            ))  # fmt: skip
            c.execute(text("DROP TRIGGER IF EXISTS audit_log_inmutable ON audit_log"))
            c.execute(text(
                "CREATE TRIGGER audit_log_inmutable BEFORE UPDATE OR DELETE OR TRUNCATE "
                "ON audit_log FOR EACH STATEMENT EXECUTE FUNCTION audit_log_inmutable()"
            ))  # fmt: skip
        else:
            for operacion in ("UPDATE", "DELETE"):
                c.execute(text(
                    f"CREATE TRIGGER IF NOT EXISTS audit_log_sin_{operacion.lower()} "
                    f"BEFORE {operacion} ON audit_log "
                    "BEGIN SELECT RAISE(ABORT, 'audit_log es de solo inserción'); END"
                ))  # fmt: skip


def _migrar(motor: Engine) -> None:
    """Migraciones aditivas e idempotentes para bases creadas con un esquema anterior
    (create_all no altera tablas existentes)."""
    columnas = {c["name"] for c in inspect(motor).get_columns("conversaciones")}
    if "usuario_id" not in columnas:
        with motor.begin() as c:
            c.execute(text("ALTER TABLE conversaciones ADD COLUMN usuario_id VARCHAR(128)"))
    columnas = {c["name"] for c in inspect(motor).get_columns("documentos")}
    with motor.begin() as c:
        if "revocado" not in columnas:
            c.execute(
                text("ALTER TABLE documentos ADD COLUMN revocado BOOLEAN NOT NULL DEFAULT FALSE")
            )
        if "expira_en" not in columnas:
            c.execute(text("ALTER TABLE documentos ADD COLUMN expira_en VARCHAR(32)"))


def inicializar(motor: Engine) -> None:
    """Crea las tablas y los roles iniciales que falten (sin tocar los existentes)."""
    t.metadata.create_all(motor)
    _migrar(motor)
    _auditoria_solo_insercion(motor)
    from app.persistencia.rls import preparar_rls

    preparar_rls(motor)
    from app.datos.catalogo import sembrar_datos_ejemplo

    sembrar_datos_ejemplo(motor)
    roles = SqlRepositorioRoles(motor)
    # Solo los que faltan: nunca se modifican roles existentes (los gestiona el administrador).
    faltan = [r for r in ROLES_INICIALES if roles.obtener(r.id) is None]
    # Primero todos los roles (sin relaciones) para que publica_para no rompa las FK.
    for rol in faltan:
        roles.guardar(rol.model_copy(update={"publica_para": []}))
    for rol in faltan:
        roles.guardar(rol)


# ------------------------------------------------------------------ roles
class SqlRepositorioRoles:
    def __init__(self, motor: Engine) -> None:
        self._motor = motor

    def listar(self, incluir_inactivos: bool = True) -> list[Rol]:
        consulta = select(t.roles).order_by(t.roles.c.nombre)
        if not incluir_inactivos:
            consulta = consulta.where(t.roles.c.activo.is_(True))
        with self._motor.connect() as c:
            filas = c.execute(consulta).mappings().all()
            permisos = c.execute(select(t.rol_permisos)).all()
            publica = c.execute(select(t.rol_publica_para)).all()
        return [
            Rol(
                **fila,
                permisos=[p for r, p in permisos if r == fila["id"]],
                publica_para=[d for r, d in publica if r == fila["id"]],
            )
            for fila in filas
        ]

    def obtener(self, rol_id: str) -> Rol | None:
        return next((r for r in self.listar() if r.id == rol_id), None)

    # ------------------------------------------------------------ asignaciones a personas
    def roles_de_usuario(self, usuario_id: str) -> list[str]:
        consulta = select(t.usuario_roles.c.rol_id).where(
            t.usuario_roles.c.usuario_id == usuario_id
        )
        with self._motor.connect() as c:
            return sorted(c.execute(consulta).scalars())

    def asignaciones(self) -> dict[str, list[str]]:
        with self._motor.connect() as c:
            filas = c.execute(select(t.usuario_roles.c.usuario_id, t.usuario_roles.c.rol_id)).all()
        resultado: dict[str, list[str]] = {}
        for usuario, rol in sorted(filas):
            resultado.setdefault(usuario, []).append(rol)
        return resultado

    def asignar(self, usuario_id: str, roles: list[str], por: str) -> None:
        """Sustituye los roles asignados a la persona (lista vacía = sin roles)."""
        with self._motor.begin() as c:
            c.execute(delete(t.usuario_roles).where(t.usuario_roles.c.usuario_id == usuario_id))
            if roles:
                c.execute(
                    insert(t.usuario_roles),
                    [{"usuario_id": usuario_id, "rol_id": r, "asignado_por": por,
                      "asignado_en": ahora()} for r in sorted(set(roles))],
                )  # fmt: skip

    def guardar(self, rol: Rol) -> Rol:
        rol = rol if rol.creado_en else rol.model_copy(update={"creado_en": ahora()})
        datos = rol.model_dump(include={"id", "nombre", "descripcion", "activo", "creado_en"})
        with self._motor.begin() as c:
            existe = c.execute(select(t.roles.c.id).where(t.roles.c.id == rol.id)).first()
            if existe:
                c.execute(update(t.roles).where(t.roles.c.id == rol.id).values(**datos))
            else:
                c.execute(insert(t.roles).values(**datos))
            c.execute(delete(t.rol_permisos).where(t.rol_permisos.c.rol_id == rol.id))
            c.execute(delete(t.rol_publica_para).where(t.rol_publica_para.c.rol_id == rol.id))
            if rol.permisos:
                c.execute(
                    insert(t.rol_permisos), [{"rol_id": rol.id, "permiso": p} for p in rol.permisos]
                )
            if rol.publica_para:
                c.execute(
                    insert(t.rol_publica_para),
                    [{"rol_id": rol.id, "destino_id": d} for d in rol.publica_para],
                )
        return rol


# ------------------------------------------------------------------ documentos
class SqlRepositorioDocumentos:
    def __init__(self, motor: Engine) -> None:
        self._motor = motor

    def registrar(self, doc: DocumentoRegistrado) -> None:
        """La ACL se reparte en sus tablas: roles, departamentos (FK) y usuarios."""
        datos = doc.model_dump(exclude={"roles"})
        roles = [g for g in doc.roles if es_rol(g)]
        deptos = [g.removeprefix("dept:") for g in doc.roles if g.startswith("dept:")]
        usuarios = [g.removeprefix("user:") for g in doc.roles if g.startswith("user:")]
        with self._motor.begin() as c:
            c.execute(delete(t.documentos).where(t.documentos.c.doc_id == doc.doc_id))
            c.execute(insert(t.documentos).values(**datos))
            for tabla, columna, valores in (
                (t.documento_roles, "rol_id", roles),
                (t.documento_departamentos, "departamento_id", deptos),
                (t.documento_usuarios, "usuario_id", usuarios),
            ):
                if valores:
                    c.execute(insert(tabla), [{"doc_id": doc.doc_id, columna: v} for v in valores])

    def marcar_revocado(self, doc_id: str, revocado: bool) -> None:
        with self._motor.begin() as c:
            c.execute(
                update(t.documentos)
                .where(t.documentos.c.doc_id == doc_id)
                .values(revocado=revocado)
            )

    def obtener(self, doc_id: str) -> DocumentoRegistrado | None:
        return next(iter(self._consultar(t.documentos.c.doc_id == doc_id)), None)

    def listar(
        self, rol_id: str | None = None, estado: EstadoDocumento | None = None
    ) -> list[DocumentoRegistrado]:
        docs = self._consultar(t.documentos.c.estado == estado if estado else None)
        return [d for d in docs if rol_id is None or rol_id in d.roles]

    def marcar_estado(self, doc_id: str, estado: EstadoDocumento, motivo: str | None) -> None:
        with self._motor.begin() as c:
            c.execute(
                update(t.documentos)
                .where(t.documentos.c.doc_id == doc_id)
                .values(estado=estado, motivo_estado=motivo)
            )

    def eliminar(self, doc_id: str) -> bool:
        with self._motor.begin() as c:
            return (
                c.execute(delete(t.documentos).where(t.documentos.c.doc_id == doc_id)).rowcount > 0
            )

    def _consultar(self, condicion) -> list[DocumentoRegistrado]:
        consulta = select(t.documentos).order_by(t.documentos.c.doc_id)
        if condicion is not None:
            consulta = consulta.where(condicion)
        with self._motor.connect() as c:
            filas = c.execute(consulta).mappings().all()
            acl: dict[str, list[str]] = {}
            for prefijo, tabla in (
                ("", t.documento_roles),
                ("dept:", t.documento_departamentos),
                ("user:", t.documento_usuarios),
            ):
                for doc_id, valor in c.execute(select(tabla)).all():
                    acl.setdefault(doc_id, []).append(f"{prefijo}{valor}")
        return [DocumentoRegistrado(**fila, roles=acl.get(fila["doc_id"], [])) for fila in filas]


# ------------------------------------------------------------------ solicitudes de acceso
class SqlRepositorioSolicitudesAcceso:
    def __init__(self, motor: Engine) -> None:
        self._motor = motor

    def crear(self, solicitud: SolicitudAcceso) -> SolicitudAcceso:
        guardada = solicitud.model_copy(
            update={"id": solicitud.id or str(uuid.uuid4()), "creada_en": ahora()}
        )
        with self._motor.begin() as c:
            c.execute(insert(t.solicitudes_acceso).values(**guardada.model_dump()))
        return guardada

    def de_solicitante(self, solicitante_id: str) -> list[SolicitudAcceso]:
        with self._motor.connect() as c:
            filas = c.execute(
                select(t.solicitudes_acceso)
                .where(t.solicitudes_acceso.c.solicitante_id == solicitante_id)
                .order_by(t.solicitudes_acceso.c.creada_en)
            ).mappings()
            return [SolicitudAcceso(**f) for f in filas]


# ------------------------------------------------------------------ departamentos de personas
class SqlRepositorioDepartamentosUsuario:
    def __init__(self, motor: Engine) -> None:
        self._motor = motor

    def de(self, usuario_id: str) -> list[str]:
        with self._motor.connect() as c:
            filas = c.execute(
                select(t.usuario_departamentos.c.departamento_id)
                .where(t.usuario_departamentos.c.usuario_id == usuario_id)
                .order_by(t.usuario_departamentos.c.departamento_id)
            ).all()
        return [f[0] for f in filas]

    def existentes(self) -> set[str]:
        with self._motor.connect() as c:
            return {f[0] for f in c.execute(select(t.departamentos.c.id)).all()}

    def iniciales(self, asignaciones: dict[str, list[str]]) -> None:
        """Arranque: añade lo que falte (nunca quita) e ignora departamentos inexistentes."""
        existentes = self.existentes()
        for usuario_id, deptos in asignaciones.items():
            usuario_id = usuario_id.strip().lower()
            actuales = set(self.de(usuario_id))
            if nuevos := (set(deptos) & existentes) - actuales:
                self.asignar(usuario_id, sorted(actuales | nuevos))

    def asignar(self, usuario_id: str, departamentos: list[str]) -> None:
        """Sustituye los departamentos de la persona (FK: deben existir)."""
        with self._motor.begin() as c:
            c.execute(
                delete(t.usuario_departamentos).where(
                    t.usuario_departamentos.c.usuario_id == usuario_id
                )
            )
            if departamentos:
                c.execute(
                    insert(t.usuario_departamentos),
                    [{"usuario_id": usuario_id, "departamento_id": d} for d in set(departamentos)],
                )


# ------------------------------------------------------------------ conversaciones
class SqlRepositorioConversaciones:
    def __init__(self, motor: Engine) -> None:
        self._motor = motor

    def crear(self, rol_id: str, usuario_id: str | None = None) -> Conversacion:
        conv = Conversacion(
            id=str(uuid.uuid4()), rol_id=rol_id, creada_en=ahora(), usuario_id=usuario_id
        )
        with self._motor.begin() as c:
            c.execute(
                insert(t.conversaciones).values(
                    id=conv.id, rol_id=rol_id, creada_en=conv.creada_en, usuario_id=usuario_id
                )
            )
        return conv

    def listar(self, usuario_id: str, rol_id: str, limite: int = 20) -> list[ResumenConversacion]:
        """Conversaciones con al menos un mensaje, la más reciente primero."""
        n = func.count(t.mensajes.c.id).label("n")
        primero = func.min(t.mensajes.c.id).label("primero")
        consulta = (
            select(t.conversaciones.c.id, t.conversaciones.c.creada_en, n, primero)
            .join(t.mensajes, t.mensajes.c.conversacion_id == t.conversaciones.c.id)
            .where(t.conversaciones.c.usuario_id == usuario_id, t.conversaciones.c.rol_id == rol_id)
            .group_by(t.conversaciones.c.id, t.conversaciones.c.creada_en)
            .order_by(func.max(t.mensajes.c.id).desc())
            .limit(limite)
        )
        with self._motor.connect() as c:
            filas = c.execute(consulta).all()
            ids = [f.primero for f in filas]
            preguntas = dict(
                c.execute(
                    select(t.mensajes.c.id, t.mensajes.c.pregunta).where(t.mensajes.c.id.in_(ids))
                ).all()
            )
        return [
            ResumenConversacion(
                id=f.id,
                rol_id=rol_id,
                creada_en=f.creada_en,
                mensajes=f.n,
                titulo=preguntas.get(f.primero, "")[:80],
            )
            for f in filas
        ]

    def obtener(self, conversacion_id: str) -> Conversacion | None:
        with self._motor.connect() as c:
            fila = (
                c.execute(select(t.conversaciones).where(t.conversaciones.c.id == conversacion_id))
                .mappings()
                .first()
            )
            if fila is None:
                return None
            msgs = (
                c.execute(
                    select(t.mensajes)
                    .where(t.mensajes.c.conversacion_id == conversacion_id)
                    .order_by(t.mensajes.c.id)
                )
                .mappings()
                .all()
            )
        return Conversacion(
            **fila,
            mensajes=[
                MensajeGuardado(
                    id=m["id"],
                    pregunta=m["pregunta"],
                    respuesta=m["respuesta"],
                    sin_contexto=m["sin_contexto"],
                    creado_en=m["creado_en"],
                    **m["datos"],
                )
                for m in msgs
            ],
        )

    def agregar_mensaje(self, conversacion_id: str, mensaje: MensajeGuardado) -> MensajeGuardado:
        mensaje = mensaje.model_copy(update={"creado_en": mensaje.creado_en or ahora()})
        datos = mensaje.model_dump(
            mode="json",
            include=_CAMPOS_DATOS,
        )
        with self._motor.begin() as c:
            resultado = c.execute(
                insert(t.mensajes).values(
                    conversacion_id=conversacion_id,
                    pregunta=mensaje.pregunta,
                    respuesta=mensaje.respuesta,
                    sin_contexto=mensaje.sin_contexto,
                    datos=datos,
                    creado_en=mensaje.creado_en,
                )
            )
        return mensaje.model_copy(update={"id": resultado.inserted_primary_key[0]})

    def registrar_feedback(
        self, conversacion_id: str, mensaje_id: int, feedback: Feedback
    ) -> MensajeGuardado | None:
        conv = self.obtener(conversacion_id)
        mensaje = next((m for m in conv.mensajes if m.id == mensaje_id), None) if conv else None
        if mensaje is None:
            return None
        feedback = feedback.model_copy(update={"creado_en": feedback.creado_en or ahora()})
        actualizado = mensaje.model_copy(update={"feedback": feedback})
        with self._motor.begin() as c:
            c.execute(
                update(t.mensajes)
                .where(t.mensajes.c.id == mensaje_id)
                .values(datos=actualizado.model_dump(mode="json", include=_CAMPOS_DATOS))
            )
        return actualizado
