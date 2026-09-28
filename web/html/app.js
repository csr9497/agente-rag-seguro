// Interfaz de una sola página. Todo el contenido del servidor se pinta con textContent.
// El rol activo se envía en la cabecera X-Rol; el servidor valida que se puede usar.

const $ = (id) => document.getElementById(id);
const MOTIVO_BLOQUEO = {
  inyeccion: "Inyección de prompt detectada",
  texto_oculto: "Texto oculto en la pregunta",
  fuga_prompt: "La respuesta revelaba instrucciones internas",
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
const tituloDoc = (id) => (estado.docs.find((d) => d.doc_id === id) || { titulo: id.split("/").pop() }).titulo;
const puede = (permiso) => !!estado.rol && estado.rol.permisos.includes(permiso);
const hora = (iso) => (iso ? new Date(iso).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" }) : "");
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
  const r = await api("roles", { conRol: false });
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
  ]);
  estado.docs = docs.ok ? docs.cuerpo : [];
  estado.todos = todos.ok ? todos.cuerpo : [];
}

async function elegirRol(rol) {
  const r = await api("conversaciones", { metodo: "POST", json: { rol_id: rol.id }, conRol: false });
  if (!r.ok) { alert(mensajeError(r)); return; }
  Object.assign(estado, { rol, conversacion: r.cuerpo, filtro: "*", subidas: [], pendiente: null, editando: null });
  $("pregunta").value = "";
  guardar("conversacion", r.cuerpo.id);
  await cargarDatosDelRol();
  pintarTodo();
  $("pregunta").focus();
}

function salirDelRol() {
  Object.assign(estado, { rol: null, conversacion: null, docs: [], todos: [], pendiente: null, editando: null });
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
    body.replaceChildren(el("div", { class: "role-picker" },
      el("p", {}, "¿Con qué rol quieres consultar? Solo verás respuestas basadas en los documentos de ese rol."),
      tarjetas.length ? el("div", { class: "role-cards" }, tarjetas) : el("p", { class: "hint" }, "No tienes roles disponibles.")));
    return;
  }
  $("chat-sub").textContent = `Con permisos de ${estado.rol.nombre} · ${estado.docs.length} documentos disponibles`;
  $("composer-hint").textContent = `Las respuestas solo usan documentos visibles para ${estado.rol.nombre}.`;

  const mensajes = (estado.conversacion ? estado.conversacion.mensajes : []).flatMap((m, i) => [
    el("div", { class: "msg-user" }, el("div", { class: "msg-meta" }, `Tú · ${hora(m.creado_en)}`), m.pregunta),
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
      mensajes.push(el("div", { class: "msg-bot", "aria-busy": "true" },
        el("div", { class: "status-line" }, el("span", { class: "spinner", "aria-hidden": "true" }),
          el("span", {}, "Buscando en los documentos de ", el("strong", {}, estado.rol.nombre), "…")),
        el("div", { class: "skeleton", "aria-hidden": "true" }, el("span"), el("span"), el("span"))));
    }
  }
  if (!mensajes.length) {
    body.replaceChildren(el("div", { class: "empty" },
      el("h3", {}, "Nueva conversación"),
      el("p", { class: "hint" }, "Pregunta en lenguaje natural. Cada respuesta indica qué documentos se consultaron y cuáles se citaron.")));
    return;
  }
  body.replaceChildren(...mensajes);
  body.scrollTop = body.scrollHeight;
}

function notice(tipo, ic, titulo, texto, accion) {
  return el("div", { class: `notice ${tipo}`, role: tipo === "danger" ? "alert" : "status" },
    icono(ic), el("strong", {}, titulo), el("p", {}, texto), accion ? el("div", { class: "actions" }, accion) : null);
}

function consultados(m) {
  if (!m.documentos_consultados.length && !m.fragmentos_descartados) return null;
  const citados = new Set(m.citas.map((c) => c.doc_id));
  const fila = el("div", { class: "consulted" }, el("span", { class: "consulted-label" }, "Documentos consultados:"));
  for (const d of m.documentos_consultados) {
    const citado = citados.has(d);
    fila.append(el("span", { class: `doc-pill${citado ? " cited" : ""}`, title: d },
      icono(citado ? "i-check" : "i-file"), tituloDoc(d),
      el("span", { class: "sr-only" }, citado ? " (citado)" : " (consultado, no citado)")));
  }
  if (m.fragmentos_descartados) {
    const n = m.fragmentos_descartados;
    fila.append(el("span", { class: "discarded", title: "Fragmentos que la búsqueda devolvió pero no corresponden a tu rol" },
      icono("i-lock"), `${n} fragmento${n > 1 ? "s" : ""} descartado${n > 1 ? "s" : ""} por permisos`));
  }
  return fila;
}

