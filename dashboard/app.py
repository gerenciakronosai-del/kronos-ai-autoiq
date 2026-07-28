"""Panel de control en tiempo real de Kronos.

    streamlit run dashboard/app.py

El panel NO decide nada: solo lee instantaneas del motor, que corre en un hilo
propio. Puedes cerrarlo y volver a abrirlo sin afectar al bot mientras la sesion
siga viva.

La metrica principal no es el winrate. Es el winrate CONTRA el umbral de
equilibrio (1 / (1 + payout)), porque acertar por debajo de ese umbral pierde
dinero de forma sostenida. El panel muestra siempre los dos numeros juntos.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Permite ejecutar el panel sin instalar el paquete.
RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import streamlit as st  # noqa: E402

from kronos.ia.cerebro import CerebroIA, CerebroNoDisponible  # noqa: E402
from kronos.ia.coste import estimar_coste_diario  # noqa: E402
from kronos.live.feed import FeedReplay, FeedSintetico  # noqa: E402
from kronos.live.motor import ConfigMotor, MotorEnVivo  # noqa: E402
from kronos.risk.manager import RiskParams  # noqa: E402
from kronos.strategy.base import Confidence  # noqa: E402

st.set_page_config(page_title="Kronos AI - AutoIQ", page_icon="K", layout="wide",
                   initial_sidebar_state="expanded")

COLOR = {"CALL": "#16a34a", "PUT": "#dc2626", "ESPERAR": "#64748b", "-": "#64748b"}


def _rerun() -> None:
    """Compatibilidad entre versiones de Streamlit."""
    fn = getattr(st, "rerun", None) or getattr(st, "experimental_rerun", None)
    if fn:
        fn()


def tarjeta(titulo: str, decision: str, confianza: str, razon: str,
            extra: str = "") -> None:
    color = COLOR.get(decision, "#64748b")
    st.markdown(
        f"""
        <div style="border:1px solid rgba(128,128,128,.35);border-left:6px solid {color};
                    border-radius:10px;padding:14px 16px;margin-bottom:6px;">
          <div style="font-size:12px;letter-spacing:.08em;opacity:.65;
                      text-transform:uppercase;">{titulo}</div>
          <div style="font-size:34px;font-weight:700;color:{color};line-height:1.15;">
            {decision}
          </div>
          <div style="font-size:13px;opacity:.75;margin-bottom:6px;">
            Confianza: <b>{confianza}</b>{extra}
          </div>
          <div style="font-size:13px;line-height:1.45;">{razon or "&mdash;"}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------- #
# Configuracion
# --------------------------------------------------------------------- #
st.title("Kronos AI — AutoIQ")
st.caption("Panel de control del motor de decision. Ejecucion sobre broker simulado.")

