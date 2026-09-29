"""Propuesta, decisión y ejecución de acciones. Ejecutores simulados (en producción se
conectarían con el sistema de tickets y el de RRHH)."""

import json
import logging
import uuid

from pydantic import ValidationError
from sqlalchemy import Engine, func, insert, select, update

from app.acciones.modelos import CATALOGO, PropuestaAccion, TipoAccion
from app.persistencia import tablas as t
from app.persistencia.repositorios import ahora
from app.servicios.errores import DatosInvalidosError, NoEncontradoError, PermisoDenegadoError

audit = logging.getLogger("audit")


class ServicioAcciones:
    def __init__(self, motor: Engine) -> None:
        self._motor = motor

    # ------------------------------------------------------------ proponer (lo usa la tool)
    def proponer(self, tipo: TipoAccion, datos: dict, rol_id: str, usuario: str) -> PropuestaAccion:
        modelo, roles, _ = CATALOGO[tipo]
        if roles is not None and rol_id not in roles:
            raise PermisoDenegadoError(f"El rol '{rol_id}' no puede proponer '{tipo}'")
        try:
            validados = modelo.model_validate(datos).model_dump(mode="json")
        except ValidationError as exc:
            raise DatosInvalidosError(
                f"Datos no válidos para {tipo}: {exc.error_count()} errores"
            ) from exc
        propuesta = PropuestaAccion(
            id=str(uuid.uuid4()), tipo=tipo, rol_id=rol_id, usuario=usuario, datos=validados,
            creada_en=ahora(),
        )  # fmt: skip
        with self._motor.begin() as c:
            c.execute(insert(t.acciones).values(**propuesta.model_dump()))
        self._auditar("accion_propuesta", propuesta)
        return propuesta

    # ------------------------------------------------------------ consultar
    def obtener(self, accion_id: str) -> PropuestaAccion | None:
        with self._motor.connect() as c:
            fila = (
                c.execute(select(t.acciones).where(t.acciones.c.id == accion_id)).mappings().first()
            )
        return PropuestaAccion(**fila) if fila else None

    def listar(self, rol_id: str, usuario: str) -> list[PropuestaAccion]:
        with self._motor.connect() as c:
            filas = c.execute(
                select(t.acciones)
                .where(t.acciones.c.rol_id == rol_id, t.acciones.c.usuario == usuario)
                .order_by(t.acciones.c.creada_en.desc())
            ).mappings()
            return [PropuestaAccion(**f) for f in filas]

    # ------------------------------------------------------------ decidir (humano)
    def decidir(self, accion_id: str, aprobar: bool, rol_id: str, usuario: str) -> PropuestaAccion:
        propuesta = self.obtener(accion_id)
        # Solo quien la propuso, con el mismo rol; si no, no se revela que existe.
        if propuesta is None or propuesta.rol_id != rol_id or propuesta.usuario != usuario:
            raise NoEncontradoError("Acción no encontrada")
        if propuesta.estado != "pendiente":
            raise DatosInvalidosError(f"La acción ya está {propuesta.estado}")
        if aprobar:
            try:
                estado, resultado = "ejecutada", self._ejecutar(propuesta)
            except Exception as exc:  # noqa: BLE001 - se registra como error de la acción
                estado, resultado = "error", f"Error al ejecutar: {exc}"
        else:
            estado, resultado = "rechazada", "Rechazada por el usuario"
        with self._motor.begin() as c:
            # Condición sobre el estado: evita dobles ejecuciones concurrentes.
            filas = c.execute(
                update(t.acciones)
                .where(t.acciones.c.id == accion_id, t.acciones.c.estado == "pendiente")
                .values(estado=estado, resultado=resultado, decidida_en=ahora())
            ).rowcount
        if not filas:
            raise DatosInvalidosError("La acción ya fue decidida")
        decidida = self.obtener(accion_id)
        self._auditar("accion_decidida", decidida)
        return decidida

    def _ejecutar(self, p: PropuestaAccion) -> str:
        with self._motor.connect() as c:
            previas = c.execute(
                select(func.count())
                .select_from(t.acciones)
                .where(t.acciones.c.tipo == p.tipo, t.acciones.c.estado == "ejecutada")
            ).scalar_one()
        prefijo = "SOP" if p.tipo == "abrir_ticket" else "VAC"
        return f"{prefijo}-{previas + 1:04d} registrado (simulado)"

    @staticmethod
    def _auditar(evento: str, p: PropuestaAccion) -> None:
        audit.info(json.dumps({"accion": evento, **p.model_dump(mode="json")}, ensure_ascii=False))
