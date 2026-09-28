// Cliente mínimo. Todo el contenido del servidor se pinta con textContent (sin innerHTML).
const $ = (id) => document.getElementById(id);
let perfil = { groups: [], grupos_editables: [] };

function el(tag, attrs = {}, text) {
  const node = document.createElement(tag);
  Object.assign(node, attrs);
  if (text !== undefined) node.textContent = text;
  return node;
}

async function api(ruta, opciones = {}) {
  const resp = await fetch(`api/${ruta}`, opciones);
  let cuerpo = null;
  try { cuerpo = await resp.json(); } catch { /* sin cuerpo JSON */ }
  return { ok: resp.ok, status: resp.status, cuerpo };
}

// ------------------------------------------------------------------ pestañas
function mostrar(tab) {
  for (const [boton, panel] of [["tab-chat", "panel-chat"], ["tab-docs", "panel-docs"]]) {
    const activo = boton === tab;
    $(boton).setAttribute("aria-selected", String(activo));
    $(panel).hidden = !activo;
  }
  if (tab === "tab-docs") cargarDocumentos();
}
$("tab-chat").addEventListener("click", () => { history.replaceState(null, "", "#"); mostrar("tab-chat"); });
$("tab-docs").addEventListener("click", () => { history.replaceState(null, "", "#documentos"); mostrar("tab-docs"); });

// ------------------------------------------------------------------ perfil
async function cargarPerfil() {
  const r = await api("yo");
  if (!r.ok) { $("perfil").textContent = "No se pudo cargar el perfil."; return; }
  perfil = r.cuerpo;
  $("perfil").textContent = `Usuario: ${perfil.id} · grupos: ${perfil.groups.join(", ") || "ninguno"}`;
  const select = $("grupo");
  select.replaceChildren(...perfil.grupos_editables.map((g) => el("option", { value: g }, g)));
  const puede = perfil.grupos_editables.length > 0;
  $("form-subir").hidden = !puede;
  $("sin-edicion").hidden = puede;
}

// ------------------------------------------------------------------ chat
function pintarRespuesta(data) {
  const card = el("div", { className: "card" });
  card.append(el("p", { className: data.sin_contexto ? "aviso" : "" }, data.respuesta));
  if (data.citas.length) {
    const lista = el("ol", { className: "citas" });
    for (const c of data.citas) {
      const li = el("li", { value: c.numero });
      li.append(el("span", { className: "mono" }, c.fuente), document.createTextNode(` — ${c.fragmento}`));
      lista.append(li);
    }
    card.append(el("strong", {}, "Fuentes"), lista);
  }
  $("resultado").replaceChildren(card);
}

$("form-chat").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("enviar").disabled = true;
  $("resultado").replaceChildren(el("p", { className: "aviso" }, "Buscando…"));
  try {
    const r = await api("consultar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pregunta: $("pregunta").value.trim() }),
    });
    if (r.ok) pintarRespuesta(r.cuerpo);
    else $("resultado").replaceChildren(el("p", { className: "err" }, mensajeError(r)));
  } catch (err) {
    $("resultado").replaceChildren(el("p", { className: "err" }, `Error de red: ${err.message}`));
  } finally {
    $("enviar").disabled = false;
  }
});

// ------------------------------------------------------------------ documentos
function mensajeError(r) {
  const d = r.cuerpo && r.cuerpo.detail;
  if (typeof d === "string") return `${d} (HTTP ${r.status})`;
  if (d && d.motivos) return d.motivos.join("; ");
  return `Error ${r.status}`;
}

const TEXTO_ESTADO = {
  indexado: ["ok", "Indexado"],
  actualizado: ["ok", "Actualizado (se reemplazó la versión anterior)"],
  sin_cambios: ["warn", "Sin cambios: ya estaba indexado con el mismo contenido"],
  duplicado: ["warn", "Duplicado"],
  rechazado: ["err", "Rechazado"],
  eliminado: ["ok", "Eliminado"],
};

