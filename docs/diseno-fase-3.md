# Diseño Fase 3 — Roles, interfaz unificada, Azure, LangSmith y evaluaciones

Estado: **propuesta para revisión** · 2026-09-28

Decisiones ya tomadas:

| Tema | Decisión |
|---|---|
| Persistencia de roles, documentos y conversaciones | SQLite en local, detrás de una interfaz de repositorio (PostgreSQL al desplegar la app en Azure) |
| Selección de rol sin autenticación | Libre en local con flag `SELECCION_LIBRE_DE_ROL=true`; en Azure desactivado y restringido a los roles del usuario en Entra ID |
| Contenido de las trazas en LangSmith | Completo en dev, enmascarado en prod |
| Orden | Este diseño → revisión → implementación local de roles + UI → despliegue de modelos en Azure |

---

## 1. Modelo de roles y permisos

### Entidades

```
Rol            id (slug), nombre, descripcion, puede_gestionar_documentos, activo, creado_en
Documento      doc_id, titulo, roles[], doc_hash, chunks, ubicacion_original, estado, subido_por, indexado_en
Conversacion   id, rol_id, creada_en
Mensaje        id, conversacion_id, pregunta (PII enmascarada), respuesta, sin_contexto,
               documentos_consultados[], citas[], hallazgos[], creado_en
```

- **El rol sustituye al "grupo".** Internamente la ACL de cada chunk sigue siendo
  `acl_groups`, ahora con ids de rol, así que el filtro de la búsqueda no cambia.
- **Visibilidad por rol.** Un documento lo ven solo los roles marcados al subirlo; el
  selector de la subida permite elegir varios. No hay herencia entre roles: si RRHH debe ver
  un documento general, se marcan ambos roles.
- **Gestión de documentos.** `puede_gestionar_documentos` sustituye al grupo `editores`. Un
  rol gestor solo puede asignar documentos a roles que existan y estén activos.
- **Roles iniciales.** Se migran los grupos actuales: `public` → "Empleado general" y
  `rrhh` → "Recursos Humanos", más un rol `gestor-documental` con gestión.
- **Ingesta por CLI.** Se mantiene la convención de carpeta = rol. La carpeta debe
  corresponder a un rol registrado; si no, el documento se rechaza.

### Permisos configurables desde la interfaz (decisión 2026-09-28)

Los roles **y sus permisos** se asignan desde la UI (panel "Roles y permisos"):

| Permiso | Qué habilita |
|---|---|
| (implícito) consultar | Chatear con los documentos marcados para ese rol |
| `gestionar_documentos` | Subir, reindexar y eliminar documentos |
| `publica_para: [roles]` | A qué roles puede asignar documentos al subirlos. Si incluye roles ajenos, el propio rol se añade siempre, para que nadie cree documentos que no puede ver ni mantener |
| `administrar_roles` | Crear, editar y desactivar roles y asignar permisos |

- **Semilla inicial**:
  - `administrador`: `administrar_roles`.
  - `rrhh`: gestiona y publica para `rrhh` y `public`.
  - `public`: solo consulta.
- **Protecciones**:
  - Nadie puede quitarse `administrar_roles` si es el último rol que lo tiene.
  - Desactivar un rol no borra sus documentos: dejan de ser consultables con ese rol.
- **Auditoría**: cada cambio de rol o permiso queda registrado.

### Validación de permisos: cuatro barreras

| # | Dónde | Qué valida | Si falla |
|---|---|---|---|
| 1 | Al indexar (gestor) | El documento tiene ≥1 rol y todos existen y están activos. El registro (SQLite) y el índice se escriben con los mismos roles | Rechazo: no se indexa |
| 2 | `authorize` (inicio del grafo) | La conversación existe, su rol existe y está activo, y en Azure el usuario tiene ese rol | Respuesta "sin acceso", sin llamar a modelos; auditado |
| 3 | Retriever (dato) | Filtro por `acl_groups ∋ rol` en Qdrant o AI Search | No se devuelve el chunk |
| 4 | **Nuevo nodo `access_guardrail`** (tras `tools`) | Cada chunk devuelto: (a) su `doc_id` está en el registro con estado activo, (b) el rol activo figura **tanto** en la ACL del chunk **como** en el registro, (c) el `doc_hash` del chunk coincide con el del registro | El chunk se descarta y se registra un hallazgo `acceso_no_autorizado` o `permisos_inconsistentes` |

