/* Kronos Studio - PWA
 *
 * El motor de medicion es el mismo paquete Python del repositorio, corriendo
 * dentro del navegador sobre Pyodide. Este fichero es solo el puente: recoge lo
 * que el usuario define, se lo pasa a Python y pinta lo que devuelve.
 *
 * Regla que gobierna este fichero: **ningun calculo estadistico vive aqui.**
 * Los umbrales, los p-valores, la correccion por intentos y los graficos salen
 * de `kronos`, que esta cubierto por 469 tests. Reimplementar cualquiera de esas
 * cosas en JavaScript crearia una segunda verdad sin tests que la sujete, y la
 * version que ve el usuario seria justo la no verificada.
 */

"use strict";

const $ = (sel) => document.querySelector(sel);
const crear = (etiqueta, props = {}) => Object.assign(document.createElement(etiqueta), props);

let py = null;                 // interprete Pyodide
let canales = {};              // catalogo que publica Python
let operadores = {};
let hayDatos = false;
let claveDatos = "";
let reglas = [];               // [{condiciones:[...], direccion:1|-1}]
let pendientes = [];           // condiciones de la regla en curso
let modo = "binarias";

/* --------------------------------------------------------------------- *
 * Persistencia: localStorage. Sin cuentas, sin servidor, sin datos que
 * custodiar. Si el usuario borra los datos del navegador, se pierde, y eso
 * es un intercambio honesto a cambio de no pedirle un registro.
 * --------------------------------------------------------------------- */
const Guardado = {
  intentos: () => JSON.parse(localStorage.getItem("kronos.intentos") || "{}"),
  fijarIntentos: (o) => localStorage.setItem("kronos.intentos", JSON.stringify(o)),
  biblioteca: () => JSON.parse(localStorage.getItem("kronos.biblioteca") || "[]"),
  fijarBiblioteca: (a) => localStorage.setItem("kronos.biblioteca", JSON.stringify(a)),
};

const intentosDe = (clave) => Guardado.intentos()[clave] || 0;

function sumarIntento(clave) {
  const o = Guardado.intentos();
  o[clave] = (o[clave] || 0) + 1;
  Guardado.fijarIntentos(o);
  return o[clave];
}

/* --------------------------------------------------------------------- *
 * Arranque
 * --------------------------------------------------------------------- */
