// Cliente mínimo: todo el contenido del servidor se pinta con textContent (sin innerHTML).
const form = document.getElementById("form");
const pregunta = document.getElementById("pregunta");
const enviar = document.getElementById("enviar");
const resultado = document.getElementById("resultado");

function el(tag, attrs = {}, text) {
  const node = document.createElement(tag);
  Object.assign(node, attrs);
  if (text !== undefined) node.textContent = text;
  return node;
}

function pintar(data) {
  const card = el("div", { className: "card" });
  card.append(el("p", { className: data.sin_contexto ? "aviso" : "" }, data.respuesta));
  if (data.citas.length) {
    const lista = el("ol", { className: "citas" });
    for (const c of data.citas) {
      const li = el("li", { value: c.numero });
      li.append(el("span", { className: "fuente" }, c.fuente), document.createTextNode(` — ${c.fragmento}`));
      lista.append(li);
    }
    card.append(el("strong", {}, "Fuentes"), lista);
  }
  resultado.replaceChildren(card);
}

form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  enviar.disabled = true;
  resultado.replaceChildren(el("p", { className: "aviso" }, "Buscando…"));
  try {
    const resp = await fetch("api/consultar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pregunta: pregunta.value.trim() }),
    });
    if (!resp.ok) throw new Error(`Error ${resp.status}`);
    pintar(await resp.json());
  } catch (err) {
    resultado.replaceChildren(el("p", { className: "error" }, `No se pudo completar la consulta (${err.message}).`));
  } finally {
    enviar.disabled = false;
  }
});