La barrera 4 compara dos fuentes independientes: el índice vectorial y el registro. Así
detecta chunks huérfanos, ACL desincronizadas o un índice manipulado.

**Validación "antes de todo".** Se añade `verificar_integridad()`, que se ejecuta:
- al arrancar la app;
- después de cada subida;
- bajo demanda con `GET /admin/integridad`.

Compara el índice con el registro:
- documentos indexados sin registro;
- roles del índice que no coinciden con los del registro;
- chunks sin ACL;
- documentos cuyos roles ya no existen.

Todo lo inconsistente queda en **cuarentena**: estado `bloqueado` en el registro, y la
barrera 4 lo excluye aunque el índice lo devuelva. El informe muestra cada problema con su
causa.

### Topología del grafo

```
authorize → input_guardrail → supervisor ⇄ tools → access_guardrail ─┐
                                   ▲                                 │
                                   └─────────────────────────────────┘
                              supervisor → generate → output_guardrail → audit
authorize / input_guardrail ──(sin acceso / bloqueada)──────────────────▶ audit
```

`output_guardrail` añade una comprobación: toda cita de la respuesta debe apuntar a un chunk
que pasó `access_guardrail`. Es redundante por diseño.

---

## 2. Conversaciones con rol

```
UI: elegir rol ──POST /conversaciones {rol_id}──▶ conversacion_id
UI: preguntar ──POST /conversaciones/{id}/mensajes {pregunta}──▶ respuesta + documentos consultados
UI: historial ──GET /conversaciones/{id}──▶ mensajes con citas y documentos consultados
```

- **El rol queda fijo** para toda la conversación. Para cambiar de rol hay que abrir una
  nueva: una conversación nunca mezcla contextos de roles distintos.
- **Documentos consultados** de cada mensaje:
  - todos los `doc_id` que devolvieron las tools y pasaron `access_guardrail`;
  - de ellos, cuáles se **citaron** en la respuesta;
  - los descartados por el guardrail solo se muestran como un contador, sin identificarlos,
    para no revelar qué existe.
- **Memoria.** En esta fase cada pregunta se responde de forma independiente; el historial es
  para consultarlo, no se usa como memoria del agente. La memoria conversacional
  (checkpointer de LangGraph) se deja para cuando haya PostgreSQL.
- **Auditoría.** Se amplía con `conversacion_id`, `rol_id`, documentos consultados y
  descartados.

---

## 3. Interfaz de una sola página (sin pestañas)

**Prototipo navegable:** http://localhost:8080/prototipo/ (datos de ejemplo, sin backend).
Tiene una barra para cambiar el estado del chat y el rol. También se puede enlazar
directamente a un estado, p. ej. `?estado=sin-rol`, `?estado=cargando&rol=rrhh` o
`?estado=error`.

| Escritorio (RRHH, claro) | Escritorio (Empleado, oscuro) |
|---|---|
| ![](prototipo/escritorio-rrhh-claro.png) | ![](prototipo/escritorio-empleado-oscuro.png) |
| **Sin rol elegido** | **Buscando respuesta** |
| ![](prototipo/sin-rol.png) | ![](prototipo/cargando.png) |

Móvil (390 px, emulación real): ![](prototipo/movil-empleado.png)

**Sistema de diseño**
- **Estilo**: minimalista y funcional.
- **Color**: tokens semánticos para claro y oscuro. Azul `#2563EB`/`#60A5FA` como primario;
  verde para citado o correcto, ámbar para bloqueado o aviso y rojo para rechazo o error.
  El estado nunca se indica solo con color: siempre lleva icono y texto.