async function arrancar() {
  const texto = $("#arranque-texto");
  try {
    texto.textContent = "Descargando Python…";
    py = await loadPyodide();

    texto.textContent = "Cargando el motor de Kronos…";
    const zip = await (await fetch("kronos.zip")).arrayBuffer();
    await py.unpackArchive(zip, "zip");

    texto.textContent = "Preparando…";
    await py.runPythonAsync(`
import json, sys
sys.path.insert(0, "/home/pyodide")

from kronos.core.candle import Candle, Series
from kronos.data import loader
from kronos.research.reglas import CANALES, OPERADORES, Condicion, EstrategiaDeclarativa, Regla
from kronos.research.veredicto import MIN_OPERACIONES, evaluar_estrategia
from kronos.research.curva import analizar_curva, curva_de_capital, operaciones_binarias, operaciones_stops
from kronos.research.grafico import curva_svg, leyenda_svg, precio_svg

SERIE = None

def catalogo_json():
    """Canales y operadores, para que la interfaz no los duplique."""
    return json.dumps({
        "canales": {n: {"periodo": c.periodo, "descripcion": c.descripcion,
                        "tipico": list(c.tipico)}
                    for n, c in CANALES.items()},
        "operadores": OPERADORES,
    })

def cargar_csv(texto_csv, nombre="datos"):
    """Escribe el CSV en el sistema de ficheros virtual y lo carga.

    El nombre se pasa aparte porque load_csv deduce el simbolo del nombre del
    fichero, y aqui el fichero real siempre se llama igual.

    Ojo al editar: este bloque vive dentro de una plantilla de JavaScript, asi
    que un acento grave o un dolar-llave aqui dentro la cierran y app.js deja de
    parsearse entero, sin error visible en consola.
    """
    global SERIE
    with open("/tmp/datos.csv", "w", encoding="utf-8") as f:
        f.write(texto_csv)
    simbolo = nombre.rsplit(".", 1)[0].upper()[:16] or "DATOS"
    SERIE = loader.load_csv("/tmp/datos.csv", symbol=simbolo)
    return json.dumps({
        "symbol": SERIE.symbol, "velas": len(SERIE),
        "desde": SERIE[0].ts, "hasta": SERIE[-1].ts,
        "ultimo_precio": SERIE.closes[-1],
    })

def evaluar(config_json):
    """Evalua la estrategia y devuelve veredicto, metricas y graficos."""
    if SERIE is None:
        raise ValueError("no hay datos cargados")
    c = json.loads(config_json)

    est = EstrategiaDeclarativa.desde_dict(
        {"nombre": c["nombre"], "reglas": c["reglas"]})

    vp = 0.0001 if SERIE.closes[-1] < 10.0 else SERIE.closes[-1] * 0.0001
    comun = dict(spread_pips=c["spread"], valor_pip=vp)

    v = evaluar_estrategia(
        SERIE, est, modo=c["modo"], split=0.6, intentos=c["intentos"],
        expiry=c.get("expiry", 5), payout=c.get("payout", 0.84),
        rr=c.get("rr", 2.0), atr_mult=c.get("atr_mult", 1.5),
        max_velas=c.get("max_velas", 48), **comun)

    sen = est.senales(SERIE)
    if c["modo"] == "binarias":
        ops = operaciones_binarias(SERIE, sen, expiry=c.get("expiry", 5),
                                   payout=c.get("payout", 0.84), **comun)
    else:
        ops = operaciones_stops(SERIE, sen, rr=c.get("rr", 2.0),
                                atr_mult=c.get("atr_mult", 1.5),
                                max_velas=c.get("max_velas", 48), **comun)

    salida = {
        "superviviente": v.superviviente,
        "dictamen": v.dictamen(),
        "winrate": v.dentro.winrate, "umbral": v.dentro.umbral,
        "edge": v.dentro.edge, "n": v.dentro.n,
        "suficiente": v.dentro.suficiente, "minimo": MIN_OPERACIONES,
        "p_corregido": v.p_corregido, "intentos": v.intentos,
        "motivos": v.motivos(), "informe": v.informe(),
        "operaciones": len(ops), "graficos": None,
    }

    if ops:
        curva = curva_de_capital(ops)
        r = analizar_curva(curva)
        ancho = min(400, len(SERIE))
        salida["graficos"] = {
            "curva": curva_svg(curva),
            "precio": precio_svg(SERIE.closes, ops, len(SERIE) - ancho, len(SERIE)),
            "leyenda": leyenda_svg(),
        }
        salida["capital"] = {
            "inicial": curva[0], "final": curva[-1],
            "drawdown": r.max_drawdown, "seguidas": r.perdidas_seguidas,
        }
    return json.dumps(salida)

def describir(reglas_json):
    """Texto legible de cada regla, generado por Python para no duplicarlo."""
    fuera = []
    for r in json.loads(reglas_json):
        fuera.append(Regla(tuple(Condicion(**c) for c in r["condiciones"]),
                           r["direccion"]).describir())
    return json.dumps(fuera)

def describir_condicion(cond_json):
    return Condicion(**json.loads(cond_json)).describir()
`);

    const cat = JSON.parse(py.globals.get("catalogo_json")());
    canales = cat.canales;
    operadores = cat.operadores;

    montarCatalogo();
    pintarBiblioteca();
    actualizarUmbral();

    $("#arranque").hidden = true;
    $("#app").hidden = false;
    registrarServiceWorker();
  } catch (e) {
    $("#arranque").innerHTML =
      `<p class="error"><strong>No se pudo arrancar el motor.</strong><br>${escapar(String(e))}
       <br><br>Comprueba la conexión: la primera carga necesita descargar Python.</p>`;
  }
}

const escapar = (s) => s.replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* --------------------------------------------------------------------- *
 * Constructor de reglas
 * --------------------------------------------------------------------- */
function montarCatalogo() {
  const selCanal = $("#canal");
  Object.keys(canales).sort().forEach((n) => {
    selCanal.append(crear("option", { value: n, textContent: n }));
  });
  const selOp = $("#operador");
  Object.entries(operadores).forEach(([k, v]) => {
    selOp.append(crear("option", { value: k, textContent: v }));
  });
  selCanal.addEventListener("change", alCambiarCanal);
  alCambiarCanal();
}

