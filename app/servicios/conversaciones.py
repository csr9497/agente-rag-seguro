"""Conversaciones con un rol fijo. Cada pregunta pasa por el orquestador multiagente; el
historial guarda, por mensaje, los documentos consultados y citados, los agentes que actuaron
y las aprobaciones pendientes."""

import logging
import uuid
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from app.models.schemas import Turno, Usuario
from app.persistencia.modelos import (
    Conversacion,
    Feedback,
    MensajeGuardado,
    ResumenConversacion,
)
from app.persistencia.repositorios import RepositorioConversaciones
from app.security.acl import grupos_efectivos
from app.security.deteccion import TIPOS_PII, enmascarar_pii
from app.servicios.errores import NoEncontradoError, PermisoDenegadoError
from app.servicios.roles import ServicioRoles

logger = logging.getLogger(__name__)


class ServicioConversaciones:
    def __init__(
        self,
        repo: RepositorioConversaciones,
        roles: ServicioRoles,
        max_turnos: int = 3,
        departamentos_de: Callable[[str], list[str]] = lambda _: [],
    ) -> None:
        self._repo = repo
        self._roles = roles
        self._max_turnos = max_turnos
        self._departamentos_de = departamentos_de
        # Se conectan al arrancar (necesitan el checkpointer): app/main.py → usar_orquestador.
        self._orquestador: Any = None
        self._aprobaciones: Any = None

    def usar_orquestador(self, orquestador: Any, aprobaciones: Any) -> None:
        """Las preguntas pasan por el orquestador multiagente y sus aprobaciones."""
        self._orquestador, self._aprobaciones = orquestador, aprobaciones

    def iniciar(self, usuario: Usuario, rol_id: str) -> Conversacion:
        rol = self._roles.actuar_como(usuario, rol_id)
        return self._repo.crear(rol.id, usuario.id)

    def listar(self, usuario: Usuario, rol_id: str) -> list[ResumenConversacion]:
        """Historial propio con un rol que el usuario puede usar ahora."""
        rol = self._roles.actuar_como(usuario, rol_id)
        return self._repo.listar(usuario.id, rol.id)

    def obtener(self, usuario: Usuario, conversacion_id: str) -> Conversacion:
        conv = self._repo.obtener(conversacion_id)
        # Ajena (otra persona, aunque tenga el mismo rol) o sin propietario (anterior a la
        # migración de usuario_id): igual que inexistente.
        if conv is None or conv.usuario_id != usuario.id:
            raise NoEncontradoError("Conversación no encontrada")
        try:
            self._roles.actuar_como(usuario, conv.rol_id)
        except PermisoDenegadoError as exc:
            # Rol desactivado o no disponible: no se revela si la conversación existe.
            raise NoEncontradoError("Conversación no encontrada") from exc
        return conv

    def preguntar(self, usuario: Usuario, conversacion_id: str, pregunta: str) -> MensajeGuardado:
        conv = self.obtener(usuario, conversacion_id)
        # El agente recibe el rol de la conversación (nunca otro) más los grupos que dan acceso
        # a documentos internos («dept:») y restringidos («user:») de esta persona.
        grupos = grupos_efectivos(usuario.id, [conv.rol_id], self._departamentos_de(usuario.id))
        if self._orquestador is None:
            raise RuntimeError("El orquestador no está conectado (usar_orquestador al arrancar)")
        # Un hilo por mensaje (checkpointer): conversación + uuid, para enlazar las aprobaciones.
        thread_id = f"{conv.id}:{uuid.uuid4()}"
        r = self._orquestador.consultar(
            pregunta, Usuario(id=usuario.id, groups=grupos), conversacion_id=conv.id,
            historial=self._historial(conv), thread_id=thread_id,
        )  # fmt: skip
        mensaje = MensajeGuardado(pregunta=r.pregunta_procesada, **_campos_respuesta(r))
        return self._repo.agregar_mensaje(conv.id, mensaje)

    def aprobaciones_pendientes(self, usuario: Usuario) -> list["AprobacionVista"]:
        """Lo que esta persona puede resolver: sus confirmaciones y la cola de sus roles
        (nunca las suyas que exigen a otra persona)."""
        if self._aprobaciones is None:
            return []
        roles = self._roles_de_quien_decide(usuario)
        vistas = []
        for a in self._aprobaciones.pendientes(roles=sorted(roles), user_id=usuario.id):
            if a.tipo != "confirm_user" and a.user_id == usuario.id:
                continue
            vistas.append(AprobacionVista.de(a, propia=a.user_id == usuario.id))
        return vistas

    def decidir(
        self,
        usuario: Usuario,
        aprobacion_id: str,
        aprobar: bool,
        motivo: str | None = None,
        edited_args: dict | None = None,
        respuesta: str | None = None,
    ) -> "ResultadoDecision":
        """Quien decide sale del token (nunca del cuerpo). confirm_user: solo el solicitante;
        approve_staff / escalate_human: alguien con el rol pedido que no sea el solicitante."""
        if self._orquestador is None or self._aprobaciones is None:
            raise NoEncontradoError("Aprobación no encontrada")
        a = self._aprobaciones.obtener(aprobacion_id)
        if a is None or a.estado != "pendiente":
            raise NoEncontradoError("Aprobación no encontrada o ya resuelta")
        roles = self._roles_de_quien_decide(usuario)
        if a.tipo == "confirm_user":
            if a.user_id != usuario.id:  # ajena: igual que inexistente
                raise NoEncontradoError("Aprobación no encontrada o ya resuelta")
        elif a.user_id == usuario.id or (a.approver_role or "") not in roles:
            raise PermisoDenegadoError("No puedes resolver esta aprobación")
        pendiente = next(
            (p for p in self._orquestador.pendientes(a.thread_id) if p.aprobacion_id == a.id), None
        )
        if pendiente is None:
            raise NoEncontradoError("La aprobación ya no está pendiente")
        r = self._orquestador.decidir(
            a.thread_id, pendiente.interrupt_id, aprobar, usuario.id, edited_args=edited_args,
            motivo=motivo, respuesta=respuesta, roles_aprobador=roles,
        )  # fmt: skip
        mensaje = self._actualizar_mensaje_del_hilo(a.thread_id, r)
        estado = self._aprobaciones.obtener(a.id)
        return ResultadoDecision(
            estado=estado.estado if estado else "pendiente",
            # Al solicitante, su mensaje actualizado; a quien aprueba por rol, solo el estado
            # (no ve la conversación de otra persona).
            mensaje=mensaje if a.user_id == usuario.id else None,
        )

    def _actualizar_mensaje_del_hilo(self, thread_id: str, r: Any) -> MensajeGuardado | None:
        conv = self._repo.obtener(thread_id.split(":", 1)[0])
        mensaje = (
            next((m for m in conv.mensajes if m.thread_id == thread_id), None) if conv else None
        )
        if mensaje is None:
            return None
        return self._repo.actualizar_mensaje(mensaje.model_copy(update=_campos_respuesta(r)))

    def _roles_de_quien_decide(self, usuario: Usuario) -> set[str]:
        return {r.id for r in self._roles.disponibles(usuario)} | set(usuario.groups)

    def _historial(self, conv: Conversacion) -> list[Turno]:
        """Últimos turnos útiles: sin consultas bloqueadas (no aportan y podrían reintroducir
        instrucciones maliciosas)."""
        utiles = [m for m in conv.mensajes if not any(h.accion == "bloquear" for h in m.hallazgos)]
        return (
            [Turno(pregunta=m.pregunta, respuesta=m.respuesta) for m in utiles[-self._max_turnos :]]
            if self._max_turnos
            else []
        )

    def valorar(
        self, usuario: Usuario, conversacion_id: str, mensaje_id: int, feedback: Feedback
    ) -> MensajeGuardado:
        self.obtener(usuario, conversacion_id)  # misma validación de rol que el historial
        if feedback.comentario:
            feedback = feedback.model_copy(
                update={"comentario": enmascarar_pii(feedback.comentario, TIPOS_PII)[0]}
            )
        mensaje = self._repo.registrar_feedback(conversacion_id, mensaje_id, feedback)
        if mensaje is None:
            raise NoEncontradoError("Mensaje no encontrado")
        self._enviar_a_langsmith(mensaje, feedback)
        return mensaje

    def _enviar_a_langsmith(self, mensaje: MensajeGuardado, feedback: Feedback) -> None:
        cliente = getattr(self._orquestador, "cliente_trazas", None)
        if cliente is None or mensaje.traza_id is None:
            return
        try:
            cliente.create_feedback(
                run_id=mensaje.traza_id,
                key="valoracion_usuario",
                score=1 if feedback.valoracion == "positiva" else 0,
                comment=feedback.comentario,
            )
        except Exception:  # noqa: BLE001 - la valoración ya está guardada; LangSmith es secundario
            logger.warning("No se pudo enviar el feedback a LangSmith", exc_info=True)