- **Contraste**: al menos 4,5:1 en todo el texto.
- **Tipografía**: fuente del sistema, sin Google Fonts, para cumplir el CSP `'self'`.
- **Espaciado**: escala de 4/8 px.
- **Iconos**: SVG en un sprite propio, sin emojis.
- **Interacción**: objetivos táctiles de al menos 44 px, foco visible, enlace para saltar al
  contenido, `aria-live` en el chat y respeto de `prefers-reduced-motion`.

**Reglas de visibilidad en la UI**
- La lista de documentos solo muestra los del rol activo; el filtro acota dentro de ese
  conjunto ("compartidos con…").
- Sin rol elegido, no se lista ningún documento.
- La subida y el borrado solo aparecen para roles con gestión.

**Decidido:** a qué roles puede publicar cada rol se configura en la UI (`publica_para`), y
el rol que publica se incluye siempre en el documento.

**Boceto de distribución** (referencia):

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Asistente de documentación          Rol: [Recursos Humanos ▾] [Nueva conv.] │
├──────────────────────────────────────────┬───────────────────────────────────┤
│ CONVERSACIÓN (rol: Recursos Humanos)     │ DOCUMENTOS                        │
│                                          │ Filtrar por rol: [Todos ▾]        │
│ Tú: ¿Rango de la banda B3?               │ ┌───────────────────────────────┐ │
│ Asistente: 42.000–58.000 € [1]           │ │ bandas-salariales.md          │ │
│   📄 Consultados: bandas-salariales.md ✓  │ │ RRHH · 3 frag. · hoy     [🗑] │ │
│      politica-vacaciones.md               │ └───────────────────────────────┘ │
│   ⛔ 1 fragmento descartado por permisos  │ Subir: [archivo] Roles: [☑RRHH   │
│                                          │        ☐Empleado] [Subir]          │
│ [ Escribe tu pregunta…            ] [→]  │ ROLES                              │
│                                          │ + Nuevo rol: [id] [nombre] [☐gest]│
└──────────────────────────────────────────┴───────────────────────────────────┘
```

- **Sin rol elegido**, el chat muestra la lista de roles y la conversación empieza al
  pulsar uno.
- **Documentos**:
  - el filtro por rol muestra qué verá cada rol;
  - la subida lleva selección múltiple de roles;
  - la lista y el borrado solo están disponibles para roles con gestión.
- **Roles**: formulario de alta en la misma página, disponible solo con
  `SELECCION_LIBRE_DE_ROL` o para un rol administrador.
- **Diseño adaptable**: dos columnas en escritorio y apiladas en móvil.
- **Seguridad**: todo el contenido del servidor se pinta con `textContent`, sin
  `innerHTML`, y el CSP sigue siendo `default-src 'self'`.

---

## 4. Persistencia

- **Interfaces** `RepositorioRoles`, `RepositorioDocumentos` y `RepositorioConversaciones`,
  con dos implementaciones.
- **Implementación local: SQLite** con **SQLAlchemy 2 (Core)**. Es una dependencia nueva,
  justificada porque el mismo SQL y las mismas migraciones sirven para SQLite hoy y
  PostgreSQL mañana, sin dos implementaciones.
- **Archivo**: `data/app.db`, en un volumen de docker-compose e ignorado por git.
- **Originales**: se guardan con la interfaz `AlmacenDocumentos`.
  - Local: carpeta `data/documentos/`.
  - Azure: Blob Storage, con los roles en los metadatos del blob (`x-ms-meta-roles`).
  - Es la fuente de verdad para reindexar sin depender de la convención de carpetas.
- **Límite de SQLite**: un solo proceso escritor. Sirve para local y una réplica, pero no
  para Container Apps con varias réplicas: al desplegar la app en Azure se pasa a PostgreSQL
  Flexible Server.

---

## 5. Despliegue en Azure

### Alcance por etapas (flag `alcance`)

| Etapa | `alcance` | Recursos | Para qué |
|---|---|---|---|
| **A (siguiente)** | `modelos` | RG, Azure OpenAI (`gpt-4o` 2024-11-20, `text-embedding-ada-002` v2), Storage (contenedor `documentos`), Key Vault, Log Analytics y el vector store según el flag | App en local contra modelos, almacenamiento y búsqueda reales; validar con la matriz |
| B | `completo` | + VNet, ACR, Container Apps (backend, web, job de ingesta), PostgreSQL Flexible Server, private endpoints opcionales | App desplegada |

Se implementa en el stack `infra/platform` existente, con `count` según las variables. El
stack `apps` solo se aplica en la etapa B.

### Flag del vector store

```hcl
variable "vector_store"  { default = "azure_search" }  # "azure_search" | "qdrant"
variable "search_sku"    { default = "free" }          # "free" | "basic"
variable "qdrant_modo"   { default = "cloud" }         # "cloud" | "container_efimero"
```

| Opción | Dónde vive el índice | Persistencia | Coste aprox. | Uso |
|---|---|---|---|---|
| `azure_search` + `free` | AI Search Free (50 MB, 3 índices, compartido, se puede borrar por inactividad) | Sí | 0 € | Etapa A con crédito de estudiante |
| `azure_search` + `basic` | AI Search Basic (15 GB, SLA con 3 réplicas) | Sí | ~75 USD/mes | Producción pequeña |
| `qdrant` + `cloud` | Qdrant Cloud en Azure (gestionado; URL y API key en Key Vault) | Sí | Tier gratuito limitado / pago | Mantener Qdrant en Azure |
| `qdrant` + `container_efimero` | Qdrant en Container Apps **sin volumen**; al arrancar, un job reindexa desde Blob | No (se reconstruye) | Solo cómputo | Demos |

**Qdrant sobre Azure Files no es una opción:** Qdrant no admite sistemas de archivos de red
(NFS/SMB) ni almacenamiento de objetos, y desde la v1.15 hace una comprobación al arrancar.
Por eso el modo en contenedor es efímero, y Blob es la fuente de verdad desde la que se
reconstruye.

La app ya elige la implementación con `VECTOR_STORE=qdrant|azure_search`. Terraform expone
el endpoint correspondiente y `make env-from-azure` lo escribe en `.env`.

### Flujo de subida en Azure

```
POST /documentos (archivo, roles[])
  → validación (ingesta segura) + roles existentes y activos
  → Blob: documentos/<doc_id> con metadata roles=<r1,r2>, hash
  → embeddings (ada-002) → índice (AI Search | Qdrant) con acl_groups = roles
  → registro (SQLite/Postgres): documento, roles, hash, estado=activo
  → verificar_integridad(doc_id)
