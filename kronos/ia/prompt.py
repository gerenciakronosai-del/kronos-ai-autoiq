"""Prompt de sistema y contrato de salida para el cerebro IA.

Dos decisiones de diseno importantes:

1. El prompt de sistema es ESTABLE byte a byte entre llamadas. Eso permite
   cachearlo (`cache_control`) y pagar ~10% por su relectura en vez del precio
   completo. Todo lo volatil (precios, indicadores, hora) va en el mensaje de
   usuario, nunca aqui: un solo caracter que cambie invalida la cache entera.

2. La salida se fuerza con `output_config.format` (structured outputs), no
   pidiendo "devuelve JSON y nada mas". El esquema lo garantiza la API: no hay
   texto de relleno, ni bloques markdown, ni claves inventadas que un parser
   tenga que limpiar.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from kronos.core import indicators as ind
from kronos.core.candle import Series

# --------------------------------------------------------------------- #
# Contrato de salida (lo impone la API, no el prompt)
# --------------------------------------------------------------------- #
ESQUEMA_DECISION: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["CALL", "PUT", "ESPERAR"],
            "description": "Direccion de la operacion, o ESPERAR si no hay setup claro.",
        },
        "confianza": {
            "type": "string",
            "enum": ["ALTA", "MEDIA", "BAJA"],
            "description": "Nivel de conviccion en la decision.",
        },
        "razon": {
            "type": "string",
            "description": "Explicacion tecnica de UNA sola linea, citando indicadores concretos.",
        },
    },
    "required": ["decision", "confianza", "razon"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------- #
# Prompt de sistema: estable, cacheable
# --------------------------------------------------------------------- #
SISTEMA = """\
Eres un motor de analisis tecnico cuantitativo para opciones binarias de corto \
plazo (vencimientos de 1 a 5 minutos). Recibes una fotografia del mercado y \
devuelves una unica decision de ejecucion.

## Tu objetivo real

NO es operar. Es filtrar. En opciones binarias con un payout del 80% hace falta \
acertar el 55.6% de las veces solo para no perder dinero:

    umbral de equilibrio = 1 / (1 + payout)

Acertar el 54% pierde capital de forma sostenida. Por eso una decision ESPERAR \
correcta vale mas que una operacion mediocre: no operar tiene esperanza cero, \
mientras que operar sin ventaja tiene esperanza negativa. Ante cualquier duda \
razonable, responde ESPERAR. Se espera que la mayoria de tus respuestas lo sean.

## Reglas de decision, en orden estricto

Los filtros vetan. Un veto no se compensa con confluencia: si un filtro se \
dispara, la respuesta es ESPERAR y no sigues evaluando.

1. VOLATILIDAD. Si el ATR como porcentaje del precio esta por debajo del minimo \
   indicado en los datos, el mercado esta muerto: el precio no se movera lo \
   suficiente antes del vencimiento. ESPERAR.
2. VOLATILIDAD ANOMALA. Si el ATR% supera el maximo indicado, o la ultima vela \
   tiene un rango superior a 3 veces el ATR, hay un spike de noticia. El \
   comportamiento tras un spike es impredecible. ESPERAR.
3. LATERALIDAD. Si el ADX esta por debajo de 20 y las Bandas de Bollinger estan \
   comprimidas, el precio oscila sin direccion en un rango estrecho. ESPERAR.
4. REGIMEN. Con ADX >= 25 el mercado tiene tendencia y se opera A FAVOR de ella, \
   entrando al final de un retroceso. Con ADX < 25 se busca agotamiento en los \
   extremos del canal de Bollinger.
5. CONFLUENCIA. Necesitas al menos DOS indicadores independientes apuntando en \
   la misma direccion. Un solo indicador nunca basta.
6. CONFLICTO. Si hay indicadores apuntando en sentidos opuestos, no hay sesgo \
   claro. ESPERAR.
7. CONTRATENDENCIA. Nunca operes una reversion contra un direccional dominante \
   (diferencia grande entre +DI y -DI). Es la forma mas rapida de perder dinero.

## Como interpretar cada indicador

- RSI: por debajo de 30 sobreventa, por encima de 70 sobrecompra. En tendencia, \
  el cruce del nivel 50 confirma la direccion.
- Bandas de Bollinger: el %B situa el precio en el canal (0 = banda inferior, \
  1 = banda superior). En reversion, los extremos senalan agotamiento; en \
  tendencia, un retroceso hacia la media es zona de entrada.