function alCambiarCanal() {
  // El rango tipico cambia mucho entre canales: rsi va de 0 a 100 y percent_b
  // de 0 a 1. Si el valor sugerido no se adapta, el usuario mete umbrales
  // absurdos sin que nada se lo advierta.
  const c = canales[$("#canal").value];
  const [lo, hi] = c.tipico;
  $("#valor").value = ((lo + hi) / 2).toPrecision(3).replace(/\.?0+$/, "");
  $("#valor").step = Math.max(1e-6, (hi - lo) / 100);
  $("#periodo").value = c.periodo;
  $("#ayuda-canal").textContent = `${c.descripcion}. Rango típico: ${lo} a ${hi}.`;
}

async function pintarReglas() {
  const ul = $("#lista-reglas");
  ul.textContent = "";
  const textos = reglas.length
    ? JSON.parse(py.globals.get("describir")(JSON.stringify(reglas)))
    : [];
  textos.forEach((t, i) => {
    const li = crear("li");
    li.append(crear("span", { className: "txt", textContent: t }));
    const btn = crear("button", { textContent: "Quitar", title: "Eliminar regla" });
    btn.addEventListener("click", () => { reglas.splice(i, 1); pintarReglas(); });
    li.append(btn);
    ul.append(li);
  });
  $("#sin-reglas").hidden = reglas.length > 0;
  refrescarEstado();
}

function pintarPendientes() {
  const ul = $("#lista-pendientes");
  ul.textContent = "";
  pendientes.forEach((c, i) => {
    const li = crear("li");
    li.append(crear("span", {
      className: "txt",
      textContent: py.globals.get("describir_condicion")(JSON.stringify(c)),
    }));
    const btn = crear("button", { textContent: "Quitar" });
    btn.addEventListener("click", () => { pendientes.splice(i, 1); pintarPendientes(); });
    li.append(btn);
    ul.append(li);
  });
  $("#pendientes-caja").hidden = pendientes.length === 0;
}

/* --------------------------------------------------------------------- *
 * Estado de la interfaz
 * --------------------------------------------------------------------- */
function refrescarEstado() {
  const listo = hayDatos && reglas.length > 0;
  $("#btn-evaluar").disabled = !listo;
  const falta = [];
  if (!hayDatos) falta.push("cargar datos");
  if (!reglas.length) falta.push("añadir al menos una regla");
  $("#falta").textContent = listo ? "" : "Para evaluar falta: " + falta.join(" y ") + ".";

  const n = intentosDe(claveDatos);
  $("#intentos").textContent = n;
  $("#btn-reiniciar").hidden = n === 0;
  const aviso = $("#aviso-intentos");
  if (n >= 20) {
    aviso.hidden = false;
    aviso.textContent = `Llevas ${n} intentos. El p-valor se multiplica por ${n}: ` +
      "a estas alturas hace falta un edge enorme para ser creíble. " +
      "Considera conseguir datos que no hayas mirado.";
  } else if (n >= 8) {
    aviso.hidden = false;
    aviso.textContent = `${n} intentos. Cada uno encarece el listón de significancia.`;
  } else {
    aviso.hidden = true;
  }
}

function actualizarUmbral() {
  const spread = parseFloat($("#spread").value) || 0;
  if (modo === "binarias") {
    const p = parseFloat($("#payout").value) || 0.84;
    $("#aviso-umbral").textContent =
      `Umbral de equilibrio: ${(100 / (1 + p)).toFixed(2)}%`;
  } else {
    const rr = parseFloat($("#rr").value) || 2;
    $("#aviso-umbral").textContent =
      `Umbral sin coste: ${(100 / (1 + rr)).toFixed(2)}%. ` +
      "El coste lo sube; se calcula al evaluar.";
  }
  const av = $("#aviso-coste");
  if (spread === 0) {
    av.hidden = false;
    av.textContent = "Con coste cero, cualquier resultado positivo es ficticio.";
  } else {
    av.hidden = true;
  }
}

/* --------------------------------------------------------------------- *
 * Datos
 * --------------------------------------------------------------------- */
