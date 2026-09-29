"""Conversaciones con un rol fijo. El historial guarda, por mensaje, los documentos
consultados, los citados y cuántos fragmentos se descartaron por permisos."""

import logging

from app.graph.agente import Agente
from app.models.schemas import Usuario
from app.persistencia.modelos import Conversacion, Feedback, MensajeGuardado
from app.persistencia.repositorios import RepositorioConversaciones
from app.security.deteccion import TIPOS_PII, enmascarar_pii
from app.servicios.errores import NoEncontradoError, PermisoDenegadoError
from app.servicios.roles import ServicioRoles

logger = logging.getLogger(__name__)


class ServicioConversaciones:
    def __init__(
        self, repo: RepositorioConversaciones, roles: ServicioRoles, agente: Agente
    ) -> None:
        self._repo = repo
        self._roles = roles
        self._agente = agente

    def iniciar(self, usuario: Usuario, rol_id: str) -> Conversacion:
        rol = self._roles.actuar_como(usuario, rol_id)
        return self._repo.crear(rol.id)

    def obtener(self, usuario: Usuario, conversacion_id: str) -> Conversacion:
        conv = self._repo.obtener(conversacion_id)
        if conv is None:
            raise NoEncontradoError("Conversación no encontrada")
        try:
            self._roles.actuar_como(usuario, conv.rol_id)
        except PermisoDenegadoError as exc:
            # Rol desactivado o no disponible: no se revela si la conversación existe.
            raise NoEncontradoError("Conversación no encontrada") from exc
        return conv

    def preguntar(self, usuario: Usuario, conversacion_id: str, pregunta: str) -> MensajeGuardado:
        conv = self.obtener(usuario, conversacion_id)
        # El agente solo recibe el rol de la conversación: nunca más grupos que ese.
        resultado = self._agente.consultar_detallado(
            pregunta, Usuario(id=usuario.id, groups=[conv.rol_id]), conversacion_id=conv.id
        )
        mensaje = MensajeGuardado(
            pregunta=resultado.pregunta_procesada,
            respuesta=resultado.respuesta.respuesta,
            sin_contexto=resultado.respuesta.sin_contexto,
            citas=resultado.respuesta.citas,
            documentos_consultados=resultado.documentos_consultados,
            fragmentos_descartados=resultado.fragmentos_descartados,
            hallazgos=resultado.hallazgos,
            traza_id=resultado.traza_id,
        )
        return self._repo.agregar_mensaje(conv.id, mensaje)

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
        cliente = self._agente.cliente_trazas
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