with st.sidebar:
    st.header("Configuracion")
    motor: MotorEnVivo | None = st.session_state.get("motor")
    bloqueado = motor is not None and motor.corriendo
    if bloqueado:
        st.info("Motor en marcha. Detenlo para cambiar la configuracion.")

    st.subheader("Datos de mercado")
    origen = st.radio("Origen", ["Replay de CSV real", "Sintetico"],
                      disabled=bloqueado, help="El replay usa precios que ocurrieron de verdad.")
    csvs = sorted(str(p) for p in (RAIZ / "data").glob("*.csv")) if (RAIZ / "data").exists() else []
    ruta_csv = ""
    if origen.startswith("Replay"):
        if csvs:
            ruta_csv = st.selectbox("Fichero", csvs, disabled=bloqueado)
        else:
            ruta_csv = st.text_input("Ruta al CSV", "data/eurusd.csv", disabled=bloqueado)
    velocidad = st.slider("Velocidad de simulacion (x tiempo real)", 1, 600, 60,
                          disabled=bloqueado,
                          help="60x = una vela de 1 minuto por segundo.")

    st.subheader("Ciclo")
    intervalo = st.slider("Intervalo del bucle (s)", 1.0, 30.0, 5.0, 0.5, disabled=bloqueado)
    solo_cierre = st.checkbox(
        "Consultar la IA solo al cierre de vela", value=True, disabled=bloqueado,
        help="Recomendado. Sin esto se consulta cada ciclo aunque la vela no haya "
             "cambiado: misma informacion, coste multiplicado.",
    )
    expiry = st.slider("Vencimiento (velas)", 1, 15, 5, disabled=bloqueado)
    payout = st.slider("Payout del broker", 0.50, 0.95, 0.80, 0.01, disabled=bloqueado)
    spread = st.slider(
        "Spread (pips)", 0.0, 3.0, 0.5, 0.1, disabled=bloqueado,
        help="Coste de entrada. A cero el panel da resultados optimistas: sobre "
             "744k velas reales, 0.2 pips bastaban para borrar toda la ventaja "
             "medible en horizontes de 1-10 minutos.",
    )

    st.subheader("Cerebros")
    usar_ia = st.checkbox("Cerebro IA (API de Anthropic)", value=True, disabled=bloqueado)
    usar_local = st.checkbox("Cerebro local (reglas deterministas)", value=True,
                             disabled=bloqueado)
    ejecutor = st.radio("Cual ejecuta las ordenes", ["ia", "local"], disabled=bloqueado,
                        horizontal=True)
    modelo = st.selectbox("Modelo", ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"],
                          disabled=bloqueado)
    effort = st.select_slider("Effort", ["low", "medium", "high"], value="low",
                              disabled=bloqueado,
                              help="Mas effort = mas razonamiento, mas latencia y mas coste.")

    st.subheader("Riesgo")
    balance = st.number_input("Balance inicial", 100.0, 1_000_000.0, 1000.0, 100.0,
                              disabled=bloqueado)
    riesgo_op = st.slider("Riesgo por operacion", 0.005, 0.05, 0.01, 0.005, disabled=bloqueado)
    max_ops = st.slider("Maximo de operaciones al dia", 1, 200, 20, disabled=bloqueado)
    max_perdidas = st.slider("Perdidas seguidas antes de enfriar", 1, 10, 3, disabled=bloqueado)
    conf_min = st.select_slider("Confianza minima para operar", ["BAJA", "MEDIA", "ALTA"],
                                value="MEDIA", disabled=bloqueado)

    st.divider()
    seg_entre_llamadas = 60.0 if solo_cierre else intervalo
    proyeccion = estimar_coste_diario(seg_entre_llamadas, modelo)
    st.metric("Coste estimado / dia", f"${proyeccion:,.2f}",
              help="Estimacion a priori con el prompt cacheado. El panel mide el real una vez arranque.")
    if not solo_cierre:
        st.warning(
            f"Sin la compuerta de cierre de vela consultaras la API cada "
            f"{intervalo:.0f} s: unas {86400 / intervalo:,.0f} llamadas al dia."
        )

    col_a, col_b = st.columns(2)
    arrancar = col_a.button("Iniciar", type="primary", disabled=bloqueado,
                            use_container_width=True)
    parar = col_b.button("Detener", disabled=not bloqueado, use_container_width=True)

    st.divider()
    auto = st.checkbox("Auto-refrescar", value=True)
    refresco = st.slider("Refresco (s)", 1, 15, 3)


# --------------------------------------------------------------------- #
# Arranque / parada
# --------------------------------------------------------------------- #
if arrancar:
    try:
        if origen.startswith("Replay"):
            feed = FeedReplay(ruta_csv, velocidad=float(velocidad))
        else:
            feed = FeedSintetico(velocidad=float(velocidad))

        cerebro = None
        st.session_state["aviso_ia"] = None
        if usar_ia:
            try:
                cerebro = CerebroIA(modelo=modelo, effort=effort)
                ok, detalle = cerebro.prueba_de_conexion()
            except CerebroNoDisponible as e:
                ok, detalle = False, str(e)
            if not ok:
                # El aviso va a session_state, no a un st.error suelto: el
                # rerun posterior borraria el mensaje y el cerebro IA quedaria
                # apagado sin que nada lo explicara en el panel.
                st.session_state["aviso_ia"] = detalle
                cerebro = None
                usar_ia = False

        nuevo = MotorEnVivo(
            feed=feed,
            config=ConfigMotor(
                intervalo_seg=float(intervalo), solo_en_cierre_de_vela=solo_cierre,
                expiry_velas=int(expiry), payout=float(payout), spread_pips=float(spread),
                cerebro_ejecutor=ejecutor, usar_ia=usar_ia, usar_local=usar_local,
            ),
            riesgo=RiskParams(
                balance_inicial=float(balance), riesgo_por_operacion=float(riesgo_op),
                max_operaciones_dia=int(max_ops), max_perdidas_seguidas=int(max_perdidas),
                confianza_minima=Confidence(conf_min),
            ),
            cerebro=cerebro,
        )
        nuevo.iniciar()
        st.session_state["motor"] = nuevo
        _rerun()
    except CerebroNoDisponible as e:
        st.sidebar.error(str(e))
    except Exception as e:
        st.sidebar.error(f"{type(e).__name__}: {e}")

if parar and st.session_state.get("motor"):
    st.session_state["motor"].detener()
    _rerun()


