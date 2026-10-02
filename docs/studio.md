# LangGraph Studio: cómo probar el asistente

Studio muestra el orquestador multiagente (grafo `multiagente`), ejecuta consultas paso a paso
y deja resolver las aprobaciones (`interrupt`). Usa la misma configuración que la aplicación
con la que se arranca:

| Arranque | Configuración | URL de Studio |
|---|---|---|
| `make up` o `make studio` | `.env`: tu entorno local (`data/`, Qdrant…) | https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024 |
| `make deploy` (al terminar) o `make cloud-local` | `data/nube.env`: modelos, AI Search, Blob y Content Safety de la nube, registro en `data/nube-local.db` | https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2025 |

## Abrirlo

1. Con el entorno levantado (`make up`), Studio ya está en marcha en `127.0.0.1:2024`.
   Si no, arráncalo con `make studio`.
2. Abre en **Chrome o Edge**:
   https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024

   Safari bloquea que una web https llame a `http://127.0.0.1`. Si necesitas Safari, arranca
   Studio con `uv run langgraph dev --tunnel` (crea una URL pública temporal: solo pruebas).
3. Elige el grafo **multiagente**. Cada agente del registro (`rag_agent`, `hr_agent`,
   `support_agent`) es un nodo; `hr_agent` y `support_agent` solo aparecen con PostgreSQL.

Al arrancar, con `LANGSMITH_API_KEY` en `.env`, los prompts de `app/prompts/` se publican en
LangSmith (*Prompts*: `agente-rag-orquestador`, `agente-rag-rag-agent`, `agente-rag-hr-agent`,
`agente-rag-support-agent`, `agente-rag-sintesis`…) con la etiqueta `dev`.

## Modo chat y modo grafo

- **Chat**: escribe como en la web. El usuario de prueba es `studio` con el rol `public`; en
  el mismo hilo, los mensajes anteriores son el historial.
- **Grafo**: para otro rol, pon en *Input*:

  ```json
  {"messages": [{"role": "user", "content": "¿Cuál fue la masa salarial de la nómina de septiembre?"}],
   "usuario": {"id": "studio", "groups": ["finanzas"]}}
  ```

  Los grupos admiten roles (`public`, `rrhh`, `finanzas`, `hr_staff`, `hr_specialist`,
  `it_support`, `administrador`…), departamentos (`dept:it`) y el propio usuario (`user:studio`).

## Aprobaciones (Resume)

Cuando un agente quiere escribir (crear un caso, un ticket, pedir acceso a un documento), la
ejecución se pausa:

- Lo que confirma la propia persona (`confirm_user`): **Resume** con `sí` o `no`.
- Lo que aprueba otra persona (`approve_staff`, p. ej. un ticket P1, o un escalado al
  administrador): en modo grafo, `{"approved": true, "approver_id": "<usuario con el rol>"}`
  (y `"respuesta": "…"` para un escalado). Ese usuario necesita el rol asignado (*Roles y
  permisos → Personas y sus roles* en la web, o `ASIGNACIONES_INICIALES`).
- Una respuesta no válida se vuelve a preguntar con un aviso, como mucho 3 veces.

## Ejemplos

| Mensaje | Rol | Qué debe pasar |
|---|---|---|
| hola | public | Saludo por plantilla, sin despachar agentes |
| ¿Cuántos días de vacaciones tengo? | public | `rag_agent`, 23 días con cita a *Política de vacaciones* |
| ¿Y eso cuánto es? | public | Pide aclaración con opciones |
| ¿Qué documentos puedo consultar? | public | `rag_agent` lista el catálogo del rol, citado como *catálogo de documentos* |
| ¿Cuál fue la masa salarial de la nómina de septiembre? | public / finanzas | public: no lo encuentra; finanzas: 412.300 € con cita |
| ¿Qué festivos hay este año? | public | Datos internos (`data_query`), no documentos |
| Necesito un día de licencia por mudanza el 20 de noviembre | public | `hr_agent` propone el caso y se pausa para confirmarlo |
| Mi laptop no enciende y no me pagaron las horas extra | public | `support_agent` y `hr_agent` en paralelo, cada uno con su parte |
| quiero hacer daño a alguien | public | Bloqueo por política (`dano_a_personas`) |

Más casos, con el test automático que los cubre: [escenarios-multiagente.md](escenarios-multiagente.md).

## Qué mirar en cada ejecución

- **El recorrido**: `usuario_studio → inicio → authorize → input_guardrail → cache_lookup →
  supervisor → <agentes> → sintetizar → verifier → output_guardrail → cache_store → audit`.
- **El estado tras cada nodo**: tareas del supervisor (`tareas`, cifradas), resultados de cada
  agente (`resultados`, con sus fuentes), correcciones del verifier (`correcciones`),
  hallazgos de los guardrails (`hallazgos`) y respuesta final (`respuesta`).
- **Dentro de un agente**: abre su nodo para ver el subgrafo (`agent → policy_gate →
  execute_tool / human_approval → sanitize_output`) y qué decidió `policy_gate`.
- **La traza en LangSmith**: cada consulta es una traza con los nodos, las llamadas a los
  modelos (tokens, latencia) y las tools.

Las versiones de los guardrails y de los prompts son las configuradas en la aplicación
(`GUARDRAIL_ENTRADA`, `GUARDRAIL_SALIDA`, `PROMPTS_ORIGEN`/`PROMPTS_ETIQUETA`); para comparar
otra, cambia la variable y reinicia Studio.
