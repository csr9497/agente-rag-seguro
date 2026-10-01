// Regenera las capturas de docs/ui contra scripts/capturas/servidor.py (puerto 8790).
//   uv run uvicorn scripts.capturas.servidor:app --port 8790 &
//   NODE_PATH=$(npm root -g) node scripts/capturas/capturas.cjs
// Requiere Playwright (npm i -g playwright) y Chromium (PLAYWRIGHT_BROWSERS_PATH o CHROMIUM).
const { chromium } = require("playwright");

(async () => {

const BASE = process.env.BASE_URL || "http://localhost:8790";
const SALIDA = "docs/ui";
const navegador = await chromium.launch({ executablePath: process.env.CHROMIUM || undefined });

async function pagina(ancho = 1440, alto = 900) {
  const ctx = await navegador.newContext({ viewport: { width: ancho, height: alto }, deviceScaleFactor: 1, colorScheme: "light", locale: "es-ES" });
  const p = await ctx.newPage();
  await p.goto(BASE);
  await p.waitForSelector(".role-card");
  return p;
}

async function rol(p, nombre) {
  await p.getByRole("button", { name: new RegExp(nombre) }).first().click();
  await p.waitForSelector("#composer:not([hidden])");
}

async function preguntar(p, texto) {
  const antes = await p.locator(".msg, .message, .turno, article").count();
  await p.fill("#pregunta", texto);
  await p.click("#enviar");
  await p.waitForFunction((n) => document.querySelectorAll(".msg, .message, .turno, article").length > n
    && !document.querySelector(".typing, .pensando, [aria-busy='true']"), antes, { timeout: 15000 }).catch(() => {});
  await p.waitForTimeout(600);
}

const foto = (p, nombre) => p.screenshot({ path: `${SALIDA}/${nombre}.png` });

// 1. Elegir rol
let p = await pagina();
await foto(p, "1-roles");

// 2. Empleado general: respuesta con cita
await rol(p, "Empleado general");
await preguntar(p, "¿Cuántos días de vacaciones tengo al año?");
await foto(p, "2-respuesta-citada");

// 3. Mismo usuario, dato de otro rol: no se filtra
await preguntar(p, "¿Cuáles son las bandas salariales?");
await foto(p, "3-sin-acceso");

// 4. Recursos Humanos: documentos de su rol y subida
p = await pagina();
await rol(p, "Recursos Humanos");
await preguntar(p, "¿Cuáles son las bandas salariales?");
await foto(p, "4-rrhh");

// 5. Acción con aprobación humana
p = await pagina();
await rol(p, "Empleado general");
await preguntar(p, "Abre un ticket: la VPN no conecta desde casa");
await foto(p, "5-accion");

// 6. Administrador: roles y permisos
p = await pagina();
await rol(p, "Administrador");
await p.screenshot({ path: `${SALIDA}/6-admin.png`, fullPage: true });

// 7. Móvil
p = await pagina(390, 844);
await rol(p, "Empleado general");
await preguntar(p, "¿Cuántos días de vacaciones tengo al año?");
await foto(p, "7-movil");

await navegador.close();
console.log("capturas en", SALIDA);
})();