```

- **Orden de escritura:** Blob → índice → registro.
  - Si falla el índice, el blob queda marcado `estado=pendiente` y un reintento lo recoge.
  - Si falla el registro, la barrera 4 descarta los chunks, porque no están registrados.
- **Documentos grandes (etapa B):** la subida solo guarda en Blob y un evento de Event Grid
  lanza el job de ingesta.

### Identidad y secretos

- En Azure, **Managed Identity** con RBAC mínimo.
- En local, `az login` más la clave de OpenAI desde Key Vault, que es lo que ya hace
  `make env-from-azure`.
- La API key de Qdrant Cloud y la de LangSmith van a Key Vault; nunca al repositorio.
- **Por verificar en el primer `plan`/`apply`:** si AI Search Free admite autenticación solo
  por RBAC. Si no, en Free se usaría una API key guardada en Key Vault.

### Validación de la etapa A

1. `terraform plan` con `alcance=modelos` → revisión → `apply`.
2. `make env-from-azure` → `.env` con endpoint, deployments y vector store.
3. Subir los documentos de ejemplo desde la UI, eligiendo sus roles.
4. Ejecutar la matriz de integración completa (hoy fallan 8 escenarios por falta de LLM) y
   las evaluaciones de la sección 7.

---

## 6. Trazas en LangSmith

### Configuración

| Variable | Dev | Prod |
|---|---|---|
| `LANGSMITH_TRACING` | `true` | `true` |
| `LANGSMITH_PROJECT` | `agente-rag-dev` | `agente-rag-prod` |
| `LANGSMITH_API_KEY` | `.env` | Key Vault |
| `TRAZAS_MODO` (nuestra) | `completo` | `enmascarado` |

### Qué se traza

- **Ejecución raíz del grafo**, llamada `consulta`, con:
  - metadata: `rol_id`, `conversacion_id`, `usuario` (hash), `vector_store`, versión (git
    SHA) y entorno;
  - tags: `rol:<id>`, `bloqueada`, `sin_contexto`.
- **Nodos y tools**: LangGraph los traza automáticamente.
- **Llamadas a Azure OpenAI** (supervisor, generación, embeddings), mediante
  `langsmith.wrappers.wrap_openai`. Así se ven los tokens, el coste y la latencia de cada
  llamada.
- **Feedback de la UI** (👍/👎 por mensaje) mediante `client.create_feedback(run_id, …)`.

### Enmascarado (prod)

- `Client(anonymizer=create_anonymizer(...))`, reutilizando los detectores de
  `app/security/deteccion.py` (email, teléfono, IBAN, tarjeta, DNI).
- En tools y generación, `process_outputs` sustituye el contenido de los fragmentos por
  `doc_id#n + hash`. Se ve qué se recuperó, pero no el texto.