- ADX: mide FUERZA de tendencia, nunca direccion. La direccion la dan +DI y -DI.
- EMA rapida contra EMA lenta: define el sesgo estructural.
- Estocastico: un cruce de %K sobre %D en zona extrema anticipa el giro.
- MACD: el signo del histograma da la direccion del momento; que se expanda o \
  se contraiga indica si ese momento se acelera o se agota.

## Nivel de confianza

- ALTA: cuatro o mas indicadores alineados, regimen inequivoco, sin senales \
  contrarias.
- MEDIA: tres indicadores alineados y regimen claro.
- BAJA: el minimo de dos indicadores, o alguna senal ambigua.

## Formato de la razon

Una sola linea, tecnica y verificable, citando los valores concretos que \
sustentan la decision. Ejemplo del estilo esperado: "TENDENCIA: ADX 31.2 con \
+DI dominante, RSI cruza 50 al alza (48.1 a 52.7) y MACD expandiendo".

No incluyas disclaimers, ni avisos de riesgo, ni texto fuera del esquema. El \
consumidor de tu respuesta es un script, no una persona.\
"""


# --------------------------------------------------------------------- #
# Fotografia del mercado: la parte volatil del prompt
# --------------------------------------------------------------------- #
def construir_snapshot(series: Series, *, atr_min_pct: float = 0.00005,
                       atr_max_pct: float = 0.005, velas_recientes: int = 12,
                       expiry_velas: int = 5) -> Optional[str]:
    """Resume el estado del mercado en texto compacto para el modelo.

    Se envian indicadores ya calculados en vez de velas en crudo: son menos
    tokens y menos margen de error aritmetico que pedirle al modelo que derive
    un RSI mentalmente. Devuelve None si no hay datos suficientes.
    """
    n = len(series)
    if n < 60:
        return None

    closes, highs, lows = series.closes, series.highs, series.lows
    i = n - 1
    precio = closes[i]

    rsi = ind.rsi(closes, 14)
    bb = ind.bollinger(closes, 20, 2.0)
    ema_f = ind.ema(closes, 9)
    ema_s = ind.ema(closes, 21)
    atr = ind.atr(highs, lows, closes, 14)
    adx_o = ind.adx(highs, lows, closes, 14)
    st = ind.stochastic(highs, lows, closes)
    mac = ind.macd(closes)

    if atr[i] is None or adx_o.adx[i] is None or rsi[i] is None:
        return None

    def f(v, d: int = 2) -> str:
        return f"{v:.{d}f}" if v is not None else "n/d"

    atr_pct = atr[i] / precio if precio else 0.0
    rango_vela = series[i].range
    vela = series[i]

    recientes = " ".join(f"{c:.5f}" for c in closes[-velas_recientes:])
    tf_min = series.timeframe / 60

    return f"""\
ACTIVO: {series.symbol}
TIMEFRAME: {tf_min:.0f} min por vela
VENCIMIENTO OBJETIVO: {expiry_velas} velas ({expiry_velas * tf_min:.0f} min)
HORA UTC: {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}

PRECIO ACTUAL: {precio:.5f}
ULTIMA VELA: apertura {vela.open:.5f} maximo {vela.high:.5f} minimo {vela.low:.5f} cierre {vela.close:.5f}
RANGO ULTIMA VELA: {rango_vela:.5f} ({rango_vela / atr[i]:.2f}x ATR)

VOLATILIDAD
  ATR(14): {f(atr[i], 5)}  =  {atr_pct * 100:.4f}% del precio
  Umbral minimo operable: {atr_min_pct * 100:.4f}%
  Umbral maximo operable: {atr_max_pct * 100:.4f}%

TENDENCIA
  ADX(14): {f(adx_o.adx[i], 1)}   +DI: {f(adx_o.plus_di[i], 1)}   -DI: {f(adx_o.minus_di[i], 1)}
  EMA(9): {f(ema_f[i], 5)}   EMA(21): {f(ema_s[i], 5)}

OSCILADORES
  RSI(14): {f(rsi[i], 1)}   (vela anterior: {f(rsi[i - 1], 1)})
  Estocastico %K: {f(st.k[i], 1)}   %D: {f(st.d[i], 1)}   (%K anterior: {f(st.k[i - 1], 1)})
  MACD histograma: {f(mac.histogram[i], 6)}   (anterior: {f(mac.histogram[i - 1], 6)})

BANDAS DE BOLLINGER(20, 2)
  Superior: {f(bb.upper[i], 5)}   Media: {f(bb.middle[i], 5)}   Inferior: {f(bb.lower[i], 5)}
  %B: {f(bb.percent_b[i], 3)}   Anchura normalizada: {f(bb.width[i], 5)}

ULTIMOS {velas_recientes} CIERRES (antiguo -> reciente)
  {recientes}\
"""
