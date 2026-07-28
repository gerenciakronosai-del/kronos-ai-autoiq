"""Catalogo de hipotesis a barrer.

Cada funcion recibe una `Series` y devuelve una lista de enteros del mismo
largo: +1 para CALL, -1 para PUT, 0 para no operar.

Las tres primeras son CONTROLES, no estrategias. Sirven para saber que mide el
barrido: si "siempre CALL" saca un edge aparente, el resultado refleja la deriva
del periodo y no capacidad predictiva; si la moneda trucada saca algo, el
barrido tiene un fallo. Sin controles no se puede interpretar el resto.
"""

from __future__ import annotations

import random
from typing import Optional

from kronos.core import indicators as ind
from kronos.core.candle import Series


# --------------------------------------------------------------------- #
# Controles
# --------------------------------------------------------------------- #
def siempre_call(series: Series) -> list[int]:
    return [1] * len(series)


def siempre_put(series: Series) -> list[int]:
    return [-1] * len(series)


def aleatoria(series: Series) -> list[int]:
    """Moneda al aire con semilla fija: el nivel de ruido de referencia."""
    rng = random.Random(20260726)
    return [rng.choice((1, -1)) for _ in range(len(series))]


# --------------------------------------------------------------------- #
# Hipotesis de una sola vela
# --------------------------------------------------------------------- #
def momentum_1v(series: Series) -> list[int]:
    """La vela anterior subio -> seguir. Continuacion pura."""
    out = [0] * len(series)
    for i in range(1, len(series)):
        cuerpo = series[i].close - series[i].open
        out[i] = 1 if cuerpo > 0 else (-1 if cuerpo < 0 else 0)
    return out


def reversion_1v(series: Series) -> list[int]:
    """La vela anterior subio -> apostar en contra. Reversion pura."""
    return [-s for s in momentum_1v(series)]


def reversion_vela_larga(series: Series) -> list[int]:
    """Solo desvanece velas anomalas: rango > 2x ATR. Exceso -> correccion."""
    atr = ind.atr(series.highs, series.lows, series.closes, 14)
    out = [0] * len(series)
    for i in range(len(series)):
        if atr[i] is None or atr[i] <= 0:
            continue
        if series[i].range > 2.0 * atr[i]:
            cuerpo = series[i].close - series[i].open
            out[i] = -1 if cuerpo > 0 else (1 if cuerpo < 0 else 0)
    return out


def tres_velas_fade(series: Series) -> list[int]:
    """Tres velas seguidas en la misma direccion -> apostar al giro."""
    out = [0] * len(series)
    for i in range(3, len(series)):
        c = [series[j].close - series[j].open for j in (i - 2, i - 1, i)]
        if all(x > 0 for x in c):
            out[i] = -1
        elif all(x < 0 for x in c):
            out[i] = 1
    return out


def tres_velas_seguir(series: Series) -> list[int]:
    return [-s for s in tres_velas_fade(series)]


# --------------------------------------------------------------------- #
# Hipotesis de indicador
# --------------------------------------------------------------------- #
def rsi_extremo(series: Series) -> list[int]:
    """Sobreventa -> CALL, sobrecompra -> PUT. El clasico de manual."""
    rsi = ind.rsi(series.closes, 14)
    return [0 if v is None else (1 if v < 30 else (-1 if v > 70 else 0)) for v in rsi]


def rsi_extremo_invertido(series: Series) -> list[int]:
    return [-s for s in rsi_extremo(series)]


def rsi_cruce_50(series: Series) -> list[int]:
    """Cruce del nivel medio: seguimiento de tendencia."""
    rsi = ind.rsi(series.closes, 14)
    out = [0] * len(series)
    for i in range(1, len(series)):
        a, b = rsi[i - 1], rsi[i]
        if a is None or b is None:
            continue
        if a <= 50 < b:
            out[i] = 1
        elif a >= 50 > b:
            out[i] = -1
    return out


