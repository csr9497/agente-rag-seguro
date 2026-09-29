// Interfaz de una sola página. Todo el contenido del servidor se pinta con textContent.
// El rol activo se envía en la cabecera X-Rol; el servidor valida que se puede usar.

const $ = (id) => document.getElementById(id);
const MOTIVO_BLOQUEO = {
  inyeccion: "Inyección de prompt detectada",
  texto_oculto: "Texto oculto en la pregunta",
  fuga_prompt: "La respuesta revelaba instrucciones internas",
};
// Políticas de uso: el servidor ya envía el mensaje adecuado en m.respuesta.
const POLITICA = {
  autolesion: ["info", "i-info", "Estamos para ayudarte"],
  dano_a_personas: ["danger", "i-ban", "No puedo ayudarte con eso"],
  acoso: ["warn", "i-ban", "Mensaje contrario al código de conducta"],
  dato_sensible: ["warn", "i-lock", "Dato sensible ocultado"],
};

const estado = {
  roles: [],           // roles disponibles para el usuario
  todos: [],           // todos los roles (solo administrador)
  rol: null,           // rol activo (objeto)
  conversacion: null,  // conversación activa
  docs: [],            // documentos visibles para el rol activo
  filtro: "*",
  subidas: [],
  editando: null,      // id del rol en edición
  pendiente: null,     // pregunta en curso o fallida { texto, error }
  historial: [],       // mis conversaciones con el rol activo (resúmenes)
  aviso: null,         // aviso en el chat { tipo, titulo, texto } (sustituye a alert())
  errorCarga: null,    // no se pudieron cargar los roles
};

// ------------------------------------------------------------------ utilidades
function el(tag, attrs = {}, ...hijos) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("aria-") || k === "role" || k === "for" || k === "title") n.setAttribute(k, v);
    else n[k] = v;
  }
  for (const h of hijos.flat()) {
    if (h !== null && h !== undefined) n.append(h instanceof Node ? h : document.createTextNode(String(h)));
  }
  return n;
}
function icono(id, clase = "icon") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", clase);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#${id}`);
  svg.append(use);
  return svg;
}
const nombreRol = (id) =>
  (estado.todos.find((r) => r.id === id) || estado.roles.find((r) => r.id === id) || { nombre: id }).nombre;
const tituloDoc = (id) => (id.startsWith("datos:") ? `Datos internos: ${id.slice(6)}`
  : id === "catalogo" ? "Listado de documentos de tu rol"
  : (estado.docs.find((d) => d.doc_id === id) || { titulo: id.split("/").pop() }).titulo);
// El listado de documentos y los datos internos se citan, pero no son documentos que abrir.
const esDocumento = (id) => id !== "catalogo" && !id.startsWith("datos:");
const puede = (permiso) => !!estado.rol && estado.rol.permisos.includes(permiso);
const hora = (iso) => (iso ? new Date(iso).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" }) : "");
// Hoy: solo la hora; otro día: fecha corta.
const fecha = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toDateString() === new Date().toDateString()
    ? `Hoy ${hora(iso)}` : d.toLocaleDateString("es", { day: "numeric", month: "short" });
};
function guardar(k, v) {
  try { if (v === null) sessionStorage.removeItem(k); else sessionStorage.setItem(k, v); } catch { /* sin almacenamiento */ }
}
function leer(k) {
  try { return sessionStorage.getItem(k); } catch { return null; }
}

async function api(ruta, { metodo = "GET", json, form, conRol = true } = {}) {
  const headers = {};
  if (conRol && estado.rol) headers["X-Rol"] = estado.rol.id;
  let body;
  if (json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(json); }
  if (form) body = form;
  const resp = await fetch(`api/${ruta}`, { method: metodo, headers, body });
  let cuerpo = null;
  try { cuerpo = await resp.json(); } catch { /* sin JSON */ }
  return { ok: resp.ok, status: resp.status, cuerpo };
}
function mensajeError(r) {
  const d = r.cuerpo && r.cuerpo.detail;
  if (typeof d === "string") return d;
  if (d && d.motivos) return d.motivos.join("; ");
  if (Array.isArray(d)) return d.map((e) => e.msg).join("; ");
  return `Error ${r.status}`;
}

// ------------------------------------------------------------------ carga y rol
async function iniciar() {
  let r;
  try { r = await api("roles", { conRol: false }); } catch (err) { r = { ok: false, status: 0, cuerpo: null }; }
  estado.errorCarga = r.ok ? null : (r.status ? mensajeError(r) : "No hay conexión con el servidor");
  estado.roles = r.ok ? r.cuerpo : [];
  const guardada = leer("conversacion");
  if (guardada) {
    const rc = await api(`conversaciones/${encodeURIComponent(guardada)}`, { conRol: false });
    if (rc.ok) {
      estado.conversacion = rc.cuerpo;
      estado.rol = estado.roles.find((x) => x.id === rc.cuerpo.rol_id) || null;
    } else guardar("conversacion", null);
  }
  if (estado.rol) await cargarDatosDelRol();
  pintarTodo();
}