def _campos_respuesta(r: Any) -> dict[str, Any]:
    """Lo que el mensaje guarda de un resultado del orquestador."""
    return {
        "respuesta": r.respuesta.respuesta, "sin_contexto": r.respuesta.sin_contexto,
        "citas": r.respuesta.citas, "documentos_consultados": r.documentos_consultados,
        "hallazgos": r.hallazgos, "traza_id": r.traza_id or None, "desde_cache": r.desde_cache,
        "conversacional": r.respuesta.conversacional, "aclaracion": r.respuesta.aclaracion,
        "aprobaciones": r.aprobaciones, "thread_id": r.thread_id, "agentes": r.agentes,
    }  # fmt: skip


class AprobacionVista(BaseModel):
    id: str
    tipo: str
    agente: str
    accion: str
    detalle: str
    riesgo: str
    vence: str
    solicitante: str
    propia: bool

    @classmethod
    def de(cls, a: Any, propia: bool) -> "AprobacionVista":
        return cls(
            id=a.id, tipo=a.tipo, agente=a.agent, accion=a.tool, detalle=a.args_preview,
            riesgo=a.risk, vence=a.expires_at, solicitante=a.user_id, propia=propia,
        )  # fmt: skip


class ResultadoDecision(BaseModel):
    estado: str
    mensaje: MensajeGuardado | None = None
