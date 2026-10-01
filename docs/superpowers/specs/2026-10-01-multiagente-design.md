# Asistente multiagente seguro (RAG + RR.HH. + Soporte)

**Fecha:** 2026-10-01 · **Estado:** fases 1, 2 y 3 terminadas (2026-10-01) · **Spec de origen:** la entregada por el
usuario («SPEC — Asistente multiagente seguro»), que se resume aquí con las decisiones tomadas.

## Punto de partida (lo que había)

- Grafo único `authorize → input_guardrail → cache_lookup → supervisor ⇄ tools →
  access_guardrail → generate → output_guardrail → cache_store → audit` (app/graph/agente.py).
- `Usuario(id, groups)`: `groups` son los roles de la app (`public`, `rrhh`, `finanzas`,
  `administrador`). Sin scopes, departamentos ni token delegado (Easy Auth GitHub/Entra o stub).
- Sin checkpointer (decisión del 2026-09-28: guardaría PII y fragmentos en reposo).
- Aprobación humana sin `interrupt`: tabla `acciones` (`abrir_ticket`, `solicitar_vacaciones`).
- ACL de documentos: un campo `acl_groups` en Qdrant y Azure AI Search; `access_guardrail`
  re-chequea cada fragmento contra el registro.
- Auditoría: línea de log JSON; sin tabla `audit_log`. SQLite en local, PostgreSQL en la nube.

## Decisiones (2026-10-01)

| Tema | Decisión |
|---|---|
| Checkpointer | PostgreSQL con **serializador cifrado** (AES, clave en Key Vault / `.env`). El estado solo guarda la pregunta ya saneada |
| Base de datos local | **PostgreSQL siempre** (`make up` lo arranca); SQLite solo en tests unitarios sin RLS |
| Acciones existentes | **Se sustituyen** por `tickets` y `hr_cases` con `interrupt` (fases 3–4): se retiran la tabla `acciones`, su servicio, su API y `proponer_accion` |
| Roles y ACL | **Mapear sobre lo actual**: se mantienen `public`/`rrhh`/`finanzas`/`administrador` y se añaden `hr_staff`, `hr_specialist`, `it_support`, `auditor`. Propuesta para la fase 2: codificar la clasificación en `acl_groups` (roles; `dept:<d>`; `user:<id>`) |
| Token delegado | No hay sistema externo: los **scopes se derivan de los roles** en la app (`authorize`) y `execute_tool` actúa con la identidad autenticada |
| Vector store | El filtro de política va en la consulta tanto en Qdrant (local) como en AI Search (nube) |

## Fases

1. Registro `AgentSpec`/`ToolPolicy`, subgrafo genérico y `policy_gate` con tests unitarios.
2. `rag_agent` con filtro ACL en la consulta y re-chequeo (`revoked`, `expires_at`); retirar
   `access_guardrail`.
3. PostgreSQL en local, checkpointer cifrado, `audit_log`, `approvals`; `hr_agent` y
   `hr_cases` con RLS e `interrupt` de confirmación.
4. `support_agent`, `tickets` con RLS, deduplicación y aprobación de P1.
5. Supervisor con `Send`, `verifier`, `escalate_human` y caché con `scope_hash`.
6. Evals de enrutamiento, permisos y prompt injection.

Cada fase termina con tests en verde y un resumen; la siguiente empieza con confirmación.

## Fase 1 — diseño

Piezas nuevas y aisladas; el grafo actual no cambia.

| Pieza | Responsabilidad |
|---|---|
| `app/agents/registry.py` | `AgentSpec`, `ToolPolicy` (anexo B + `writes` y `args_model`), `register`, `resolve_mode`. `register` rechaza escrituras en `auto` y agentes duplicados |
| `app/agents/scopes.py` | Scopes por rol; `authorize` los añade al usuario (único nodo que lo escribe) |
| `app/agents/policy_gate.py` | `allow` / `deny` / `needs_approval`, determinista: tool del agente, scope, argumentos válidos, presupuesto, modo (`mode_if`) |
| `app/agents/subgraph.py` | `agent → policy_gate → {execute_tool \| human_approval \| agent con motivo} → sanitize_output → agent`; `interrupt` con `{type, agent, tool, args_preview, risk, expires_at}` y `Command(resume={approved, edited_args?, reason?, approver_id})`; en `approve_staff`, quien aprueba debe tener `approver_role` y no ser el solicitante |
| `sanitize_output` | Reutiliza `neutralizar` y el enmascarado de PII; marca la salida como `<dato_herramienta>` |
| `AuditSink` | Una fila por decisión de tool (hash de args, decisión, `approver_id`); en memoria y log (tabla en la fase 3) |
| Presupuestos | Iteraciones y tiempo en el estado del subgrafo |