async function cargarTexto(texto, clave) {
  try {
    const info = JSON.parse(py.globals.get("cargar_csv")(texto, clave));
    hayDatos = true;
    claveDatos = clave;
    const f = (ts) => new Date(ts * 1000).toISOString().slice(0, 16).replace("T", " ");
    $("#info-datos").textContent =
      `${info.symbol} · ${info.velas.toLocaleString("es")} velas · ` +
      `${f(info.desde)} a ${f(info.hasta)} (UTC)`;
    if (info.ultimo_precio >= 10) {
      const av = $("#aviso-coste");
      if ((parseFloat($("#spread").value) || 0) < 10) {
        av.hidden = false;
        av.textContent = "Parece cripto. La comisión típica (0,2% taker) " +
          "equivale a unos 20 pips; con 0,5 estás midiendo un mercado que no existe.";
      }
    }
    refrescarEstado();
  } catch (e) {
    $("#info-datos").textContent = "No se pudo leer el fichero: " + e;
  }
}

/* --------------------------------------------------------------------- *
 * Evaluacion
 * --------------------------------------------------------------------- */
async function evaluar() {
  const btn = $("#btn-evaluar");
  btn.disabled = true;
  btn.textContent = "Midiendo…";

  const intentos = sumarIntento(claveDatos);
  const cfg = {
    nombre: $("#nombre").value || "Sin nombre",
    reglas, modo, intentos,
    spread: parseFloat($("#spread").value) || 0,
    expiry: parseInt($("#expiry").value, 10) || 5,
    payout: parseFloat($("#payout").value) || 0.84,
    rr: parseFloat($("#rr").value) || 2,
    atr_mult: parseFloat($("#atrmult").value) || 1.5,
    max_velas: parseInt($("#maxvelas").value, 10) || 48,
  };

  try {
    const r = JSON.parse(py.globals.get("evaluar")(JSON.stringify(cfg)));
    pintarResultado(r, cfg);
  } catch (e) {
    $("#resultado").hidden = false;
    $("#resultado").innerHTML = `<p class="error">${escapar(String(e))}</p>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Evaluar";
    refrescarEstado();
  }
}

function metrica(titulo, valor, sub) {
  return `<div class="metrica"><dt>${titulo}</dt><dd>${valor}` +
    (sub ? `<span class="sub">${sub}</span>` : "") + "</dd></div>";
}

function pintarResultado(r, cfg) {
  const pct = (x) => (x * 100).toFixed(2) + "%";
  let html = `<div class="veredicto ${r.superviviente ? "si" : "no"}">${r.dictamen}</div>`;

  html += '<dl class="metricas">';
  html += metrica("Winrate", pct(r.winrate), `${pct(r.edge)} vs umbral`);
  html += metrica("Umbral", pct(r.umbral), "de equilibrio");
  html += metrica("p corregido", r.p_corregido.toFixed(4), `x${r.intentos} intentos`);
  html += metrica("Operaciones", r.n.toLocaleString("es"),
    r.suficiente ? "suficientes" : `faltan para ${r.minimo}`);
  html += "</dl>";

  r.motivos.forEach((m) => { html += `<p class="motivo">${escapar(m)}</p>`; });

  if (r.graficos) {
    const c = r.capital;
    html += "<h2>Curva de capital</h2>";
    html += '<p class="apunte tenue">Riesgo fijo del 1% por operación. Sin ' +
      "progresión ni interés compuesto.</p>";
    html += `<div class="grafico">${r.graficos.curva}</div>`;
    html += '<dl class="metricas">';
    html += metrica("Capital final", c.final.toFixed(0),
      ((c.final / c.inicial - 1) * 100).toFixed(2) + "%");
    html += metrica("Peor caída", pct(c.drawdown), "desde máximo");
    html += metrica("Pérdidas seguidas", c.seguidas, "¿aguantarías?");
    html += metrica("Operaciones", r.operaciones.toLocaleString("es"), "en total");
    html += "</dl>";
    html += "<h2>Dónde entró cada operación</h2>";
    html += `<div class="grafico">${r.graficos.precio}${r.graficos.leyenda}</div>`;
  } else {
    html += '<p class="apunte">La estrategia no generó ninguna operación cerrada. ' +
      "Suele significar que las condiciones son demasiado estrictas.</p>";
  }

  html += `<pre>${escapar(r.informe)}</pre>`;
  html += '<button id="btn-guardar" class="secundario ancho">Guardar en la biblioteca</button>';

  const sec = $("#resultado");
  sec.className = "tarjeta";
  sec.hidden = false;
  sec.innerHTML = html;
  sec.scrollIntoView({ behavior: "smooth", block: "start" });

  $("#btn-guardar").addEventListener("click", () => {
    const lib = Guardado.biblioteca();
    const entrada = {
      nombre: cfg.nombre, reglas: cfg.reglas, modo: cfg.modo,
      superviviente: r.superviviente, edge: r.edge,
      fecha: new Date().toISOString().slice(0, 16).replace("T", " "),
    };
    const i = lib.findIndex((x) => x.nombre === entrada.nombre);
    if (i >= 0) lib[i] = entrada; else lib.unshift(entrada);
    Guardado.fijarBiblioteca(lib);
    pintarBiblioteca();
    $("#btn-guardar").textContent = "Guardada";
    $("#btn-guardar").disabled = true;
  });
}

/* --------------------------------------------------------------------- *
 * Biblioteca
 * --------------------------------------------------------------------- */
function pintarBiblioteca() {
  const lib = Guardado.biblioteca();
  const ul = $("#lista-biblioteca");
  ul.textContent = "";
  lib.forEach((e, i) => {
    const li = crear("li");
    const marca = e.superviviente ? "SOBREVIVIO" : "descartada";
    li.append(crear("span", {
      className: "txt",
      textContent: `${e.nombre} — ${e.fecha} · ${marca}`,
    }));
    const cargar = crear("button", { textContent: "Cargar" });
    cargar.addEventListener("click", () => {
      reglas = JSON.parse(JSON.stringify(e.reglas));
      pendientes = [];
      $("#nombre").value = e.nombre;
      pintarReglas();
      pintarPendientes();
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
    const borrar = crear("button", { textContent: "X", title: "Borrar" });
    borrar.addEventListener("click", () => {
      const l = Guardado.biblioteca();
      l.splice(i, 1);
      Guardado.fijarBiblioteca(l);
      pintarBiblioteca();
    });
    li.append(cargar, borrar);
    ul.append(li);
  });
  $("#sin-biblioteca").hidden = lib.length > 0;
}

/* --------------------------------------------------------------------- *
 * Eventos
 * --------------------------------------------------------------------- */
document.addEventListener("DOMContentLoaded", () => {
  $("#btn-demo").addEventListener("click", async () => {
    $("#info-datos").textContent = "Cargando ejemplo…";
    const t = await (await fetch("demo.csv")).text();
    cargarTexto(t, "demo");
  });

  $("#fichero").addEventListener("change", async (ev) => {
    const f = ev.target.files[0];
    if (f) cargarTexto(await f.text(), f.name);
  });

  document.querySelectorAll(".segmentado button").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelectorAll(".segmentado button").forEach((x) => {
        x.classList.remove("activo");
        x.setAttribute("aria-checked", "false");
      });
      b.classList.add("activo");
      b.setAttribute("aria-checked", "true");
      modo = b.dataset.modo;
      $("#campos-binarias").hidden = modo !== "binarias";
      $("#campos-stops").hidden = modo !== "stops";
      actualizarUmbral();
    });
  });

  ["#payout", "#rr", "#spread"].forEach((s) =>
    $(s).addEventListener("input", actualizarUmbral));

  $("#btn-condicion").addEventListener("click", () => {
    const canal = $("#canal").value;
    const cond = {
      canal,
      operador: $("#operador").value,
      valor: parseFloat($("#valor").value),
    };
    if (!Number.isFinite(cond.valor)) return;
    const per = parseInt($("#periodo").value, 10);
    if (per && per !== canales[canal].periodo) cond.periodo = per;
    pendientes.push(cond);
    pintarPendientes();
  });

  $("#btn-call").addEventListener("click", () => crearRegla(1));
  $("#btn-put").addEventListener("click", () => crearRegla(-1));

  $("#btn-reiniciar").addEventListener("click", () => {
    const o = Guardado.intentos();
    o[claveDatos] = 0;
    Guardado.fijarIntentos(o);
    refrescarEstado();
  });

  $("#btn-evaluar").addEventListener("click", evaluar);

  arrancar();
});

function crearRegla(direccion) {
  if (!pendientes.length) return;
  reglas.push({ condiciones: pendientes.slice(), direccion });
  pendientes = [];
  pintarPendientes();
  pintarReglas();
}

function registrarServiceWorker() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => {
      /* Sin service worker la app sigue funcionando; solo pierde el modo
         sin conexion. No merece molestar al usuario con un error. */
    });
  }
}
