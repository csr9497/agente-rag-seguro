# Orientación cuando no hay información

**Fecha:** 2026-10-01 · **Estado:** implementado

## Problema

Cuando el asistente no tenía información respondía siempre la frase fija «No encuentro esa
información en los documentos a los que tienes acceso.», en tres situaciones distintas:

1. La búsqueda no devuelve nada visible para el rol (p. ej. un empleado general pregunta por
   salarios).
2. Hay fragmentos, pero no contienen la respuesta (o el modelo no cita).
3. La consulta no es de la empresa (plantilla fija `fuera_de_ambito`).

El usuario no sabía qué podía preguntar ni a quién acudir.

## Decisiones

- **Alcance de la orientación: solo lo del rol del usuario.** Nunca se nombran documentos,
  áreas ni roles que no puede ver (regla 1: permisos en el dato). Si quizá existe pero no lo
  ve, se sugiere pedir acceso al administrador, sin dar detalles.
- **Sin conocimiento general.** Para temas ajenos no responde con su conocimiento: orienta
  (regla 6: solo respuestas citadas o la salida de escape).
- **Enfoque A**: respuesta redactada por el LLM con el catálogo del rol (frente a plantillas
  enriquecidas o dejar que el supervisor responda).

## Diseño

| Pieza | Responsabilidad |
|---|---|
| `app/rag/catalogo.py` | `CatalogoRol`: roles del usuario (nombre y descripción), títulos de sus documentos activos (con identificador, solo para el supervisor) y datos internos que su rol puede consultar. Se construye desde el registro y el catálogo de `data_query`, filtrando por los grupos autenticados |
| `app/prompts/orientacion.md` | Prompt versionado (LangSmith `agente-rag-orientacion`) y protegido por el guardrail de salida |
| `app/rag/orientacion.py` | `responder_sin_informacion`: el LLM recibe motivo, catálogo y pregunta, nunca fragmentos. Sin citas (se eliminan marcas `[n]`), `sin_contexto=True` (no se cachea). Si el modelo falla, texto fijo de respaldo |
| `generate` | Orienta en los casos 1 y 2, y en `conversacion` de tipo `ayuda` y `fuera_de_ambito` (respaldo: su plantilla). Saludo, agradecimiento y despedida siguen con plantilla. Usuario sin roles: frase fija sin LLM (deny by default) |
| `supervisor` | Recibe el catálogo con identificadores reales: distingue lo de la empresa de lo ajeno y no inventa identificadores. El catálogo no limita: lo de la empresa se busca aunque no esté en él |

## Pruebas

- Unitarias: catálogo filtrado por rol (documentos y datos internos), identificadores solo
  para el supervisor, orientación sin fragmentos ni citas, respaldo si el modelo falla,
  etiquetas neutralizadas; grafo con los tres casos; composición real (registro + documentos
  de ejemplo): un empleado general no recibe nada de RRHH ni de Finanzas.
- Gate de evaluaciones del CI (modelos simulados): aprobado.
- Matriz de integración con modelos reales contra la app local: 39/39.

## Incidencia encontrada al probar

Con solo títulos en el catálogo, el supervisor llamaba a `buscar_en_documento` con
identificadores inventados y Finanzas dejaba de ver su nómina (PERM-07). Se corrigió dando al
supervisor el identificador real de cada documento y la regla de buscar con `rag_retrieve` si
un documento «no existe».