- Las respuestas se envían ya enmascaradas por `output_guardrail`.

**Riesgo del modo completo en dev:** el contenido de documentos confidenciales se envía a
LangSmith. Mitigaciones:
- usarlo solo con documentos de ejemplo;
- proyecto de dev con acceso restringido;
- en el arranque, una comprobación que rechaza `TRAZAS_MODO=completo` si `ENTORNO=prod`.

### Automatización

| Automatización | Cómo |
|---|---|
| Dataset de regresión | `evals/sincronizar_dataset.py` convierte `escenarios.yaml` en el dataset `matriz-escenarios` |
| Experimento por PR | En CI: `evaluate()` del agente contra el dataset con los evaluadores de la sección 7. Se publica el enlace al experimento y se bloquea el PR si baja algún umbral |
| Evaluación online | Regla en LangSmith: muestreo del 10% de trazas de prod → juez de fidelidad |
| Cola de anotación | Regla: trazas con hallazgos `bloquear`, `permisos_inconsistentes` o 👎 → cola de revisión humana. Lo revisado se añade al dataset |
| Alertas | Tasa de errores, latencia p95 y bloqueos por rol por encima de un umbral |

---

## 7. Evaluaciones con Pydantic

Se amplía el evaluador actual (`tests/integration/evaluador.py`) a un paquete `evals/`
organizado en capas. Cada capa produce resultados tipados.

```python
class Escenario(BaseModel):  # matriz ampliada
    id: str
    rol: str  # sustituye a `grupos`
    pregunta: str
    docs_relevantes: list[str] = []  # verdad de referencia para métricas de recuperación
    respuesta_referencia: str | None = None
    esperado: Expectativa  # la actual


class JuicioRespuesta(BaseModel):  # salida estructurada del juez (LLM)
    fidelidad: int = Field(ge=1, le=5)  # cada afirmación sale del contexto
    relevancia: int = Field(ge=1, le=5)
    completitud: int = Field(ge=1, le=5)
    afirmaciones_sin_soporte: list[str]
    razonamiento: str


class Metrica(BaseModel):
    nombre: str
    valor: float
    umbral: float
    aprobado: bool


class ResultadoEvaluacion(BaseModel):
    escenario_id: str
    capa: Literal["contrato", "deterministas", "recuperacion", "juez", "seguridad"]
    metricas: list[Metrica]
    detalles: list[str] = []
```