# --------------------------------------------------------------------- #
# Panel
# --------------------------------------------------------------------- #
motor: MotorEnVivo | None = st.session_state.get("motor")
if motor is None:
    st.info("Configura el motor en la barra lateral y pulsa **Iniciar**.")
    st.markdown(
        """
        ### Que hace este panel

        Cada ciclo, el motor pregunta a **dos cerebros** sobre los mismos datos:

        | | Cerebro IA | Cerebro local |
        |---|---|---|
        | Motor | API de Anthropic | Reglas de confluencia |
        | Determinista | No | Si |
        | Coste por decision | Por token | Cero |
        | Latencia | Segundos | Microsegundos |
        | Backtesteable | No en la practica | Si |

        Solo uno ejecuta las ordenes, pero se registran los dos. **La tasa de
        acuerdo es el dato clave**: si la IA coincide casi siempre con las
        reglas, estas pagando por replicarlas; si difiere mucho, hay que mirar
        cual de las dos acierta mas antes de fiarse de ninguna.

        Todas las ordenes van contra un **broker simulado**. No se mueve dinero real.
        """
    )
    st.stop()

s = motor.snapshot()

aviso_ia = st.session_state.get("aviso_ia") or s["error_cerebro"]
if aviso_ia:
    st.warning(
        f"**Cerebro IA desactivado** — {aviso_ia}\n\n"
        "El motor sigue funcionando solo con el cerebro local. Para activar la IA, "
        "define `ANTHROPIC_API_KEY` en el entorno y reinicia el panel."
    )
if s["kill_switch"]:
    st.error(f"KILL SWITCH ACTIVADO — {s['motivo_kill']}. El motor no abrira mas posiciones.")
if s["ultimo_error"]:
    st.warning(f"Ultimo error: {s['ultimo_error']}")

estado = "EN MARCHA" if s["corriendo"] else "DETENIDO"
st.markdown(f"**{estado}** · {s['feed']} · precio actual `{s['precio']:.5f}`"
            if s["precio"] else f"**{estado}** · {s['feed']}")

# -- KPIs -------------------------------------------------------------- #
st_ = s["stats"]
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Balance", f"{s['balance']:,.2f}", f"{s['pnl']:+,.2f}")
umbral = s["umbral_equilibrio"]
delta_wr = (st_.winrate - umbral) * 100 if st_.decisivas else 0.0
c2.metric("Winrate", f"{st_.winrate * 100:.1f}%" if st_.decisivas else "—",
          f"{delta_wr:+.1f} pp vs umbral" if st_.decisivas else f"umbral {umbral * 100:.1f}%")
c3.metric("Operaciones", f"{st_.ordenes}",
          f"{st_.ganadas}G / {st_.perdidas}P / {st_.empates}E")
c4.metric("Coste IA (sesion)", f"${s['coste_total']:.4f}",
          f"~${s['coste_diario_proyectado']:,.2f}/dia")
c5.metric("Latencia IA", f"{s['latencia_media']:,.0f} ms",
          f"p95 {s['latencia_p95']:,.0f} ms")
c6.metric("Acuerdo IA/local", f"{st_.tasa_acuerdo * 100:.0f}%"
          if (st_.acuerdos + st_.desacuerdos) else "—",
          f"{st_.acuerdos} de {st_.acuerdos + st_.desacuerdos}")

if st_.decisivas and st_.winrate < umbral:
    st.error(
        f"El winrate ({st_.winrate * 100:.1f}%) esta por debajo del umbral de "
        f"equilibrio ({umbral * 100:.1f}%) que exige un payout del {payout * 100:.0f}%. "
        "Con estos numeros el sistema pierde dinero por diseno."
    )
if st_.decisivas and st_.decisivas < 30:
    st.info(f"Solo {st_.decisivas} operaciones decisivas: por debajo de 30 el "
            "winrate es ruido estadistico, no una senal.")

# -- Graficos ------------------------------------------------------------ #
g1, g2 = st.columns([3, 2])
with g1:
    st.caption(f"Precio de {s['symbol']} — ultimas {len(s['precios'])} velas")
    if s["precios"]:
        st.line_chart(
            {"precio": [p for _, p in s["precios"]]},
            height=240, use_container_width=True,
        )
    else:
        st.info("Esperando velas...")
