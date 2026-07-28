"""Indicadores tecnicos en Python puro (sin numpy/pandas).

Convencion comun a todas las funciones:

* Reciben listas de `float` y devuelven listas de la MISMA longitud.
* Las posiciones en periodo de calentamiento (warm-up) valen `None`.
* Nunca miran hacia el futuro: el valor en el indice `i` solo usa datos `<= i`.
  Esta propiedad esta cubierta por tests y es la que evita el sesgo de
  look-ahead en el backtest.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

Num = Optional[float]


def _check(period: int, name: str = "period") -> None:
    if period < 1:
        raise ValueError(f"{name} debe ser >= 1, recibido {period}")


def sma(values: Sequence[float], period: int) -> list[Num]:
    """Media movil simple."""
    _check(period)
    out: list[Num] = [None] * len(values)
    if len(values) < period:
        return out
    acc = sum(values[:period])
    out[period - 1] = acc / period
    for i in range(period, len(values)):
        acc += values[i] - values[i - period]
        out[i] = acc / period
    return out


def ema(values: Sequence[float], period: int) -> list[Num]:
    """Media movil exponencial, sembrada con la SMA del primer bloque."""
    _check(period)
    out: list[Num] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1.0)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def wilder_smooth(values: Sequence[float], period: int) -> list[Num]:
    """Suavizado de Wilder (RMA), base de RSI / ATR / ADX."""
    _check(period)
    out: list[Num] = [None] * len(values)
    if len(values) < period:
        return out
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out


def stdev(values: Sequence[float], period: int) -> list[Num]:
    """Desviacion tipica poblacional movil (la que usan las Bandas de Bollinger)."""
    _check(period)
    out: list[Num] = [None] * len(values)
    if len(values) < period:
        return out
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        mean = sum(window) / period
        var = sum((v - mean) ** 2 for v in window) / period
        out[i] = math.sqrt(var)
    return out


def rsi(values: Sequence[float], period: int = 14) -> list[Num]:
    """RSI de Wilder. Rango 0-100."""
    _check(period)
    n = len(values)
    out: list[Num] = [None] * n
    if n <= period:
        return out

    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        d = values[i] - values[i - 1]
        gains[i] = max(d, 0.0)
        losses[i] = max(-d, 0.0)

    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    out[period] = _rsi_from(avg_gain, avg_loss)

    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i] = _rsi_from(avg_gain, avg_loss)
    return out


def _rsi_from(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


class BollingerBands:
    """Contenedor de las tres bandas mas metricas derivadas."""

    __slots__ = ("upper", "middle", "lower", "width", "percent_b")

    def __init__(self, upper: list[Num], middle: list[Num], lower: list[Num],
                 width: list[Num], percent_b: list[Num]):
        self.upper = upper
        self.middle = middle
        self.lower = lower
        self.width = width
        self.percent_b = percent_b

    def __len__(self) -> int:
        return len(self.middle)


def bollinger(values: Sequence[float], period: int = 20, mult: float = 2.0) -> BollingerBands:
    """Bandas de Bollinger.

    `width` es la anchura normalizada ((upper-lower)/middle), util como filtro
    de volatilidad/lateralidad. `percent_b` situa el precio dentro del canal:
    0 = banda inferior, 1 = banda superior.
    """
    _check(period)
    if mult <= 0:
        raise ValueError("mult debe ser > 0")
    mid = sma(values, period)
    sd = stdev(values, period)
    n = len(values)
    up: list[Num] = [None] * n
    lo: list[Num] = [None] * n
    width: list[Num] = [None] * n
    pb: list[Num] = [None] * n
    for i in range(n):
        m, s = mid[i], sd[i]
        if m is None or s is None:
            continue
        u = m + mult * s
        l = m - mult * s
        up[i], lo[i] = u, l
        width[i] = (u - l) / m if m else None
        span = u - l
        pb[i] = (values[i] - l) / span if span > 1e-12 else 0.5
    return BollingerBands(up, mid, lo, width, pb)


def true_range(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> list[float]:
    n = len(closes)
    tr = [0.0] * n
    if n:
        tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    return tr


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int = 14) -> list[Num]:
    """Average True Range (Wilder)."""
    return wilder_smooth(true_range(highs, lows, closes), period)


class ADX:
    __slots__ = ("adx", "plus_di", "minus_di")

    def __init__(self, adx: list[Num], plus_di: list[Num], minus_di: list[Num]):
        self.adx = adx
        self.plus_di = plus_di
        self.minus_di = minus_di


def adx(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int = 14) -> ADX:
    """ADX y direccionales de Wilder. ADX mide FUERZA de tendencia, no direccion."""
    _check(period)
    n = len(closes)
    empty: list[Num] = [None] * n
    if n < period * 2:
        return ADX(list(empty), list(empty), list(empty))

    tr = true_range(highs, lows, closes)
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0

    # Wilder arranca las series en el indice 1 (el DM del indice 0 no existe).
    atr_s = wilder_smooth(tr[1:], period)
    pdm_s = wilder_smooth(plus_dm[1:], period)
    mdm_s = wilder_smooth(minus_dm[1:], period)

    pdi: list[Num] = [None] * n
    mdi: list[Num] = [None] * n
    dx_vals: list[float] = []
    dx_index: list[int] = []
    for j in range(len(atr_s)):
        a, p, m = atr_s[j], pdm_s[j], mdm_s[j]
        if a is None or p is None or m is None or a <= 1e-12:
            continue
        i = j + 1
        pdi[i] = 100.0 * p / a
        mdi[i] = 100.0 * m / a
        denom = pdi[i] + mdi[i]
        dx_vals.append(100.0 * abs(pdi[i] - mdi[i]) / denom if denom > 1e-12 else 0.0)
        dx_index.append(i)

    out: list[Num] = [None] * n
    smoothed = wilder_smooth(dx_vals, period)
    for j, val in enumerate(smoothed):
        if val is not None:
            out[dx_index[j]] = val
    return ADX(out, pdi, mdi)


class MACD:
    __slots__ = ("macd", "signal", "histogram")

    def __init__(self, macd: list[Num], signal: list[Num], histogram: list[Num]):
        self.macd = macd
        self.signal = signal
        self.histogram = histogram


def macd(values: Sequence[float], fast: int = 12, slow: int = 26, signal_period: int = 9) -> MACD:
    if fast >= slow:
        raise ValueError("fast debe ser < slow")
    ef = ema(values, fast)
    es = ema(values, slow)
    n = len(values)
    line: list[Num] = [None if (ef[i] is None or es[i] is None) else ef[i] - es[i] for i in range(n)]

    dense = [v for v in line if v is not None]
    sig_dense = ema(dense, signal_period)
    offset = n - len(dense)
    sig: list[Num] = [None] * n
    for j, v in enumerate(sig_dense):
        sig[offset + j] = v

    hist: list[Num] = [
        None if (line[i] is None or sig[i] is None) else line[i] - sig[i] for i in range(n)
    ]
    return MACD(line, sig, hist)


class Stochastic:
    __slots__ = ("k", "d")

    def __init__(self, k: list[Num], d: list[Num]):
        self.k = k
        self.d = d


def stochastic(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
               k_period: int = 14, d_period: int = 3, smooth_k: int = 3) -> Stochastic:
    """Estocastico lento. %K suavizado y %D = SMA(%K)."""
    _check(k_period)
    n = len(closes)
    raw: list[Num] = [None] * n
    for i in range(k_period - 1, n):
        hh = max(highs[i - k_period + 1 : i + 1])
        ll = min(lows[i - k_period + 1 : i + 1])
        span = hh - ll
        raw[i] = 50.0 if span <= 1e-12 else 100.0 * (closes[i] - ll) / span

    dense = [v for v in raw if v is not None]
    k_dense = sma(dense, smooth_k) if smooth_k > 1 else list(dense)
    offset = n - len(dense)
    k: list[Num] = [None] * n
    for j, v in enumerate(k_dense):
        k[offset + j] = v

    k_valid = [v for v in k if v is not None]
    d_dense = sma(k_valid, d_period)
    off_d = n - len(k_valid)
    d: list[Num] = [None] * n
    for j, v in enumerate(d_dense):
        d[off_d + j] = v
    return Stochastic(k, d)


def slope(values: Sequence[Num], lookback: int = 5) -> list[Num]:
    """Pendiente normalizada de una serie: (v[i] - v[i-lookback]) / |v[i-lookback]|."""
    _check(lookback, "lookback")
    n = len(values)
    out: list[Num] = [None] * n
    for i in range(lookback, n):
        a, b = values[i - lookback], values[i]
        if a is None or b is None or abs(a) < 1e-12:
            continue
        out[i] = (b - a) / abs(a)
    return out
