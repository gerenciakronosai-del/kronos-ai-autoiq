"""Kronos Studio: definir una estrategia sin escribir codigo y medirla en serio.

El panel de `app.py` monitoriza el bot en marcha. Este es otra cosa: un banco de
pruebas donde se construye una estrategia con reglas, se lanza contra datos
reales y se recibe un veredicto que aplica los cinco filtros del proyecto.

La decision de diseno que lo separa de cualquier otro backtester con interfaz:
**el contador de intentos es visible y penaliza**. Cada evaluacion contra el
mismo conjunto de datos aumenta la correccion de Bonferroni. Probar cuarenta
variantes hasta que una salga bonita es exactamente lo que la herramienta esta
disenyada para impedir, y por eso el coste de hacerlo se ensenya en pantalla.

    streamlit run dashboard/estudio.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import streamlit as st

from kronos.core.candle import Series
from kronos.data import loader
from kronos.research.reglas import (
    CANALES,
    OPERADORES,
    Condicion,
    EstrategiaDeclarativa,
    Regla,
)
from kronos.research.veredicto import MIN_OPERACIONES, evaluar_estrategia

st.set_page_config(page_title="Kronos Studio", page_icon="K", layout="wide",
                   initial_sidebar_state="expanded")

# --------------------------------------------------------------------- #
# Estado
# --------------------------------------------------------------------- #
ST = st.session_state
ST.setdefault("reglas", [])          # list[dict]: {condiciones: [...], direccion: int}
ST.setdefault("pendientes", [])      # condiciones de la regla que se esta montando
ST.setdefault("intentos", {})        # clave de datos -> nº de evaluaciones
ST.setdefault("serie", None)
ST.setdefault("clave_datos", "")
ST.setdefault("historial", [])       # list[tuple[str, bool, float]]


def _fecha(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M")


def clave_actual() -> str:
    return ST.clave_datos or "(sin datos)"


def intentos_actuales() -> int:
    return ST.intentos.get(clave_actual(), 0)


# --------------------------------------------------------------------- #
# Barra lateral: datos
# --------------------------------------------------------------------- #
with st.sidebar:
    st.header("1. Datos")

    csvs = sorted(p.name for p in (RAIZ / "data").glob("*.csv"))
    origen = st.radio("Origen", ["Fichero local", "Descargar de Binance"],
                      label_visibility="collapsed")

    if origen == "Fichero local":
        if not csvs:
            st.info("No hay CSV en `data/`. Genera uno con "
                    "`python -m kronos datos --out data/prueba.csv` "
                    "o descarga cripto aqui al lado.")
        else:
            elegido = st.selectbox("Fichero", csvs)
            if st.button("Cargar", use_container_width=True):
                try:
                    ST.serie = loader.load_csv(RAIZ / "data" / elegido)
                    ST.clave_datos = elegido
                except Exception as e:
                    st.error(f"{type(e).__name__}: {e}")
    else:
        simbolo = st.text_input("Simbolo", "ETHUSDT")
        tf = st.selectbox("Velas", [3600, 14400, 86400, 604800],
                          format_func=lambda s: {3600: "1 hora", 14400: "4 horas",
                                                 86400: "diarias", 604800: "semanales"}[s],
                          index=2)
        cuantas = st.number_input("Cuantas", 500, 20000, 3000, step=500)
        if st.button("Descargar", use_container_width=True):
            from kronos.data import binance
            barra = st.progress(0.0, "Descargando...")
            try:
                ST.serie = binance.descargar(
                    simbolo, tf, total=int(cuantas),
                    progreso=lambda hechas, total: barra.progress(
                        min(1.0, hechas / total), f"{hechas:,} / {total:,} velas"))
                ST.clave_datos = f"{simbolo}-{tf}-{cuantas}"
                barra.empty()
            except Exception as e:
                barra.empty()
                st.error(f"{type(e).__name__}: {e}")

    if ST.serie is not None:
        s: Series = ST.serie
        st.success(f"**{s.symbol}** · {len(s):,} velas")
        st.caption(f"{_fecha(s[0].ts)} .. {_fecha(s[-1].ts)} (UTC)")

    st.divider()
    st.header("2. Como se evalua")
    modo = st.radio("Modo", ["binarias", "stops"],
                    format_func=lambda m: {"binarias": "Opciones binarias",
                                           "stops": "Stop y objetivo"}[m])
    if modo == "binarias":
        payout = st.slider("Payout del broker", 0.50, 0.95, 0.84, 0.01)
        expiry = st.number_input("Vencimiento (velas)", 1, 100, 5)
        rr = atr_mult = max_velas = None
        st.caption(f"Umbral de equilibrio: **{100 / (1 + payout):.2f}%**")
    else:
        rr = st.slider("Objetivo : riesgo", 0.5, 5.0, 2.0, 0.1)
        atr_mult = st.slider("Stop (multiplos de ATR)", 0.5, 5.0, 1.5, 0.1)
        max_velas = st.number_input("Horizonte maximo (velas)", 5, 500, 48)
        payout = expiry = None
        st.caption(f"Umbral sin coste: **{100 / (1 + rr):.2f}%** "
                   "(el coste lo sube; se calcula al evaluar)")

    spread = st.number_input("Coste por operacion (pips)", 0.0, 100.0, 0.5, 0.1,
                             help="Nunca lo pongas a cero. Evaluar sin coste "
                                  "fabrica ganadores que no existen.")
    if spread == 0:
        st.warning("Con coste cero, cualquier resultado positivo es ficticio.")
    split = st.slider("Reparto dentro / fuera de muestra", 0.3, 0.8, 0.6, 0.05)


# --------------------------------------------------------------------- #
# Cuerpo: constructor de reglas
# --------------------------------------------------------------------- #
st.title("Kronos Studio")
st.caption("Define una estrategia con reglas y comprueba si tiene ventaja real.")

izq, der = st.columns([3, 2])

with izq:
    st.subheader("3. Reglas")

    if not ST.reglas:
        st.info("Sin reglas todavia. Anyade la primera abajo.")

    for idx, r in enumerate(list(ST.reglas)):
        try:
            regla = Regla(tuple(Condicion(**c) for c in r["condiciones"]), r["direccion"])
            texto = regla.describir()
        except ValueError as e:
            texto = f"(regla invalida: {e})"
        c1, c2 = st.columns([9, 1])
        c1.code(texto, language=None)
        if c2.button("X", key=f"del{idx}", help="Eliminar"):
            ST.reglas.pop(idx)
            st.rerun()

    st.markdown("**Nueva regla**")

    # Sin `st.form` a proposito: al cambiar de canal, Streamlit re-ejecuta y el
    # rango sugerido de `valor` se adapta. Dentro de un formulario el rango se
    # quedaria congelado en el del canal anterior, que con percent_b (0 a 1)
    # frente a rsi (0 a 100) es una invitacion a meter un umbral absurdo.
    f1, f2, f3 = st.columns([3, 2, 2])
    canal = f1.selectbox("Canal", sorted(CANALES), key="sel_canal",
                         format_func=lambda c: f"{c} - {CANALES[c].descripcion}")
    operador = f2.selectbox("Operador", list(OPERADORES), key="sel_op",
                            format_func=lambda o: OPERADORES[o])
    lo, hi = CANALES[canal].tipico
    paso = max(1e-6, (hi - lo) / 100)
    valor = f3.number_input("Valor", value=float((lo + hi) / 2), step=float(paso),
                            format="%g", key=f"val_{canal}",
                            help=f"Rango tipico de {canal}: {lo:g} a {hi:g}")
    periodo = st.number_input("Periodo del indicador", 1, 500,
                              CANALES[canal].periodo, key=f"per_{canal}",
                              help="Deja el valor por defecto salvo que sepas "
                                   "por que lo cambias. Cada periodo distinto "
                                   "que pruebas es otro intento mas.")

    if st.button("Anyadir condicion", use_container_width=True):
        cond = {"canal": canal, "operador": operador, "valor": float(valor)}
        if int(periodo) != CANALES[canal].periodo:
            cond["periodo"] = int(periodo)
        ST.pendientes.append(cond)
        st.rerun()

    if ST.pendientes:
        st.markdown("**Condiciones de la regla en curso** (se cumplen a la vez)")
        for j, c in enumerate(list(ST.pendientes)):
            k1, k2 = st.columns([9, 1])
            k1.code(Condicion(**c).describir(), language=None)
            if k2.button("X", key=f"delc{j}"):
                ST.pendientes.pop(j)
                st.rerun()

        d1, d2 = st.columns(2)
        if d1.button("Crear regla -> CALL", use_container_width=True):
            ST.reglas.append({"condiciones": list(ST.pendientes), "direccion": 1})
            ST.pendientes = []
            st.rerun()
        if d2.button("Crear regla -> PUT", use_container_width=True):
            ST.reglas.append({"condiciones": list(ST.pendientes), "direccion": -1})
            ST.pendientes = []
            st.rerun()

    st.caption("Cada regla se evalua por separado. Si dos apuntan en sentidos "
               "opuestos en la misma vela, esa vela no opera.")

with der:
    st.subheader("Intentos sobre estos datos")
    n_int = intentos_actuales()
    st.metric("Evaluaciones", n_int,
              help="Cada evaluacion contra los mismos datos multiplica el "
                   "p-valor exigido. Es la correccion de Bonferroni.")
    if n_int >= 20:
        st.error(f"Llevas {n_int} intentos. El p-valor se multiplica por {n_int}: "
                 "a estas alturas hace falta un edge enorme para ser creible. "
                 "Considera conseguir datos nuevos.")
    elif n_int >= 8:
        st.warning(f"{n_int} intentos. Cada uno encarece el liston de significancia.")
    if n_int and st.button("Reiniciar contador"):
        st.caption("Solo si vas a usar datos que no has mirado antes.")
        ST.intentos[clave_actual()] = 0
        st.rerun()

    if ST.reglas:
        est_previa = {"nombre": "estrategia", "reglas": ST.reglas}
        st.download_button("Exportar estrategia (JSON)",
                           data=EstrategiaDeclarativa.desde_dict(est_previa).a_json(),
                           file_name="estrategia.json", mime="application/json",
                           use_container_width=True)

    subido = st.file_uploader("Importar estrategia", type="json")
    if subido is not None:
        try:
            est = EstrategiaDeclarativa.desde_json(subido.getvalue().decode("utf-8"))
            ST.reglas = [r.a_dict() for r in est.reglas]
            st.success(f"Cargada: {est.nombre}")
        except ValueError as e:
            st.error(str(e))


# --------------------------------------------------------------------- #
# Evaluacion
# --------------------------------------------------------------------- #
st.divider()
nombre = st.text_input("Nombre de la estrategia", "Sin nombre")
listo = ST.serie is not None and bool(ST.reglas)

if st.button("Evaluar", type="primary", disabled=not listo, use_container_width=True):
    ST.intentos[clave_actual()] = intentos_actuales() + 1
    try:
        est = EstrategiaDeclarativa.desde_dict({"nombre": nombre, "reglas": ST.reglas})
        v = evaluar_estrategia(
            ST.serie, est, modo=modo, split=split, intentos=intentos_actuales(),
            expiry=int(expiry) if expiry else 5,
            payout=float(payout) if payout else 0.84,
            rr=float(rr) if rr else 2.0,
            atr_mult=float(atr_mult) if atr_mult else 1.5,
            max_velas=int(max_velas) if max_velas else 48,
            spread_pips=float(spread),
        )
        ST.historial.append((nombre, v.superviviente, v.dentro.edge))

        if v.superviviente:
            st.success("SOBREVIVE a los cinco filtros")
        else:
            st.error("NO SOBREVIVE")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Winrate (dentro)", f"{v.dentro.winrate * 100:.2f}%",
                  f"{v.dentro.edge * 100:+.2f}% vs umbral")
        m2.metric("Umbral de equilibrio", f"{v.dentro.umbral * 100:.2f}%")
        m3.metric("p corregido", f"{v.p_corregido:.4f}",
                  f"x{v.intentos} intentos", delta_color="off")
        m4.metric("Operaciones", f"{v.dentro.n:,}",
                  "suficientes" if v.dentro.suficiente else f"faltan para {MIN_OPERACIONES}",
                  delta_color="normal" if v.dentro.suficiente else "inverse")

        for motivo in v.motivos():
            st.warning(motivo)

        st.code(v.informe(), language=None)

    except Exception as e:
        st.error(f"{type(e).__name__}: {e}")

elif not listo:
    falta = []
    if ST.serie is None:
        falta.append("cargar datos")
    if not ST.reglas:
        falta.append("anyadir al menos una regla")
    st.info("Para evaluar falta: " + " y ".join(falta) + ".")

if ST.historial:
    st.divider()
    st.subheader("Historial de la sesion")
    st.caption("Todo lo que has probado contra estos datos. Esta lista ES la "
               "correccion por multiples comparaciones.")
    for i, (nom, sobrevive, edge) in enumerate(reversed(ST.historial), 1):
        marca = "SOBREVIVE" if sobrevive else "descartada"
        st.text(f"{len(ST.historial) - i + 1:>3}. {nom:<30} {edge * 100:+6.2f}%  {marca}")