async function cargarDatosDelRol() {
  if (!estado.rol) return;
  const [docs, todos] = await Promise.all([
    api("documentos"),
    puede("administrar_roles") ? api("roles/todos") : Promise.resolve({ ok: false }),
    cargarHistorial(),
  ]);
  estado.docs = docs.ok ? docs.cuerpo : [];
  estado.todos = todos.ok ? todos.cuerpo : [];
}

async function cargarHistorial() {
  if (!estado.rol) { estado.historial = []; return; }
  const r = await api(`conversaciones?rol_id=${encodeURIComponent(estado.rol.id)}`, { conRol: false });
  estado.historial = r.ok ? r.cuerpo : [];
}

async function abrirConversacion(id) {
  if (estado.pendiente && !estado.pendiente.error) return; // no interrumpir una consulta en curso
  const r = await api(`conversaciones/${encodeURIComponent(id)}`, { conRol: false });
  if (!r.ok) { estado.aviso = { tipo: "danger", titulo: "No se pudo abrir la conversación", texto: mensajeError(r) }; pintarChat(); return; }
  Object.assign(estado, { conversacion: r.cuerpo, pendiente: null, aviso: null });
  guardar("conversacion", r.cuerpo.id);
  pintarChat(); pintarHistorial();
  $("pregunta").focus();
}

async function elegirRol(rol) {
  const r = await api("conversaciones", { metodo: "POST", json: { rol_id: rol.id }, conRol: false });
  if (!r.ok) { estado.aviso = { tipo: "danger", titulo: `No se pudo usar el rol ${rol.nombre}`, texto: mensajeError(r) }; pintarChat(); return; }
  Object.assign(estado, { rol, conversacion: r.cuerpo, filtro: "*", subidas: [], pendiente: null, editando: null, aviso: null });
  $("pregunta").value = "";
  guardar("conversacion", r.cuerpo.id);
  await cargarDatosDelRol();
  pintarTodo();
  $("pregunta").focus();
}

function salirDelRol() {
  Object.assign(estado, { rol: null, conversacion: null, docs: [], todos: [], historial: [], pendiente: null, editando: null });
  $("pregunta").value = "";
  guardar("conversacion", null);
  pintarTodo();
}

// ------------------------------------------------------------------ cabecera
function pintarCabecera() {
  const cont = $("header-actions");
  if (!estado.rol) { cont.replaceChildren(el("span", { class: "hint" }, "Elige un rol para empezar")); return; }
  const cambiar = el("button", { class: "btn btn-ghost", type: "button" }, "Cambiar rol");
  cambiar.addEventListener("click", salirDelRol);
  const nueva = el("button", { class: "btn btn-secondary", type: "button" }, icono("i-new"), "Nueva conversación");
  nueva.addEventListener("click", () => elegirRol(estado.rol));
  cont.replaceChildren(
    el("span", { class: "role-chip" }, icono("i-users"), el("span", {}, "Rol: ", el("strong", {}, estado.rol.nombre))),
    cambiar, nueva);
}

// ------------------------------------------------------------------ chat
function pintarChat() {
  const body = $("chat-body");
  $("composer").hidden = !estado.rol;
  const aviso = estado.aviso ? notice(estado.aviso.tipo, "i-alert", estado.aviso.titulo, estado.aviso.texto) : null;
  if (estado.errorCarga) {
    const reintentar = el("button", { class: "btn btn-secondary", type: "button" }, icono("i-refresh"), "Reintentar");
    reintentar.addEventListener("click", iniciar);
    body.replaceChildren(notice("danger", "i-alert", "No se pudieron cargar tus roles", `${estado.errorCarga}.`, reintentar));
    return;
  }
  if (!estado.rol) {
    $("chat-sub").textContent = "Cada conversación usa los permisos de un único rol.";
    const tarjetas = estado.roles.map((r) => {
      const b = el("button", { class: "role-card", type: "button" },
        el("span", { class: "role-card-title" }, icono("i-users"), r.nombre),
        el("p", {}, r.descripcion || "Sin descripción."),
        el("span", { class: "meta" },
          r.permisos.includes("gestionar_documentos") ? el("span", { class: "badge accent" }, "Gestiona documentos") : null,
          r.permisos.includes("administrar_roles") ? el("span", { class: "badge accent" }, "Administra roles") : null));
      b.addEventListener("click", () => elegirRol(r));
      return b;
    });
    body.replaceChildren(...[aviso].filter(Boolean), el("div", { class: "role-picker" },
      el("p", {}, "¿Con qué rol quieres consultar? Solo verás respuestas basadas en los documentos de ese rol."),
      tarjetas.length ? el("div", { class: "role-cards" }, tarjetas) : el("p", { class: "hint" }, "No tienes roles disponibles.")));
    return;
  }
  $("chat-sub").textContent = `Con permisos de ${estado.rol.nombre} · ${estado.docs.length} documentos disponibles`;
  $("composer-hint").textContent = `Las respuestas solo usan documentos visibles para ${estado.rol.nombre}.`;

  const mensajes = (estado.conversacion ? estado.conversacion.mensajes : []).flatMap((m, i) => [
    el("div", { class: "msg-user" }, el("div", { class: "msg-meta" }, `Tú · ${hora(m.creado_en)}`), m.pregunta),
    ...(m.hallazgos.some((h) => h.accion === "bloquear") ? [] : [avisoOcultados(m)].filter(Boolean)),
    pintarRespuesta(m, i),
  ]);
  if (estado.pendiente) {
    mensajes.push(el("div", { class: "msg-user" }, el("div", { class: "msg-meta" }, "Tú · ahora"), estado.pendiente.texto));
    if (estado.pendiente.error) {
      const reintentar = el("button", { class: "btn btn-secondary", type: "button" }, icono("i-refresh"), "Reintentar");
      reintentar.addEventListener("click", () => preguntar(estado.pendiente.texto));
      mensajes.push(notice("danger", "i-alert", "No se pudo completar la consulta",
        `${estado.pendiente.error} Tu pregunta no se ha perdido.`, reintentar));
    } else {
      mensajes.push(el("div", { class: "msg-bot typing", "aria-busy": "true", role: "status" },
        el("span", { class: "sr-only" }, "El asistente está preparando la respuesta"),
        el("span", { class: "dots", "aria-hidden": "true" }, el("span"), el("span"), el("span"))));
    }
  }
  if (!mensajes.length) {
    const sinDocs = !estado.docs.length
      ? notice("info", "i-info", `${estado.rol.nombre} no tiene documentos visibles`,
          puede("gestionar_documentos")
            ? "Sube un documento en «Subir documento» para poder consultarlo aquí."
            : "No hay nada que consultar con este rol todavía. Pide a quien gestiona documentos que los publique para él, o cambia de rol.")
      : null;
    body.replaceChildren(...[aviso, sinDocs].filter(Boolean), el("div", { class: "empty" },
      el("h3", {}, "Nueva conversación"),
      el("p", { class: "hint" }, "Pregunta en lenguaje natural. Cada respuesta indica qué documentos se consultaron y cuáles se citaron.")));
    return;
  }
  body.replaceChildren(...[aviso].filter(Boolean), ...mensajes);
  body.scrollTop = body.scrollHeight;
}

