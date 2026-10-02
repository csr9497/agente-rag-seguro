"""Catálogo del rol: qué puede consultar el usuario (sus roles, los documentos que ve y los datos
internos). Orienta al usuario cuando no hay información y ayuda al supervisor a distinguir lo
que es de la empresa de lo que no.

Regla 1 (permisos en el dato): se construye con los roles autenticados del usuario y los
permisos del registro; nunca nombra documentos ni roles que el usuario no tiene.
"""

from collections.abc import Iterable, Mapping

from pydantic import BaseModel, Field

from app.security.acl import es_rol


class CatalogoRol(BaseModel):
    roles: list[str] = Field(default_factory=list, description="«Nombre: descripción» de cada rol")
    documentos: list[str] = Field(default_factory=list, description="Títulos visibles")
    # Título → identificador: el supervisor necesita los reales para buscar_en_documento.
    ids: dict[str, str] = Field(default_factory=dict)
    datos: list[str] = Field(default_factory=list, description="Datos internos consultables")

    @property
    def vacio(self) -> bool:
        return not (self.roles or self.documentos or self.datos)

    def como_texto(self, con_ids: bool = False) -> str:
        """`con_ids`: para el supervisor; al usuario (orientación) solo se le dan títulos."""
        bloques = []
        if self.roles:
            bloques.append("Roles del usuario:\n" + "\n".join(f"- {r}" for r in self.roles))
        if self.documentos:
            titulos = [
                f"{t} (id: {self.ids[t]})" if con_ids and t in self.ids else t
                for t in self.documentos
            ]
            bloques.append("Documentos que puede consultar:\n" + _lista(titulos))
        if self.datos:
            bloques.append("Datos internos que puede consultar:\n" + _lista(self.datos))
        return "\n\n".join(bloques) or "El usuario no tiene acceso a ningún documento."


def construir_catalogo(
    grupos: list[str],
    documentos: Iterable[tuple[str, str, list[str]]],
    roles: Mapping[str, tuple[str, str]],
    datos: Iterable[tuple[str, list[str]]],
) -> CatalogoRol:
    """`documentos`: (doc_id, título, roles) de los documentos activos; `roles`: id → (nombre,
    descripción); `datos`: (descripción, roles) de las consultas de data_query. Solo entra lo
    que comparte algún rol con `grupos`."""
    if not grupos:
        return CatalogoRol()
    ids = {titulo: doc_id for doc_id, titulo, de in documentos if set(de) & set(grupos)}
    visibles = sorted(ids)
    descritos = []
    for rol in (g for g in grupos if es_rol(g)):  # «dept:»/«user:» no son roles
        nombre, descripcion = roles.get(rol, (rol, ""))
        descritos.append(f"{nombre}: {descripcion}" if descripcion else nombre)
    consultables = [
        # Sin los detalles para el supervisor («(parámetro: anio)»).
        descripcion.split(" (")[0]
        for descripcion, de in datos
        if set(de) & set(grupos)
    ]
    return CatalogoRol(roles=descritos, documentos=visibles, ids=ids, datos=consultables)


def _lista(elementos: list[str]) -> str:
    return "\n".join(f"- {e}" for e in elementos)
