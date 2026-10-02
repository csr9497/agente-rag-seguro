"""Caché semántica con permisos (regla 2: la clave incluye el scope de permisos).

El `alcance` de una entrada es: roles del usuario + huella de los documentos visibles para
esos roles (doc_id, hash y estado) + modelo/versión. Así:
- una respuesta cacheada para un rol nunca se sirve a otro rol;
- cualquier alta, baja, cambio de roles o cuarentena de un documento cambia la huella y las
  entradas anteriores dejan de coincidir (invalidación automática);
- un cambio de modelo o de versión de la app también invalida.

Implementación local en memoria. En Azure: Azure Cache for Redis detrás de esta interfaz.
"""

import hashlib
import math
import threading
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel

from app.models.schemas import RespuestaConsulta
from app.persistencia.repositorios import RepositorioDocumentos
from app.security.acl import es_rol


class EntradaCache(BaseModel):
    alcance: str
    pregunta: str
    vector: list[float]
    respuesta: RespuestaConsulta
    documentos_consultados: list[str]


class CacheSemantica(Protocol):
    def buscar(self, vector: list[float], alcance: str) -> EntradaCache | None: ...

    def guardar(self, entrada: EntradaCache) -> None: ...


def _coseno(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b, strict=True))
    den = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return num / den if den else 0.0


class CacheMemoria:
    """LRU acotada por alcance. Solo compara entradas del mismo alcance."""

    def __init__(self, umbral: float = 0.95, max_entradas: int = 1000) -> None:
        self._umbral = umbral
        self._max = max_entradas
        self._entradas: OrderedDict[int, EntradaCache] = OrderedDict()
        self._siguiente = 0
        self._lock = threading.Lock()

    def buscar(self, vector: list[float], alcance: str) -> EntradaCache | None:
        with self._lock:
            mejor: tuple[float, int] | None = None
            for clave, e in self._entradas.items():
                if e.alcance != alcance:
                    continue
                sim = _coseno(vector, e.vector)
                if sim >= self._umbral and (mejor is None or sim > mejor[0]):
                    mejor = (sim, clave)
            if mejor is None:
                return None
            self._entradas.move_to_end(mejor[1])
            return self._entradas[mejor[1]]

    def guardar(self, entrada: EntradaCache) -> None:
        with self._lock:
            self._entradas[self._siguiente] = entrada
            self._siguiente += 1
            while len(self._entradas) > self._max:
                self._entradas.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entradas)


def alcance_de_permisos(
    registro: RepositorioDocumentos,
    roles: list[str],
    version: str,
    ahora: datetime | None = None,
) -> str:
    """Huella de lo que el usuario puede ver AHORA (+ versión de modelo/app).

    `roles` son los grupos efectivos (roles, «dept:», «user:»). Entran los roles reales (los
    datos internos dependen del rol) y los documentos visibles y vigentes: revocar o caducar
    un documento cambia el alcance, así que una respuesta construida con él no se reutiliza.
    «dept:»/«user:» solo cuentan a través de lo que dejan ver: dos personas con la misma
    visibilidad comparten caché."""
    momento = ahora or datetime.now(UTC)
    grupos = set(roles)
    docs = sorted(
        (d.doc_id, d.doc_hash, d.estado)
        for d in registro.listar()
        if grupos & set(d.roles)
        and not d.revocado
        and not (d.expira_en and datetime.fromisoformat(d.expira_en) <= momento)
    )
    reales = sorted(r for r in roles if es_rol(r))
    huella = hashlib.sha256(repr((reales, docs, version)).encode()).hexdigest()
    return f"{','.join(reales)}:{huella[:24]}"


class CacheRedis:
    """Caché compartida entre réplicas. Funciona en cualquier tier de Redis (no necesita
    búsqueda vectorial): una lista por alcance, acotada y con caducidad; la similitud se calcula
    en la app sobre las pocas entradas de ese alcance."""

    def __init__(
        self, cliente, umbral: float = 0.95, max_por_alcance: int = 100, ttl_s: int = 86400
    ) -> None:  # noqa: ANN001 - redis.Redis o compatible
        self._r = cliente
        self._umbral = umbral
        self._max = max_por_alcance
        self._ttl = ttl_s

    @staticmethod
    def _clave(alcance: str) -> str:
        return f"cache:{alcance}"

    def buscar(self, vector: list[float], alcance: str) -> EntradaCache | None:
        mejor: tuple[float, EntradaCache] | None = None
        for crudo in self._r.lrange(self._clave(alcance), 0, -1):
            entrada = EntradaCache.model_validate_json(crudo)
            if entrada.alcance != alcance:  # defensa en profundidad
                continue
            sim = _coseno(vector, entrada.vector)
            if sim >= self._umbral and (mejor is None or sim > mejor[0]):
                mejor = (sim, entrada)
        return mejor[1] if mejor else None

    def guardar(self, entrada: EntradaCache) -> None:
        clave = self._clave(entrada.alcance)
        with self._r.pipeline() as p:
            p.lpush(clave, entrada.model_dump_json())
            p.ltrim(clave, 0, self._max - 1)
            p.expire(clave, self._ttl)
            p.execute()