function notice(tipo, ic, titulo, texto, accion) {
  return el("div", { class: `notice ${tipo}`, role: tipo === "danger" ? "alert" : "status" },
    icono(ic), el("strong", {}, titulo), el("p", {}, texto), accion ? el("div", { class: "actions" }, accion) : null);
}

function feedback(m) {
  const cont = el("div", { class: "feedback" }, el("span", {}, "¿Te ha sido útil?"));
  const actual = m.feedback ? m.feedback.valoracion : null;
  const boton = (valoracion, ic, clase, etiqueta) => {
    const b = el("button", { class: `btn btn-icon ${clase}`, type: "button", "aria-label": etiqueta,
      "aria-pressed": String(actual === valoracion), title: etiqueta }, icono(ic));
    b.addEventListener("click", () => (valoracion === "negativa" ? pedirComentario(m, cont) : valorar(m, "positiva")));
    return b;
  };
  cont.append(boton("positiva", "i-up", "up", "Útil"), boton("negativa", "i-down", "down", "No útil"));
  if (m.feedback) cont.append(el("span", { class: "gracias" }, "Gracias, valoración registrada."));
  return cont;
}

function pedirComentario(m, cont) {
  if (cont.querySelector("form")) return;
  const input = el("input", { type: "text", maxLength: 500, placeholder: "¿Qué falló? (opcional)", "aria-label": "Comentario" });
  const form = el("form", {}, input, el("button", { class: "btn btn-secondary", type: "submit" }, "Enviar"));
  form.addEventListener("submit", (ev) => { ev.preventDefault(); valorar(m, "negativa", input.value.trim() || null); });
  cont.append(form);
  input.focus();
}

async function valorar(m, valoracion, comentario = null) {
  const r = await api(`conversaciones/${encodeURIComponent(estado.conversacion.id)}/mensajes/${m.id}/feedback`,
    { metodo: "POST", json: { valoracion, comentario }, conRol: false });
  if (!r.ok) { alert(mensajeError(r)); return; }
  const i = estado.conversacion.mensajes.findIndex((x) => x.id === m.id);
  estado.conversacion.mensajes[i] = r.cuerpo;
  pintarChat();
}

const ETIQUETA_ACCION = { abrir_ticket: "Abrir ticket de soporte", solicitar_vacaciones: "Solicitar vacaciones" };
const ESTADO_ACCION = {
  pendiente: ["warn", "Pendiente de tu aprobación"],
  ejecutada: ["ok", "Ejecutada"],
  rechazada: ["info", "Rechazada"],
  error: ["danger", "Error al ejecutar"],
};

