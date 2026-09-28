"""Integridad entre el índice vectorial y el registro de documentos ("validar antes de todo").

- Documento en el índice sin registro → huérfano (access_guardrail ya lo descarta).
- Documento registrado que falta en el índice → estado `pendiente`.
- Roles o hash distintos entre índice y registro → estado `bloqueado` (cuarentena).
- Documento en cuarentena que vuelve a ser coherente → `activo`.
"""

import logging
from typing import Literal

from pydantic import BaseModel, Field

from app.persistencia.repositorios import RepositorioDocumentos, RepositorioRoles
from app.retrieval.base import Retriever

logger = logging.getLogger("integridad")


class ProblemaIntegridad(BaseModel):
    doc_id: str
    tipo: Literal["huerfano_en_indice", "falta_en_indice", "acl_desincronizada", "hash_distinto"]
    detalle: str
    accion: Literal["ninguna", "pendiente", "bloqueado"]


class InformeIntegridad(BaseModel):
    revisados: int
    problemas: list[ProblemaIntegridad] = Field(default_factory=list)
    reactivados: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problemas


def verificar_integridad(
    retriever: Retriever, registro: RepositorioDocumentos, roles: RepositorioRoles
) -> InformeIntegridad:
    todos_los_roles = [r.id for r in roles.listar()]
    indexados = {d.doc_id: d for d in retriever.list_documents(todos_los_roles)}
    registrados = {d.doc_id: d for d in registro.listar()}
    problemas: list[ProblemaIntegridad] = []
    reactivados: list[str] = []

    for doc_id in sorted(set(indexados) - set(registrados)):
        problemas.append(
            ProblemaIntegridad(
                doc_id=doc_id,
                tipo="huerfano_en_indice",
                detalle="en el índice sin registro de permisos; se ignora en las consultas",
                accion="ninguna",
            )
        )

    for doc_id, doc in sorted(registrados.items()):
        en_indice = indexados.get(doc_id)
        problema: ProblemaIntegridad | None = None
        if en_indice is None:
            problema = ProblemaIntegridad(
                doc_id=doc_id,
                tipo="falta_en_indice",
                detalle="registrado pero no indexado",
                accion="pendiente",
            )
        elif sorted(en_indice.acl_groups) != doc.roles:
            problema = ProblemaIntegridad(
                doc_id=doc_id,
                tipo="acl_desincronizada",
                detalle=f"índice {sorted(en_indice.acl_groups)} ≠ registro {doc.roles}",
                accion="bloqueado",
            )
        elif en_indice.doc_hash and en_indice.doc_hash != doc.doc_hash:
            problema = ProblemaIntegridad(
                doc_id=doc_id,
                tipo="hash_distinto",
                detalle="el contenido indexado no es el registrado",
                accion="bloqueado",
            )

        if problema:
            problemas.append(problema)
            registro.marcar_estado(doc_id, problema.accion, f"{problema.tipo}: {problema.detalle}")
        elif doc.estado != "activo":
            registro.marcar_estado(doc_id, "activo", None)
            reactivados.append(doc_id)

    informe = InformeIntegridad(
        revisados=len(set(indexados) | set(registrados)),
        problemas=problemas,
        reactivados=reactivados,
    )
    for p in problemas:
        logger.warning("%s %s: %s → %s", p.tipo, p.doc_id, p.detalle, p.accion)
    return informe
