"""Conversaciones con un rol fijo. El historial guarda, por mensaje, los documentos
consultados, los citados y cuántos fragmentos se descartaron por permisos."""

from app.graph.agente import Agente
from app.models.schemas import Usuario
from app.persistencia.modelos import Conversacion, MensajeGuardado
from app.persistencia.repositorios import RepositorioConversaciones
from app.servicios.errores import NoEncontradoError, PermisoDenegadoError
from app.servicios.roles import ServicioRoles


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
        )
        return self._repo.agregar_mensaje(conv.id, mensaje)