function tarjetasAccion(m) {
  if (!m.acciones || !m.acciones.length) return null;
  return el("div", { class: "acciones" }, m.acciones.map((a) => {
    const [clase, texto] = ESTADO_ACCION[a.estado] || ["", a.estado];
    const datos = el("dl", {}, Object.entries(a.datos).flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, String(v))]));
    const tarjeta = el("div", { class: `accion ${clase}`, role: "group", "aria-label": ETIQUETA_ACCION[a.tipo] || a.tipo },
      el("div", { class: "accion-head" }, el("strong", {}, ETIQUETA_ACCION[a.tipo] || a.tipo), el("span", { class: `badge ${clase}` }, texto)),
      datos, a.resultado ? el("p", { class: "hint" }, a.resultado) : null);
    if (a.estado === "pendiente") {
      const aprobar = el("button", { class: "btn btn-primary", type: "button" }, icono("i-check"), "Aprobar");
      const rechazar = el("button", { class: "btn btn-secondary", type: "button" }, "Rechazar");
      aprobar.addEventListener("click", () => decidir(m, a, true));
      rechazar.addEventListener("click", () => decidir(m, a, false));
      tarjeta.append(el("div", { class: "row" }, rechazar, aprobar),
        el("p", { class: "hint" }, "Nada se ejecuta hasta que lo apruebes."));
    }
    return tarjeta;
  }));
}

async function decidir(m, a, aprobar) {
  const r = await api(`acciones/${encodeURIComponent(a.id)}/decision`, { metodo: "POST", json: { aprobar } });
  if (!r.ok) { alert(mensajeError(r)); return; }
  m.acciones = m.acciones.map((x) => (x.id === a.id ? r.cuerpo : x));
  pintarChat();
}

// Aviso cuando se ocultaron datos personales o sensibles de la pregunta (no se guardaron).
function avisoOcultados(m) {
  const tipos = new Set(m.hallazgos.filter((h) => h.accion === "enmascarar" && ["pii", "dato_sensible"].includes(h.tipo)).map((h) => h.tipo));
  if (!tipos.size) return null;
  return notice("warn", "i-lock", "Hemos ocultado datos de tu mensaje",
    "No se guardan ni se envían al asistente. Evita compartir datos personales, bancarios o contraseñas en el chat.");
}

function pintarRespuesta(m, i) {
  const bloqueo = m.hallazgos.find((h) => h.accion === "bloquear");
  if (bloqueo && POLITICA[bloqueo.tipo]) {
    const [tipo, ic, titulo] = POLITICA[bloqueo.tipo];
    return notice(tipo, ic, titulo, m.respuesta);
  }
  if (bloqueo) {
    const motivo = MOTIVO_BLOQUEO[bloqueo.tipo] || "La consulta infringe la política de uso";
    return notice("warn", "i-ban", "Consulta bloqueada por la política de uso", `${motivo}. No se ha consultado ningún documento.`);
  }
  if (m.acciones && m.acciones.length && m.sin_contexto) {
    return el("article", { class: "msg-bot" },
      el("div", { class: "msg-meta" }, `Asistente · ${hora(m.creado_en)}`),
      el("p", { class: "answer" }, m.respuesta), tarjetasAccion(m), feedback(m));
  }
  if (m.aclaracion) {
    const opciones = m.aclaracion.opciones.map((o) => {
      const b = el("button", { class: "btn btn-secondary opcion", type: "button" }, o);
      b.addEventListener("click", () => { if (!(estado.pendiente && !estado.pendiente.error)) preguntar(o); });
      return b;
    });
    return el("article", { class: "msg-bot" },
      el("div", { class: "msg-meta" }, `Asistente · ${hora(m.creado_en)}`),
      el("p", { class: "answer" }, m.aclaracion.pregunta),
      opciones.length ? el("div", { class: "opciones", role: "group", "aria-label": "Elige una opción o escribe tu pregunta" }, opciones) : null,
      el("p", { class: "hint" }, "Elige una opción o escribe tu pregunta con más detalle."));
  }
  if (m.conversacional) {
    return el("article", { class: "msg-bot" },
      el("div", { class: "msg-meta" }, `Asistente · ${hora(m.creado_en)}`),
      el("p", { class: "answer" }, m.respuesta));
  }
  if (m.sin_contexto) {
    const busque = (m.consultas || []).map((q) => `«${q}»`).join(", ");
    const texto = busque
      ? `Busqué ${busque} en los documentos de tu rol y no encontré información. Prueba a concretar el tema o el periodo; si crees que debería estar, pide acceso al rol correspondiente.`
      : "Ningún documento visible para tu rol responde a esta pregunta. Si crees que debería, pide acceso al rol correspondiente.";
    return el("div", { class: "msg-bot" },
      notice("info", "i-info", "No encuentro esa información en tus documentos", texto),
      feedback(m));
  }
  const id = `m${i}`;
  const answer = el("p", { class: "answer" });
  const numeros = new Set(m.citas.map((c) => c.numero));
  for (const parte of m.respuesta.split(/(\[\d+\])/)) {
    const n = /^\[(\d+)\]$/.exec(parte);
    if (n && numeros.has(Number(n[1]))) {
      const b = el("button", { class: "cite", type: "button", "aria-label": `Fuente ${n[1]}` }, n[1]);
      const cita = m.citas.find((c) => c.numero === Number(n[1]));
      b.setAttribute("aria-label", `Fuente ${n[1]}: ${tituloDoc(cita.doc_id)}`);
      if (esDocumento(cita.doc_id)) b.addEventListener("click", () => abrirDocumento(cita.doc_id, cita.chunk_id));
      else b.disabled = true;
      answer.append(b);
    } else answer.append(parte);
  }
  return el("article", { class: "msg-bot", "aria-label": `Respuesta de las ${hora(m.creado_en)}` },
    el("div", { class: "msg-meta" }, `Asistente · ${hora(m.creado_en)}`,
      m.desde_cache ? el("span", { class: "badge", title: "Respuesta reutilizada: mismos documentos visibles para tu rol" }, " desde caché") : null),
    answer, fuentesPlegadas(m, id), tarjetasAccion(m), feedback(m));
}