function pintarResultadoSubida(r) {
  const destino = $("resultado-subida");
  // Éxito: el resultado viene en el cuerpo; 409/404/422 del gestor: en detail.
  const info = r.cuerpo && r.cuerpo.estado ? r.cuerpo : r.cuerpo && r.cuerpo.detail;
  if (!info || !info.estado) {
    const aviso = r.status === 503
      ? `El documento es válido, pero no se pudo indexar: ${mensajeError(r)}`
      : mensajeError(r);
    destino.replaceChildren(el("p", { className: "err" }, aviso));
    return;
  }
  const [clase, texto] = TEXTO_ESTADO[info.estado] || ["", info.estado];
  const bloque = el("div", { className: "card" });
  const titulo = el("p", { className: clase }, `${texto}: `);
  titulo.append(el("span", { className: "mono" }, info.doc_id));
  bloque.append(titulo);
  const detalles = [...(info.motivos || []), ...(info.avisos || [])];
  if (info.chunks) detalles.unshift(`${info.chunks} fragmento(s) indexados`);
  if (detalles.length) {
    const lista = el("ul");
    for (const d of detalles) lista.append(el("li", {}, d));
    bloque.append(lista);
  }
  destino.replaceChildren(bloque);
}

$("form-subir").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const archivo = $("archivo").files[0];
  if (!archivo) return;
  if (archivo.size > 1_000_000) {
    $("resultado-subida").replaceChildren(el("p", { className: "err" }, "El archivo supera 1 MB."));
    return;
  }
  const datos = new FormData();
  datos.append("grupo", $("grupo").value);
  datos.append("archivo", archivo);
  $("subir").disabled = true;
  $("resultado-subida").replaceChildren(el("p", { className: "aviso" }, "Validando e indexando…"));
  try {
    pintarResultadoSubida(await api("documentos", { method: "POST", body: datos }));
    $("archivo").value = "";
    cargarDocumentos();
  } catch (err) {
    $("resultado-subida").replaceChildren(el("p", { className: "err" }, `Error de red: ${err.message}`));
  } finally {
    $("subir").disabled = false;
  }
});

async function borrar(docId) {
  if (!confirm(`¿Eliminar ${docId} del índice?`)) return;
  const ruta = docId.split("/").map(encodeURIComponent).join("/");
  const r = await api(`documentos/${ruta}`, { method: "DELETE" });
  pintarResultadoSubida(r);
  cargarDocumentos();
}

async function cargarDocumentos() {
  const destino = $("lista-docs");
  const r = await api("documentos");
  if (!r.ok) { destino.replaceChildren(el("p", { className: "err" }, mensajeError(r))); return; }
  if (!r.cuerpo.length) { destino.replaceChildren(el("p", { className: "aviso" }, "No hay documentos indexados.")); return; }
  const tabla = el("table");
  const cab = el("tr");
  for (const t of ["Documento", "Grupo", "Fragmentos", "Indexado", ""]) cab.append(el("th", {}, t));
  tabla.append(cab);
  for (const d of r.cuerpo) {
    const fila = el("tr");
    fila.append(
      el("td", { className: "mono" }, d.doc_id),
      el("td", {}, d.acl_groups.join(", ")),
      el("td", {}, String(d.chunks)),
      el("td", {}, d.indexado_en ? new Date(d.indexado_en).toLocaleString() : "—"),
    );
    const celda = el("td");
    if (d.acl_groups.some((g) => perfil.grupos_editables.includes(g))) {
      const boton = el("button", { className: "borrar", type: "button" }, "Eliminar");
      boton.addEventListener("click", () => borrar(d.doc_id));
      celda.append(boton);
    }
    fila.append(celda);
    tabla.append(fila);
  }
  destino.replaceChildren(tabla);
}

cargarPerfil().then(() => mostrar(location.hash === "#documentos" ? "tab-docs" : "tab-chat"));