| Capa | Qué mide | Cómo | Umbral inicial |
|---|---|---|---|
| Contrato | Esquema e invariantes de `RespuestaConsulta` | Pydantic (ya existe) | 100% |
| Deterministas | Expectativas del escenario (citas, fuentes, textos) | Ya existe | 100% en seguridad; ≥ 90% en el resto |
| Recuperación | recall@k, precisión y MRR de los documentos consultados frente a `docs_relevantes` | Documentos consultados del mensaje | recall@4 ≥ 0,8 |
| Juez | Fidelidad, relevancia y completitud | gpt-4o con `response_format=JuicioRespuesta` y una rúbrica fija; se calibra con ~20 respuestas etiquetadas a mano | media de fidelidad ≥ 4,0; ninguna ≤ 2 |
| Seguridad | Fuga entre roles | **Canarios**: cada documento confidencial lleva un marcador único (p. ej. `CANARIO-RRHH-7F3A`). Si una respuesta a otro rol contiene el marcador, falla | 0 fugas (bloqueante) |
| Seguridad | Inyección y jailbreak | Escenarios adversariales (directos y en documentos subidos) | 100% bloqueados o sin efecto |

- **Dónde se ejecutan:**
  - en local, con pytest (`make evals`) sobre la misma matriz;
  - en CI, con LangSmith `evaluate()` reutilizando las mismas funciones como evaluadores.
    Las métricas se guardan como feedback del experimento.
- **Informe:** el actual por capacidad, más métricas por capa y por rol, con la comparación
  frente al experimento anterior.
- **Coste:** el juez usa el mismo deployment de gpt-4o, a razón de 1 llamada por escenario
  (~25 por ejecución).

---

## 8. Plan de implementación

| # | Paso | Entregable | Verificación |
|---|---|---|---|
| 1 | Repositorios SQLite (roles, documentos, conversaciones) + migración de grupos a roles | `app/persistencia/` | Tests de repositorio |
| 2 | API de roles y conversaciones; subida con `roles[]` | `/roles`, `/conversaciones`, `/documentos` | Tests de API y permisos |
| 3 | `access_guardrail` + `verificar_integridad` + cuarentena | Nodo nuevo, `/admin/integridad` | Tests: índice manipulado, ACL desincronizada, rol desactivado |
| 4 | Documentos consultados en la respuesta y el historial | Esquema `Mensaje` | Tests del grafo y de la API |
| 5 | UI de una sola página | `web/html/` | Capturas (escritorio y móvil) + prueba manual |
| 6 | Matriz con roles, `docs_relevantes` y canarios | `escenarios.yaml` | Ejecución HTTP con fakes |
| 7 | Terraform con `alcance` y `vector_store` | `infra/platform` | `validate` + `plan` (sin `apply` hasta tu indicación) |
| 8 | LangSmith: `wrap_openai`, metadata, enmascarado por modo | `app/observabilidad.py` | Test del enmascarado; traza real cuando haya API key |
| 9 | Paquete `evals/` (capas, juez, canarios) + sincronización del dataset | `evals/` | Tests del evaluador; ejecución real tras la etapa A |

Los pasos 1–6 son solo locales. El 7 prepara Azure, pero no crea nada hasta que lo
indiques. Los pasos 8 y 9 dejan todo listo, pero sus ejecuciones reales necesitan API keys o
modelos desplegados.

## 9. Riesgos y puntos abiertos

- **Selección libre de rol:** es solo para local. Sin Entra ID, cualquiera con acceso a la
  web elige cualquier rol, y por eso la web sigue escuchando solo en `127.0.0.1`.
- **AI Search Free:** puede eliminarse por inactividad. Además, falta verificar si admite
  RBAC; si no, se usaría una API key en Key Vault.
- **Qdrant en Azure:** el modo efímero pierde el índice en cada reinicio, lo que es
  aceptable solo porque se reconstruye desde Blob.
- **Trazas completas en dev:** envían contenido a un servicio externo. Hay que usar un
  proyecto restringido y datos de ejemplo.
- **Juez LLM:** sesgo y coste. Se calibra con etiquetas humanas y se usa como señal, no como
  única puerta; las métricas bloqueantes son las deterministas y de seguridad.
- **Cambio de convención de ACL:** los documentos ya indexados con grupos (`public`,
  `rrhh`) se migran al registrar esos grupos como roles iniciales, con los mismos ids y sin
  reindexar.