// Fuentes plegadas: un botón con el número de documentos citados; al desplegarlo, cada
// documento se abre en el visor con el fragmento citado resaltado.
function fuentesPlegadas(m, id) {
  const docs = [];
  for (const c of m.citas) {
    const d = docs.find((x) => x.doc_id === c.doc_id);
    if (d) d.citas.push(c); else docs.push({ doc_id: c.doc_id, citas: [c] });
  }
  if (!docs.length) return null;
  const panelId = `${id}-fuentes`;
  const n = docs.length;
  const texto = `${n} ${n === 1 ? "fuente" : "fuentes"}`;
  const boton = el("button", { class: "sources-toggle", type: "button", "aria-expanded": "false", "aria-controls": panelId },
    icono("i-file"), texto, icono("i-chevron", "icon chevron"));
  const panel = el("div", { class: "sources-panel", id: panelId, hidden: true },
    docs.map((d) => {
      const refs = el("span", { class: "source-refs" }, d.citas.map((c) => `[${c.numero}]`).join(" "));
      if (!esDocumento(d.doc_id)) {
        return el("div", { class: "source-doc info" }, icono("i-info"),
          el("span", { class: "source-name" }, tituloDoc(d.doc_id)), refs, el("span"));
      }
      const b = el("button", { class: "source-doc", type: "button" },
        icono("i-file"),
        el("span", { class: "source-name" }, tituloDoc(d.doc_id)),
        refs,
        el("span", { class: "source-open" }, "Ver documento"));
      b.addEventListener("click", () => abrirDocumento(d.doc_id, d.citas[0].chunk_id));
      return b;
    }),
    (m.consultas || []).length
      ? el("p", { class: "hint consultas" }, "Búsqueda: ", m.consultas.map((q) => `«${q}»`).join(", "))
      : null);
  boton.addEventListener("click", () => {
    const abrir = panel.hidden;
    panel.hidden = !abrir;
    boton.setAttribute("aria-expanded", String(abrir));
  });
  return el("div", { class: "sources" }, boton, panel);
}