function pintarRespuesta(m, i) {
  const bloqueo = m.hallazgos.find((h) => h.accion === "bloquear");
  if (bloqueo) {
    const motivo = MOTIVO_BLOQUEO[bloqueo.tipo] || "La consulta infringe la política de uso";
    return notice("warn", "i-ban", "Consulta bloqueada por la política de uso", `${motivo}. No se ha consultado ningún documento.`);
  }
  if (m.sin_contexto) {
    return el("div", { class: "msg-bot" },
      notice("info", "i-info", "No encuentro esa información en tus documentos",
        "Ningún documento visible para tu rol responde a esta pregunta. Si crees que debería, pide acceso al rol correspondiente."),
      consultados(m));
  }
  const id = `m${i}`;
  const answer = el("p", { class: "answer" });
  const numeros = new Set(m.citas.map((c) => c.numero));
  for (const parte of m.respuesta.split(/(\[\d+\])/)) {
    const n = /^\[(\d+)\]$/.exec(parte);
    if (n && numeros.has(Number(n[1]))) {
      const b = el("button", { class: "cite", type: "button", "aria-label": `Fuente ${n[1]}` }, n[1]);
      b.addEventListener("click", () => document.getElementById(`${id}-f${n[1]}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" }));
      answer.append(b);
    } else answer.append(parte);
  }
  const fuentes = el("div", { class: "sources" },
    el("div", { class: "sources-head" }, el("span", {}, "Fuentes citadas"), el("span", {}, String(m.citas.length))),
    m.citas.map((c) => el("div", { class: "source", id: `${id}-f${c.numero}` },
      el("span", { class: "source-n" }, c.numero),
      el("span", { class: "source-name" }, tituloDoc(c.doc_id), " ", el("span", { class: "mono hint" }, c.doc_id)),
      el("span", { class: "source-frag" }, c.fragmento))));
  return el("article", { class: "msg-bot", "aria-label": `Respuesta de las ${hora(m.creado_en)}` },
    el("div", { class: "msg-meta" }, `Asistente · ${hora(m.creado_en)}`), answer, fuentes, consultados(m));
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
    } else if (r.status === 404) {
      estado.pendiente = null;
      alert("La conversación ya no está disponible (¿rol desactivado?). Elige un rol de nuevo.");
      salirDelRol();
      return;
    } else {
      estado.pendiente.error = r.status === 503 ? "El servicio de IA no está disponible (HTTP 503)." : `${mensajeError(r)}.`;
    }
  } catch (err) {
    estado.pendiente.error = `Error de red: ${err.message}.`;
  } finally {
    $("enviar").disabled = false;
  }
  pintarChat();
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
  const lista = admin ? estado.todos : estado.roles;
  $("roles-sub").textContent = admin
    ? "Como administrador puedes crear roles y asignar permisos."
    : "Solo un rol administrador puede cambiar roles y permisos.";
  $("new-role").hidden = !admin;
  $("role-list").replaceChildren(...lista.flatMap((r) => {
    const desc = el("span", { class: "role-item-desc" }, r.descripcion || "Sin descripción.",
      r.publica_para.length ? ` · Publica para: ${r.publica_para.map(nombreRol).join(", ")}` : "");
    if (admin) {
      const abierto = estado.editando === r.id;
      const editar = el("button", { class: "link", type: "button", "aria-expanded": String(abierto) }, abierto ? "Cerrar" : "Editar permisos");
      editar.addEventListener("click", () => { estado.editando = abierto ? null : r.id; pintarRoles(); });
      desc.append(" ", editar);
    }
    const li = el("li", { class: `role-item${r.activo ? "" : " inactive"}` },
      el("span", { class: "role-item-name" }, r.nombre, " ", el("span", { class: "mono hint" }, r.id)),
      el("span", { class: "badges" },
        r.activo ? null : el("span", { class: "badge" }, "Inactivo"),
        r.permisos.includes("gestionar_documentos") ? el("span", { class: "badge accent" }, "Gestión") : null,
        r.permisos.includes("administrar_roles") ? el("span", { class: "badge accent" }, "Admin") : null),
      desc);
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
function pintarTodo() { pintarCabecera(); pintarChat(); pintarDocumentos(); pintarRoles(); }

$("composer").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const texto = $("pregunta").value.trim();
  const enCurso = estado.pendiente && !estado.pendiente.error;
  if (texto && !enCurso) preguntar(texto);
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

iniciar();