## Fase 2 — rag_agent (hecha)

| Pieza | Qué hace |
|---|---|
| `app/security/acl.py` | ACL en `acl_groups`: rol (confidencial), `dept:<d>` (interno), `user:<id>` (restringido), `public`. Grupos efectivos del usuario = rol + `dept:` + `user:`. Solo formatos sin comillas, comas ni espacios (acaban en filtros OData) |
| Registro | `documentos.revocado` y `documentos.expira_en` (migración aditiva); `documento_departamentos`, `documento_usuarios`, `usuario_departamentos` (`DEPARTAMENTOS_INICIALES`) y `solicitudes_acceso` |
| Ingesta | `<documento>.acl.json` junto al original (roles, departamentos, usuarios, expira_en); departamentos inexistentes se rechazan |
| `VerificadorRegistro` | Además rechaza documentos revocados o caducados (también en el grafo actual) |
| Caché | El alcance se calcula con lo que el usuario ve AHORA: revocar o caducar un documento lo cambia (antes no: fallo latente corregido); `user:` no fragmenta la caché salvo que dé acceso a algo |
| `app/agents/rag.py` | `search_documents` (filtro en la consulta + re-chequeo en la tool), `get_document_metadata` (ajeno = inexistente), `request_document_access` (`confirm_user`; misma respuesta exista o no el documento). Registrado en `build_registro_agentes` |

**Decisión:** `access_guardrail` se mantiene en el grafo actual hasta la fase 5 (opción A); `rag_agent` no lo necesita.

## Fase 3 — hr_agent, RLS, auditoría, aprobaciones y checkpointer (hecha)

| Pieza | Qué hace |
|---|---|
| PostgreSQL local | `make up` lo arranca y la app lo usa (SQLite queda para tests unitarios). `.env`: `DATABASE_URL` (host) y `CHECKPOINT_CLAVE`, generadas si faltan. `make test-postgres` usa la base aparte `agente_test` |
| RLS (`app/persistencia/rls.py`) | Rol `agente_rls` sin login, superusuario ni BYPASSRLS; `sesion_rls` hace `SET LOCAL ROLE` y fija `app.user_id`/`app.roles` desde el token. Necesario porque la conexión es de un superusuario (Docker) o del dueño de las tablas (Azure), que se saltan RLS. Solo SELECT/INSERT: ningún agente cierra, reasigna ni borra |
| `hr_cases` / `hr_case_notes` | Políticas del anexo A (solicitante, `hr_staff` solo `normal`, `hr_specialist` todo); notas solo en casos propios abiertos. Sensibilidad por categoría en código |
| `audit_log` | Una fila por llamada a tool; trigger que impide UPDATE/DELETE/TRUNCATE (también al dueño), en PostgreSQL y SQLite |
| `approvals` | Fila por pausa; se resuelve una vez; `vencer()` cancela a las 24 h y notifica; una vencida no se ejecuta aunque se reanude el hilo |
| Checkpointer (`app/agents/checkpointer.py`) | `PostgresSaver` con AES-EAX y lista explícita de tipos deserializables. PostgresSaver guarda `str`/`int` del estado en claro: los textos del usuario van en `TextoPrivado` y la entrada se envuelve antes de llegar al grafo (test: nada legible en reposo; test estructural de campos) |
| `hr_agent` (`app/agents/hr.py`) | `search_hr_policies` (solo público/interno), `get_my_hr_cases` (sin resumen de confidenciales), `create_hr_case` y `add_hr_case_note` (`confirm_user`); devuelve solo el ID. Sin PostgreSQL no se registra (fallo cerrado) |

**Pendiente para la fase 5:** conectar el checkpointer y `approvals` al grafo principal (y la clave en Key Vault en Azure), una tarea periódica que llame a `vencer()` y la API para aprobar desde la web.