// ------------------------------------------------------------------ visor de documentos
// Markdown sencillo a elementos (títulos, listas, párrafos), siempre con textContent.
function bloquesMarkdown(md) {
  const limpio = (x) => x.replace(/(\*\*|__|`)/g, "");
  const bloques = [];
  let lista = null;
  let parrafo = null; // líneas seguidas = un párrafo (los .md suelen cortar líneas a mano)
  for (const linea of md.split("\n")) {
    const t = linea.trim();
    if (!t) { lista = null; parrafo = null; continue; }
    const titulo = /^(#{1,6})\s+(.*)$/.exec(t);
    const item = /^([-*+]|\d+\.)\s+(.*)$/.exec(t);
    if (titulo) {
      lista = parrafo = null;
      bloques.push(el(titulo[1].length <= 2 ? "h3" : "h4", {}, limpio(titulo[2])));
    } else if (item) {
      parrafo = null;
      if (!lista) { lista = el("ul"); bloques.push(lista); }
      lista.append(el("li", {}, limpio(item[2])));
    } else if (parrafo) {
      parrafo.append(` ${limpio(t)}`);
    } else {
      lista = null;
      parrafo = el("p", {}, limpio(t));
      bloques.push(parrafo);
    }
  }
  return bloques;
}

async function abrirDocumento(docId, chunkId) {
  const visor = $("visor");
  $("visor-titulo").textContent = tituloDoc(docId);
  $("visor-id").textContent = docId;
  $("visor-body").replaceChildren(el("div", { class: "skeleton", "aria-hidden": "true" }, el("span"), el("span"), el("span")));
  if (!visor.open) visor.showModal();
  const ruta = docId.split("/").map(encodeURIComponent).join("/");
  const r = await api(`documentos/${ruta}/contenido`);
  if (!r.ok) {
    $("visor-body").replaceChildren(notice("danger", "i-alert", "No se puede mostrar el documento",
      r.status === 404 ? "Ya no está disponible para tu rol." : mensajeError(r)));
    return;
  }
  $("visor-titulo").textContent = r.cuerpo.titulo;
  $("visor-body").replaceChildren(...r.cuerpo.fragmentos.map((f) => {
    const citado = f.chunk_id === chunkId;
    return el("section", { class: `visor-frag${citado ? " citado" : ""}`, "aria-label": citado ? "Fragmento citado" : null },
      citado ? el("span", { class: "badge accent" }, "Fragmento citado en la respuesta") : null,
      ...bloquesMarkdown(f.contenido));
  }));
  $("visor-body").querySelector(".citado")?.scrollIntoView({ block: "center" });
}

async function preguntar(texto) {
  estado.pendiente = { texto };
  pintarChat();
  $("enviar").disabled = true;
  try {
    const r = await api(`conversaciones/${encodeURIComponent(estado.conversacion.id)}/mensajes`,
      { metodo: "POST", json: { pregunta: texto }, conRol: false });
    if (r.ok) {
      estado.conversacion.mensajes.push(r.cuerpo);
      estado.pendiente = null;
      $("pregunta").value = "";
      cargarHistorial().then(pintarHistorial);
    } else if (r.status === 404) {
      salirDelRol();
      estado.aviso = { tipo: "danger", titulo: "La conversación ya no está disponible",
        texto: "El rol pudo desactivarse o dejar de estar asignado. Elige un rol de nuevo." };
      pintarChat();
      return;
    } else {
      estado.pendiente.error = `${mensajeError(r)}`.replace(/\.?$/, ".");
    }
  } catch (err) {
    estado.pendiente.error = `Error de red: ${err.message}.`;
  } finally {
    $("enviar").disabled = false;
  }
  pintarChat();
}

// ------------------------------------------------------------------ historial
function pintarHistorial() {
  $("hist-panel").hidden = !estado.rol;
  if (!estado.rol) return;
  $("hist-sub").textContent = `Con el rol ${estado.rol.nombre}. Solo las ves tú.`;
  const actual = estado.conversacion && estado.conversacion.id;
  const items = estado.historial.map((c) => {
    const b = el("button", { class: "conv", type: "button", "aria-current": c.id === actual ? "true" : null },
      icono("i-chat"),
      el("span", { class: "conv-title" }, c.titulo || "Sin título"),
      el("span", { class: "conv-meta" }, `${fecha(c.creada_en)} · ${c.mensajes} ${c.mensajes === 1 ? "pregunta" : "preguntas"}`));
    b.addEventListener("click", () => abrirConversacion(c.id));
    return el("li", {}, b);
  });
  $("conv-list").replaceChildren(...(items.length ? items
    : [el("li", { class: "doc-empty" }, "Aún no hay conversaciones con este rol. Las que empieces aparecerán aquí.")]));
}

// ------------------------------------------------------------------ documentos
const RESULTADO = {
  indexado: ["ok", "i-check", "Indexado"],
  actualizado: ["ok", "i-check", "Actualizado"],
  eliminado: ["ok", "i-check", "Eliminado"],
  sin_cambios: ["info", "i-info", "Sin cambios"],
  duplicado: ["warn", "i-alert", "Duplicado"],
  rechazado: ["danger", "i-ban", "Rechazado"],
  error: ["danger", "i-alert", "No se pudo completar"],
};

function pintarDocumentos() {
  const filtro = $("filtro-rol");
  const otros = new Set(estado.docs.flatMap((d) => d.roles));
  if (estado.rol) otros.delete(estado.rol.id);
  filtro.replaceChildren(el("option", { value: "*" }, "Todos mis documentos"),
    [...otros].sort().map((r) => el("option", { value: r }, `Compartidos con ${nombreRol(r)}`)));
  if (![...filtro.options].some((o) => o.value === estado.filtro)) estado.filtro = "*";
  filtro.value = estado.filtro;
  filtro.disabled = !estado.rol;

  const gestor = puede("gestionar_documentos");
  const items = estado.docs
    .filter((d) => estado.filtro === "*" || d.roles.includes(estado.filtro))
    .map((d) => {
      const acciones = el("div", { class: "doc-actions" });
      if (gestor) {
        const b = el("button", { class: "btn btn-icon btn-danger-ghost", type: "button", "aria-label": `Eliminar ${d.titulo}`, title: "Eliminar" }, icono("i-trash"));
        b.addEventListener("click", () => eliminar(d));
        acciones.append(b);
      }
      return el("li", { class: "doc" },
        icono("i-file"), el("span", { class: "doc-title" }, d.titulo), acciones,
        el("span", { class: "doc-meta" },
          d.roles.map((r) => el("span", { class: `badge${estado.rol && r === estado.rol.id ? " accent" : ""}` }, nombreRol(r))),
          el("span", {}, `${d.chunks} frag.`),
          d.estado !== "activo" ? el("span", { class: "badge", title: d.motivo_estado || "" }, `En revisión: ${d.estado}`) : null));
    });
  const vacio = !estado.rol ? "Elige un rol para ver sus documentos."
    : estado.filtro === "*" ? "Ningún documento es visible para este rol." : "Ninguno de tus documentos es visible también para ese rol.";
  $("doc-list").replaceChildren(...(items.length ? items : [el("li", { class: "doc-empty" }, vacio)]));

  // Subida: solo roles con gestión; destinos = publica_para + el propio rol (siempre incluido).
  $("upload-panel").hidden = !gestor;
  if (gestor) {
    const destinos = [...new Set([estado.rol.id, ...estado.rol.publica_para])];
    $("roles-pick").replaceChildren(...destinos.map((r) => el("label", { class: "chip" },
      el("input", { type: "checkbox", value: r, checked: r === estado.rol.id, disabled: r === estado.rol.id }),
      el("span", {}, icono("i-check"), nombreRol(r)))));
    $("roles-pick-hint").textContent = `${estado.rol.nombre} se incluye siempre. Solo los roles marcados podrán consultarlo.`;
  }
  $("upload-results").replaceChildren(...estado.subidas.map((s) => {
    const [clase, ic, titulo] = RESULTADO[s.estado] || RESULTADO.error;
    return el("li", { class: `result ${clase}` },
      icono(ic),
      el("span", { class: "result-title" }, titulo, s.chunks ? ` · ${s.chunks} fragmentos` : ""),
      el("span", { class: "result-doc" }, s.doc_id || s.nombre),
      s.detalles.length ? el("ul", {}, s.detalles.map((d) => el("li", {}, d))) : null);
  }));
}

async function subir(ev) {
  ev.preventDefault();
  const archivo = $("archivo").files[0];
  if (!archivo) { $("archivo").click(); return; }
  if (archivo.size > 1_000_000) {
    estado.subidas.unshift({ estado: "rechazado", nombre: archivo.name, detalles: ["El archivo supera 1 MB."] });
    pintarDocumentos();
    return;
  }
  const form = new FormData();
  for (const c of $("roles-pick").querySelectorAll("input:checked")) form.append("roles", c.value);
  form.append("archivo", archivo);
  $("subir").disabled = true;
  try {
    const r = await api("documentos", { metodo: "POST", form });
    const info = r.ok ? r.cuerpo : r.cuerpo && r.cuerpo.detail && r.cuerpo.detail.estado ? r.cuerpo.detail : null;
    if (info) {
      estado.subidas.unshift({
        estado: info.estado, doc_id: info.doc_id, chunks: info.chunks,
        detalles: [...info.motivos, ...info.avisos, info.duplicado_de ? `Mismo contenido que ${info.duplicado_de}.` : null].filter(Boolean),
      });
    } else {
      const detalle = r.status === 503 ? `El documento es válido, pero no se pudo indexar: ${mensajeError(r)}` : mensajeError(r);
      estado.subidas.unshift({ estado: "error", nombre: archivo.name, detalles: [detalle] });
    }
    $("archivo").value = "";
    document.querySelector(".dropzone .file-chosen")?.remove();
    await cargarDatosDelRol();
  } catch (err) {
    estado.subidas.unshift({ estado: "error", nombre: archivo.name, detalles: [`Error de red: ${err.message}`] });
  } finally {
    $("subir").disabled = false;
  }
  pintarTodo();
}

async function eliminar(doc) {
  if (!confirm(`¿Eliminar «${doc.titulo}» del índice? Dejará de estar disponible para ${doc.roles.length} rol(es).`)) return;
  const ruta = doc.doc_id.split("/").map(encodeURIComponent).join("/");
  const r = await api(`documentos/${ruta}`, { metodo: "DELETE" });
  estado.subidas.unshift(r.ok
    ? { estado: "eliminado", doc_id: doc.doc_id, detalles: [] }
    : { estado: "error", doc_id: doc.doc_id, detalles: [mensajeError(r)] });
  await cargarDatosDelRol();
  pintarTodo();
}

// ------------------------------------------------------------------ roles y permisos
function pintarRoles() {
  const admin = puede("administrar_roles");
  // Sin rol, las tarjetas del chat ya muestran los roles; sin permisos de administración,
  // solo interesa el rol activo (no el catálogo completo con identificadores internos).
  $("roles-panel").hidden = !estado.rol;
  if (!estado.rol) return;
  const lista = admin ? estado.todos : [estado.rol];
  $("roles-title").textContent = admin ? "Roles y permisos" : "Tu rol";
  $("roles-sub").textContent = admin
    ? "Como administrador puedes crear roles y asignar permisos."
    : "Qué puede hacer este rol. Solo un rol administrador puede cambiarlo.";
  $("new-role").hidden = !admin;
  $("role-list").replaceChildren(...lista.flatMap((r) => {
    let editarBtn = null;
    const desc = el("span", { class: "role-item-desc" }, r.descripcion || "Sin descripción.",
      r.publica_para.length ? ` · Publica para: ${r.publica_para.map(nombreRol).join(", ")}` : "");
    if (admin) {
      const abierto = estado.editando === r.id;
      editarBtn = el("button", { class: "link role-item-edit", type: "button", "aria-expanded": String(abierto) }, abierto ? "Cerrar" : "Editar permisos");
      editarBtn.addEventListener("click", () => { estado.editando = abierto ? null : r.id; pintarRoles(); });
    }
    const li = el("li", { class: `role-item${r.activo ? "" : " inactive"}` },
      el("span", { class: "role-item-name" }, r.nombre, " ", el("span", { class: "mono hint" }, r.id)),
      el("span", { class: "badges" },
        r.activo ? null : el("span", { class: "badge" }, "Inactivo"),
        r.permisos.includes("gestionar_documentos") ? el("span", { class: "badge accent" }, "Gestión") : null,
        r.permisos.includes("administrar_roles") ? el("span", { class: "badge accent" }, "Admin") : null),
      desc, editarBtn);
    return admin && estado.editando === r.id ? [li, editorRol(r)] : [li];
  }));
}

function editorRol(r) {
  const check = (clase, texto, marcado) => el("label", { class: "check" }, el("input", { type: "checkbox", className: clase, checked: marcado }), texto);
  const destinos = estado.todos.filter((x) => x.id !== r.id).map((x) => el("label", { class: "chip" },
    el("input", { type: "checkbox", value: x.id, checked: r.publica_para.includes(x.id) }),
    el("span", {}, icono("i-check"), x.nombre)));
  const error = el("p", { class: "form-error", role: "alert" });
  const guardarBtn = el("button", { class: "btn btn-primary", type: "submit" }, "Guardar");
  const form = el("form", { class: "role-edit" },
    el("h3", {}, `Permisos de ${r.nombre}`),
    check("e-activo", "Activo", r.activo),
    check("e-gestion", "Puede gestionar documentos", r.permisos.includes("gestionar_documentos")),
    check("e-admin", "Puede administrar roles", r.permisos.includes("administrar_roles")),
    el("fieldset", { class: "roles-pick" }, el("legend", {}, "Puede publicar documentos para"),
      el("div", { class: "chips" }, destinos), el("p", { class: "hint" }, "Su propio rol se incluye siempre al publicar.")),
    error, el("div", { class: "row" }, guardarBtn));
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const permisos = [
      form.querySelector(".e-gestion").checked && "gestionar_documentos",
      form.querySelector(".e-admin").checked && "administrar_roles",
    ].filter(Boolean);
    const publica_para = [...form.querySelectorAll(".chips input:checked")].map((i) => i.value);
    guardarBtn.disabled = true;
    const resp = await api(`roles/${encodeURIComponent(r.id)}`, {
      metodo: "PATCH", json: { activo: form.querySelector(".e-activo").checked, permisos, publica_para },
    });
    guardarBtn.disabled = false;
    if (!resp.ok) { error.textContent = mensajeError(resp); return; }
    estado.editando = null;
    await refrescarRoles();
  });
  return el("li", {}, form);
}

async function refrescarRoles() {
  const r = await api("roles", { conRol: false });
  if (r.ok) estado.roles = r.cuerpo;
  if (estado.rol) {
    // El rol activo pudo cambiar de permisos o quedar desactivado.
    const actualizado = estado.roles.find((x) => x.id === estado.rol.id);
    if (!actualizado) { salirDelRol(); return; }
    estado.rol = actualizado;
    await cargarDatosDelRol();
  }
  pintarTodo();
}

async function crearRol(ev) {
  ev.preventDefault();
  const permisos = [$("rol-gestion").checked && "gestionar_documentos", $("rol-admin").checked && "administrar_roles"].filter(Boolean);
  const r = await api("roles", {
    metodo: "POST",
    json: { id: $("rol-id").value.trim(), nombre: $("rol-nombre").value.trim(), descripcion: $("rol-desc").value.trim(), permisos },
  });
  if (!r.ok) { $("role-form-error").textContent = mensajeError(r); return; }
  $("role-form").reset();
  $("role-form-error").textContent = "";
  $("new-role").open = false;
  await refrescarRoles();
}

// ------------------------------------------------------------------ eventos
function pintarTodo() { pintarCabecera(); pintarChat(); pintarHistorial(); pintarDocumentos(); pintarRoles(); }

$("composer").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const texto = $("pregunta").value.trim();
  const enCurso = estado.pendiente && !estado.pendiente.error;
  if (texto && !enCurso) {
    $("pregunta").value = ""; // la pregunta ya se ve en el chat; si falla, «Reintentar» la reenvía
    preguntar(texto);
  }
});
$("pregunta").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); $("composer").requestSubmit(); }
});
$("filtro-rol").addEventListener("change", (ev) => { estado.filtro = ev.target.value; pintarDocumentos(); });
$("upload-form").addEventListener("submit", subir);
$("role-form").addEventListener("submit", crearRol);

const zona = document.querySelector(".dropzone");
$("archivo").addEventListener("change", () => {
  const f = $("archivo").files[0];
  zona.querySelector(".file-chosen")?.remove();
  if (f) zona.append(el("span", { class: "file-chosen" }, `${f.name} · ${(f.size / 1024).toFixed(1)} KB`));
});
for (const tipo of ["dragenter", "dragover"]) zona.addEventListener(tipo, (e) => { e.preventDefault(); zona.classList.add("over"); });
for (const tipo of ["dragleave", "drop"]) zona.addEventListener(tipo, (e) => { e.preventDefault(); zona.classList.remove("over"); });
zona.addEventListener("drop", (e) => { $("archivo").files = e.dataTransfer.files; $("archivo").dispatchEvent(new Event("change")); });

$("visor-cerrar").addEventListener("click", () => $("visor").close());
// Clic en el fondo (fuera del panel) también cierra; Esc lo gestiona el propio <dialog>.
$("visor").addEventListener("click", (ev) => { if (ev.target === $("visor")) $("visor").close(); });

iniciar();