def bollinger_extremo(series: Series) -> list[int]:
    """Toque de banda -> reversion al canal."""
    bb = ind.bollinger(series.closes, 20, 2.0)
    return [0 if v is None else (1 if v <= 0.05 else (-1 if v >= 0.95 else 0))
            for v in bb.percent_b]


def bollinger_ruptura(series: Series) -> list[int]:
    """Toque de banda -> continuacion de la ruptura."""
    return [-s for s in bollinger_extremo(series)]


def ema_cruce(series: Series) -> list[int]:
    """Cruce de EMA 9/21: sesgo estructural."""
    ef = ind.ema(series.closes, 9)
    es = ind.ema(series.closes, 21)
    out = [0] * len(series)
    for i in range(1, len(series)):
        if None in (ef[i], es[i], ef[i - 1], es[i - 1]):
            continue
        if ef[i - 1] <= es[i - 1] and ef[i] > es[i]:
            out[i] = 1
        elif ef[i - 1] >= es[i - 1] and ef[i] < es[i]:
            out[i] = -1
    return out


def macd_signo(series: Series) -> list[int]:
    m = ind.macd(series.closes)
    return [0 if v is None else (1 if v > 0 else -1) for v in m.histogram]


def estocastico_extremo(series: Series) -> list[int]:
    st = ind.stochastic(series.highs, series.lows, series.closes)
    return [0 if v is None else (1 if v < 20 else (-1 if v > 80 else 0)) for v in st.k]


def adx_direccional(series: Series) -> list[int]:
    """Solo con tendencia fuerte (ADX>=25), operar a favor del direccional."""
    a = ind.adx(series.highs, series.lows, series.closes, 14)
    out = [0] * len(series)
    for i in range(len(series)):
        if a.adx[i] is None or a.adx[i] < 25:
            continue
        if a.plus_di[i] is None or a.minus_di[i] is None:
            continue
        out[i] = 1 if a.plus_di[i] > a.minus_di[i] else -1
    return out


def reversion_filtrada(series: Series) -> list[int]:
    """Reversion en banda de Bollinger, pero solo con volatilidad suficiente
    y sin tendencia dominante. La version 'con filtros' del clasico."""
    bb = ind.bollinger(series.closes, 20, 2.0)
    atr = ind.atr(series.highs, series.lows, series.closes, 14)
    a = ind.adx(series.highs, series.lows, series.closes, 14)
    closes = series.closes
    out = [0] * len(series)
    for i in range(len(series)):
        if None in (bb.percent_b[i], atr[i], a.adx[i]):
            continue
        atr_pct = atr[i] / closes[i] if closes[i] else 0
        if not (0.00005 <= atr_pct <= 0.005):
            continue
        if a.adx[i] >= 25:          # con tendencia fuerte no se opera reversion
            continue
        if bb.percent_b[i] <= 0.05:
            out[i] = 1
        elif bb.percent_b[i] >= 0.95:
            out[i] = -1
    return out


CATALOGO = {
    # controles
    "CTRL siempre CALL": siempre_call,
    "CTRL siempre PUT": siempre_put,
    "CTRL aleatoria": aleatoria,
    # una vela
    "momentum 1 vela": momentum_1v,
    "reversion 1 vela": reversion_1v,
    "fade vela larga": reversion_vela_larga,
    "fade 3 velas": tres_velas_fade,
    "seguir 3 velas": tres_velas_seguir,
    # indicadores
    "RSI extremo": rsi_extremo,
    "RSI extremo invert.": rsi_extremo_invertido,
    "RSI cruce 50": rsi_cruce_50,
    "Bollinger extremo": bollinger_extremo,
    "Bollinger ruptura": bollinger_ruptura,
    "EMA cruce 9/21": ema_cruce,
    "MACD signo": macd_signo,
    "Estocastico extremo": estocastico_extremo,
    "ADX direccional": adx_direccional,
    "reversion filtrada": reversion_filtrada,
}