with g2:
    st.caption("Curva de capital")
    if len(s["equity"]) > 1:
        st.line_chart(
            {"balance": [b for _, b in s["equity"]]},
            height=240, use_container_width=True,
        )
        pico = max(b for _, b in s["equity"])
        dd = (pico - s["balance"]) / pico if pico else 0.0
        st.caption(f"Drawdown actual: {dd * 100:.2f}% (pico {pico:,.2f}) · "
                   f"spread {s['spread_pips']:.1f} pips")
    else:
        st.info(f"Sin operaciones cerradas todavia. Spread configurado: "
                f"{s['spread_pips']:.1f} pips.")

# -- Ultimas decisiones -------------------------------------------------- #
st.subheader("Ultima decision")
ultimo = s["historial"][-1] if s["historial"] else None
col_ia, col_local = st.columns(2)
with col_ia:
    if ultimo:
        extra = f" · {ultimo.ia_latencia_ms:,.0f} ms · ${ultimo.ia_coste_usd:.5f}"
        razon = ultimo.ia_razon if not ultimo.ia_error else f"⚠ {ultimo.ia_error}"
        tarjeta("Cerebro IA", ultimo.ia_decision, ultimo.ia_confianza, razon, extra)
    else:
        tarjeta("Cerebro IA", "-", "-", "Esperando la primera consulta...")
with col_local:
    if ultimo:
        tarjeta("Cerebro local", ultimo.local_decision, ultimo.local_confianza,
                ultimo.local_razon)
    else:
        tarjeta("Cerebro local", "-", "-", "Esperando la primera consulta...")

if ultimo and ultimo.veto:
    st.caption(f"Gestion de riesgo bloqueo la ejecucion: **{ultimo.veto}**")
elif ultimo and ultimo.ejecutada:
    st.caption(f"Orden **{ultimo.orden_id}** abierta con stake {ultimo.stake:.2f}")

# -- Historial ----------------------------------------------------------- #
st.subheader("Historial de decisiones")
if s["historial"]:
    filas = [
        {
            "#": c.n, "Hora (UTC)": c.hora, "Precio": round(c.precio, 5),
            "IA": c.ia_decision, "Conf. IA": c.ia_confianza,
            "Local": c.local_decision, "Conf. local": c.local_confianza,
            "Acuerdo": "-" if c.acuerdo is None else ("si" if c.acuerdo else "NO"),
            "Ejecutada": "si" if c.ejecutada else "",
            "Veto": c.veto, "ms": c.ia_latencia_ms, "$": c.ia_coste_usd,
            "Razon IA": c.ia_razon,
        }
        for c in reversed(s["historial"])
    ]
    st.dataframe(filas, use_container_width=True, hide_index=True, height=330)
else:
    st.caption("Sin decisiones todavia. Con la compuerta de cierre de vela activa, "
               "la primera llega cuando cierre la siguiente vela.")

# -- Diagnostico --------------------------------------------------------- #
with st.expander("Diagnostico del motor"):
    d1, d2, d3 = st.columns(3)
    d1.write("**Ciclo**")
    d1.write(f"- Ticks: {st_.ticks}")
    d1.write(f"- Consultas a los cerebros: {st_.consultas}")
    d1.write(f"- Ticks retrasados: {st_.ticks_retrasados}")
    d1.write(f"- Posiciones abiertas: {s['posiciones_abiertas']}")

    d2.write("**API**")
    d2.write(f"- Llamadas: {s['llamadas_ia']}")
    d2.write(f"- Errores: {s['errores_ia']}")
    d2.write(f"- Coste medio: ${s['coste_medio']:.5f}")
    d2.write(f"- Tokens servidos desde cache: {s['tasa_cache'] * 100:.0f}%")

    d3.write("**Riesgo**")
    d3.write(f"- Operaciones hoy: {s['operaciones_hoy']}")
    d3.write(f"- Perdidas seguidas: {s['perdidas_seguidas']}")
    d3.write(f"- Kill switch: {'SI' if s['kill_switch'] else 'no'}")
    if st_.vetos:
        d3.write("- Vetos: " + ", ".join(f"{k} ({v})" for k, v in st_.vetos.items()))

    if st_.ticks_retrasados > st_.consultas * 0.2 and st_.consultas:
        st.warning(
            "Muchos ciclos tardan mas que el intervalo configurado: la latencia de "
            "la API no cabe en el ciclo. Sube el intervalo o baja el effort."
        )

st.caption(
    f"Todas las ordenes se ejecutan contra un broker simulado, con un spread de "
    f"{s['spread_pips']:.1f} pips aplicado en contra en cada entrada. Aun asi, los "
    "resultados no predicen los de una cuenta real: no se modelan requotes, "
    "ampliacion del spread en noticias, huecos de fin de semana ni payouts variables."
)

if auto and s["corriendo"]:
    time.sleep(refresco)
    _rerun()
