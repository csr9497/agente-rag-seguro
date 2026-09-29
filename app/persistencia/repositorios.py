"""Repositorios de roles, documentos y conversaciones.

Interfaces (Protocol) + implementación SQLAlchemy Core válida para SQLite (local) y
PostgreSQL (Azure). La lógica de negocio (quién puede qué) vive en los servicios, no aquí.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy import Engine, create_engine, delete, event, insert, select, update
from sqlalchemy.pool import StaticPool

from app.persistencia import tablas as t
from app.persistencia.modelos import (
    Conversacion,
    DocumentoRegistrado,
    EstadoDocumento,
    Feedback,
    MensajeGuardado,
    Rol,
)

_CAMPOS_DATOS = {
    "citas",
    "documentos_consultados",
    "fragmentos_descartados",
    "hallazgos",
    "traza_id",
    "feedback",
}


def ahora() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ------------------------------------------------------------------ interfaces
class RepositorioRoles(Protocol):
    def listar(self, incluir_inactivos: bool = True) -> list[Rol]: ...
    def obtener(self, rol_id: str) -> Rol | None: ...
    def guardar(self, rol: Rol) -> Rol: ...


class RepositorioDocumentos(Protocol):
    def registrar(self, doc: DocumentoRegistrado) -> None: ...
    def obtener(self, doc_id: str) -> DocumentoRegistrado | None: ...
    def listar(
        self, rol_id: str | None = None, estado: EstadoDocumento | None = None
    ) -> list[DocumentoRegistrado]: ...
    def marcar_estado(self, doc_id: str, estado: EstadoDocumento, motivo: str | None) -> None: ...
    def eliminar(self, doc_id: str) -> bool: ...


class RepositorioConversaciones(Protocol):
    def crear(self, rol_id: str) -> Conversacion: ...
    def obtener(self, conversacion_id: str) -> Conversacion | None: ...
    def agregar_mensaje(
        self, conversacion_id: str, mensaje: MensajeGuardado
    ) -> MensajeGuardado: ...
    def registrar_feedback(
        self, conversacion_id: str, mensaje_id: int, feedback: Feedback
    ) -> MensajeGuardado | None: ...


# ------------------------------------------------------------------ motor
def crear_motor(url: str) -> Engine:
    """SQLite con claves foráneas activas; `sqlite://` (memoria) comparte una conexión."""
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
        descripcion="Nóminas, bandas salariales y procesos de RRHH.",
        permisos=["gestionar_documentos"],
        publica_para=["public", "rrhh"],
    ),
    Rol(
        id="public",
        nombre="Empleado general",
        descripcion="Políticas generales: vacaciones, teletrabajo, onboarding.",
    ),
]


def inicializar(motor: Engine) -> None:
    """Crea las tablas y, si no hay roles, carga los iniciales."""
    t.metadata.create_all(motor)
    roles = SqlRepositorioRoles(motor)
    if not roles.listar():
        # Primero todos los roles (sin relaciones) para que publica_para no rompa las FK.
        for rol in ROLES_INICIALES:
            roles.guardar(rol.model_copy(update={"publica_para": []}))
        for rol in ROLES_INICIALES:
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
        datos = doc.model_dump(exclude={"roles"})
        with self._motor.begin() as c:
            c.execute(delete(t.documentos).where(t.documentos.c.doc_id == doc.doc_id))
            c.execute(insert(t.documentos).values(**datos))
            c.execute(
                insert(t.documento_roles), [{"doc_id": doc.doc_id, "rol_id": r} for r in doc.roles]
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
            roles = c.execute(select(t.documento_roles)).all()
        return [
            DocumentoRegistrado(**fila, roles=[r for d, r in roles if d == fila["doc_id"]])
            for fila in filas
        ]


# ------------------------------------------------------------------ conversaciones
class SqlRepositorioConversaciones:
    def __init__(self, motor: Engine) -> None:
        self._motor = motor

    def crear(self, rol_id: str) -> Conversacion:
        conv = Conversacion(id=str(uuid.uuid4()), rol_id=rol_id, creada_en=ahora())
        with self._motor.begin() as c:
            c.execute(insert(t.conversaciones).values(**conv.model_dump(exclude={"mensajes"})))
        return conv

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
