"""Estrategia de confluencia multi-indicador para opciones binarias 1-5 min.

Sustituye la llamada a un LLM por reglas deterministas y auditables. El orden de
evaluacion es deliberado: primero los FILTROS que vetan la operacion, y solo
despues el conteo de confluencia. Un veto nunca se puede compensar con votos.

    1. Datos suficientes             -> si no, ESPERAR
    2. Filtro de volatilidad (ATR%)  -> mercado muerto, ESPERAR
    3. Filtro de spike / noticia     -> vela anomala, ESPERAR
    4. Filtro de lateral estrecho    -> ADX bajo + Bollinger comprimido, ESPERAR
    5. Clasificacion de regimen      -> TENDENCIA (ADX alto) o REVERSION
    6. Confluencia (>= min_votos)    -> votos a favor de CALL / PUT
    7. Conflicto entre bandos        -> ESPERAR
    8. Confianza segun score y margen
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from kronos.core import indicators as ind
from kronos.core.candle import Series
from kronos.strategy.base import Confidence, Decision, Regime, Signal, Strategy, Vote


@dataclass(slots=True)
class ConfluenceParams:
    """Parametros de la estrategia. Todos los umbrales viven aqui, ninguno inline."""

    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    rsi_trend_mid: float = 50.0

    bb_period: int = 20
    bb_mult: float = 2.0
    bb_lower_pb: float = 0.10          # %B por debajo -> zona de sobreventa
    bb_upper_pb: float = 0.90          # %B por encima -> zona de sobrecompra
    bb_min_width: float = 0.0002       # anchura normalizada minima (anti-lateral)

    ema_fast: int = 9
    ema_slow: int = 21

    # Umbrales de volatilidad calibrados para FX en 1 minuto, donde el ATR
    # tipico ronda el 0.008-0.015% del precio. OJO: son especificos del activo y
    # del timeframe. En un indice o una cripto los valores son otros; si el
    # backtest reporta que casi todo se veta por "volatilidad insuficiente", es
    # esto lo que hay que recalibrar (`kronos indicadores` muestra el ATR% real).
    atr_period: int = 14
    atr_min_pct: float = 0.00005       # ATR/precio minimo (anti baja volatilidad)
    atr_max_pct: float = 0.005         # ATR/precio maximo (anti caos/noticia)
    spike_atr_mult: float = 3.0        # rango de vela > N*ATR -> spike, no operar

    adx_period: int = 14
    adx_range_max: float = 20.0        # por debajo: mercado sin tendencia
    adx_trend_min: float = 25.0        # por encima: regimen de tendencia

    stoch_k: int = 14
    stoch_d: int = 3
    stoch_smooth: int = 3
    stoch_oversold: float = 20.0
    stoch_overbought: float = 80.0

    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    min_votos: int = 2                 # confluencia minima exigida
    votos_alta: int = 4                # score >= -> confianza ALTA
    votos_media: int = 3               # score >= -> confianza MEDIA
    permitir_contratendencia: bool = False  # reversion contra ADX fuerte

    def __post_init__(self) -> None:
        if self.min_votos < 2:
            raise ValueError("min_votos debe ser >= 2 (la regla exige confluencia)")
        if self.ema_fast >= self.ema_slow:
            raise ValueError("ema_fast debe ser < ema_slow")
        if not 0.0 <= self.bb_lower_pb < self.bb_upper_pb <= 1.0:
            raise ValueError("umbrales de %B invalidos")
        if self.adx_range_max > self.adx_trend_min:
            raise ValueError("adx_range_max no puede superar adx_trend_min")
        if self.atr_min_pct >= self.atr_max_pct:
            raise ValueError("atr_min_pct debe ser < atr_max_pct")


class ConfluenceStrategy(Strategy):
    """Motor de reglas por confluencia con filtros de regimen y volatilidad."""

    name = "confluence"

    def __init__(self, params: Optional[ConfluenceParams] = None):
        self.p = params or ConfluenceParams()

    @property
    def min_bars(self) -> int:
        p = self.p
        return max(
            p.bb_period,
            p.rsi_period + 1,
            p.ema_slow,
            p.adx_period * 2 + 2,
            p.macd_slow + p.macd_signal,
            p.stoch_k + p.stoch_d + p.stoch_smooth,
        ) + 5

    # ------------------------------------------------------------------ #
    def evaluate(self, series: Series) -> Signal:
        p = self.p
        n = len(series)
        base = {"symbol": series.symbol, "ts": series[-1].ts if n else 0}

        if n < self.min_bars:
            return Signal.esperar(
                f"Datos insuficientes: {n}/{self.min_bars} velas para calcular indicadores.", **base
            )

        closes, highs, lows = series.closes, series.highs, series.lows
        i = n - 1
        price = closes[i]

        rsi_v = ind.rsi(closes, p.rsi_period)
        bb = ind.bollinger(closes, p.bb_period, p.bb_mult)
        ema_f = ind.ema(closes, p.ema_fast)
        ema_s = ind.ema(closes, p.ema_slow)
        atr_v = ind.atr(highs, lows, closes, p.atr_period)
        adx_o = ind.adx(highs, lows, closes, p.adx_period)
        st = ind.stochastic(highs, lows, closes, p.stoch_k, p.stoch_d, p.stoch_smooth)
        mac = ind.macd(closes, p.macd_fast, p.macd_slow, p.macd_signal)

        # El ATR se evalua primero: en un mercado sin movimiento el ADX ni
        # siquiera existe (division por cero), y decir "calentamiento" cuando lo
        # que pasa es que no hay volatilidad seria una razon enganosa.
        if atr_v[i] is None:
            return Signal.esperar(
                f"ATR aun en calentamiento ({p.atr_period} velas necesarias).", **base
            )
        atr_now = float(atr_v[i])
        atr_pct = atr_now / price if price else 0.0

        if atr_pct < p.atr_min_pct:
            return Signal.esperar(
                f"Volatilidad insuficiente: ATR {atr_pct*100:.3f}% < minimo {p.atr_min_pct*100:.3f}%.",
                regimen=Regime.INDEFINIDO, **base,
            )
        if atr_pct > p.atr_max_pct:
            return Signal.esperar(
                f"Volatilidad anomala: ATR {atr_pct*100:.3f}% > maximo {p.atr_max_pct*100:.3f}%, riesgo de noticia.",
                regimen=Regime.INDEFINIDO, **base,
            )
        if series[i].range > p.spike_atr_mult * atr_now:
            return Signal.esperar(
                f"Vela anomala: rango {series[i].range/atr_now:.1f}x ATR, posible spike de noticia.",
                regimen=Regime.INDEFINIDO, **base,
            )

        need = {
            "RSI": rsi_v[i], "BB %B": bb.percent_b[i], "BB anchura": bb.width[i],
            "EMA rapida": ema_f[i], "EMA lenta": ema_s[i], "ADX": adx_o.adx[i],
            "Stoch %K": st.k[i], "Stoch %D": st.d[i], "MACD hist": mac.histogram[i],
        }
        faltan = [k for k, v in need.items() if v is None]
        if faltan:
            return Signal.esperar(
                f"Indicadores aun en calentamiento: {', '.join(faltan)}.", **base
            )

        rsi_now = float(rsi_v[i]); rsi_prev = float(rsi_v[i - 1]) if rsi_v[i - 1] is not None else rsi_now
        pb_now = float(bb.percent_b[i]); pb_prev = float(bb.percent_b[i - 1]) if bb.percent_b[i - 1] is not None else pb_now
        bb_w = float(bb.width[i])
        e_fast = float(ema_f[i]); e_slow = float(ema_s[i])
        adx_now = float(adx_o.adx[i])
        pdi = float(adx_o.plus_di[i]) if adx_o.plus_di[i] is not None else 0.0
        mdi = float(adx_o.minus_di[i]) if adx_o.minus_di[i] is not None else 0.0
        k_now = float(st.k[i]); d_now = float(st.d[i])
        k_prev = float(st.k[i - 1]) if st.k[i - 1] is not None else k_now
        d_prev = float(st.d[i - 1]) if st.d[i - 1] is not None else d_now
        hist = float(mac.histogram[i])
        hist_prev = float(mac.histogram[i - 1]) if mac.histogram[i - 1] is not None else hist

        ctx = {
            "precio": price, "rsi": rsi_now, "percent_b": pb_now, "bb_width": bb_w,
            "ema_fast": e_fast, "ema_slow": e_slow, "atr": atr_now, "atr_pct": atr_pct,
            "adx": adx_now, "plus_di": pdi, "minus_di": mdi,
            "stoch_k": k_now, "stoch_d": d_now, "macd_hist": hist,
        }
        base["contexto"] = ctx

        # --- Filtro de lateralidad (los de volatilidad ya se aplicaron) --- #
        if adx_now < p.adx_range_max and bb_w < p.bb_min_width:
            return Signal.esperar(
                f"Rango lateral estrecho: ADX {adx_now:.1f} y Bollinger comprimido ({bb_w*100:.3f}%).",
                regimen=Regime.INDEFINIDO, **base,
            )

        # --- Regimen ---------------------------------------------------- #
        regimen = Regime.TENDENCIA if adx_now >= p.adx_trend_min else Regime.REVERSION
        base["regimen"] = regimen

        votos = (
            self._votos_tendencia(p, pdi, mdi, e_fast, e_slow, rsi_now, rsi_prev,
                                  pb_now, hist, hist_prev, k_now, k_prev, d_now, d_prev)
            if regimen is Regime.TENDENCIA
            else self._votos_reversion(p, rsi_now, rsi_prev, pb_now, pb_prev,
                                       k_now, k_prev, d_now, d_prev, hist, hist_prev)
        )

        calls = [v for v in votos if v.direccion is Decision.CALL]
        puts = [v for v in votos if v.direccion is Decision.PUT]

        if calls and puts:
            return Signal.esperar(
                f"Senales en conflicto ({len(calls)} CALL vs {len(puts)} PUT); sin sesgo claro.",
                votos=votos, **base,
            )

        lado = calls or puts
        if len(lado) < p.min_votos:
            return Signal.esperar(
                f"Confluencia insuficiente: {len(lado)}/{p.min_votos} indicadores alineados en regimen {regimen}.",
                votos=votos, score=len(lado), **base,
            )

        decision = lado[0].direccion

        # Guarda anti-contratendencia: no comprar reversion contra tendencia fuerte.
        if (
            regimen is Regime.REVERSION
            and not p.permitir_contratendencia
            and adx_now >= p.adx_range_max
        ):
            direccion_tendencia = Decision.CALL if pdi > mdi else Decision.PUT
            if decision is not direccion_tendencia and abs(pdi - mdi) > 10.0:
                return Signal.esperar(
                    f"Reversion {decision} contra direccional dominante (+DI {pdi:.1f} / -DI {mdi:.1f}); descartada.",
                    votos=votos, score=len(lado), **base,
                )

        score = len(lado)
        confianza = (
            Confidence.ALTA if score >= p.votos_alta
            else Confidence.MEDIA if score >= p.votos_media
            else Confidence.BAJA
        )
        # Degradacion: en reversion sin exceso claro la confianza baja un escalon.
        if regimen is Regime.REVERSION and confianza is Confidence.ALTA and adx_now > p.adx_range_max:
            confianza = Confidence.MEDIA

        razon = (
            f"{regimen}: {score} confluencias {decision} ("
            + ", ".join(v.indicador for v in lado)
            + f"); RSI {rsi_now:.1f}, %B {pb_now:.2f}, ADX {adx_now:.1f}."
        )
        return Signal(
            decision=decision, confianza=confianza, razon=razon,
            score=score, votos=votos, **base,
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def _votos_tendencia(p, pdi, mdi, e_fast, e_slow, rsi_now, rsi_prev,
                         pb_now, hist, hist_prev, k_now, k_prev, d_now, d_prev) -> list[Vote]:
        """En tendencia se opera A FAVOR, buscando el final de un retroceso."""
        alcista = pdi > mdi and e_fast > e_slow
        bajista = mdi > pdi and e_fast < e_slow
        if not (alcista or bajista):
            return []
        lado = Decision.CALL if alcista else Decision.PUT
        v: list[Vote] = [
            Vote("EMA+DI", lado,
                 f"EMA{p.ema_fast}{'>' if alcista else '<'}EMA{p.ema_slow} con {'+DI' if alcista else '-DI'} dominante")
        ]
        if alcista:
            if rsi_prev <= p.rsi_trend_mid < rsi_now:
                v.append(Vote("RSI", lado, f"cruce alcista de 50 ({rsi_prev:.1f}->{rsi_now:.1f})"))
            if pb_now < 0.5:
                v.append(Vote("Bollinger", lado, f"retroceso a la mitad baja del canal (%B {pb_now:.2f})"))
            if hist > 0 and hist > hist_prev:
                v.append(Vote("MACD", lado, "histograma positivo y expandiendo"))
            if k_prev <= d_prev < k_now and k_now < 60:
                v.append(Vote("Estocastico", lado, f"cruce %K/%D al alza en zona baja ({k_now:.1f})"))
        else:
            if rsi_prev >= p.rsi_trend_mid > rsi_now:
                v.append(Vote("RSI", lado, f"cruce bajista de 50 ({rsi_prev:.1f}->{rsi_now:.1f})"))
            if pb_now > 0.5:
                v.append(Vote("Bollinger", lado, f"rebote a la mitad alta del canal (%B {pb_now:.2f})"))
            if hist < 0 and hist < hist_prev:
                v.append(Vote("MACD", lado, "histograma negativo y expandiendo"))
            if k_prev >= d_prev > k_now and k_now > 40:
                v.append(Vote("Estocastico", lado, f"cruce %K/%D a la baja en zona alta ({k_now:.1f})"))
        return v

    @staticmethod
    def _votos_reversion(p, rsi_now, rsi_prev, pb_now, pb_prev,
                         k_now, k_prev, d_now, d_prev, hist, hist_prev) -> list[Vote]:
        """Sin tendencia dominante se busca agotamiento en los extremos del canal."""
        v: list[Vote] = []
        # CALL: agotamiento bajista
        if rsi_now < p.rsi_oversold:
            v.append(Vote("RSI", Decision.CALL, f"sobreventa {rsi_now:.1f} < {p.rsi_oversold:.0f}"))
        if pb_now <= p.bb_lower_pb:
            v.append(Vote("Bollinger", Decision.CALL, f"precio en banda inferior (%B {pb_now:.2f})"))
        if k_now < p.stoch_oversold and k_prev <= d_prev < k_now:
            v.append(Vote("Estocastico", Decision.CALL, f"cruce al alza en sobreventa ({k_now:.1f})"))
        if pb_prev < p.bb_lower_pb <= pb_now:
            v.append(Vote("Rebote BB", Decision.CALL, "reentrada al canal desde banda inferior"))
        if hist < 0 and hist > hist_prev and rsi_now < 45:
            v.append(Vote("MACD", Decision.CALL, "divergencia de momento: histograma negativo contrayendose"))

        # PUT: agotamiento alcista
        if rsi_now > p.rsi_overbought:
            v.append(Vote("RSI", Decision.PUT, f"sobrecompra {rsi_now:.1f} > {p.rsi_overbought:.0f}"))
        if pb_now >= p.bb_upper_pb:
            v.append(Vote("Bollinger", Decision.PUT, f"precio en banda superior (%B {pb_now:.2f})"))
        if k_now > p.stoch_overbought and k_prev >= d_prev > k_now:
            v.append(Vote("Estocastico", Decision.PUT, f"cruce a la baja en sobrecompra ({k_now:.1f})"))
        if pb_prev > p.bb_upper_pb >= pb_now:
            v.append(Vote("Rebote BB", Decision.PUT, "reentrada al canal desde banda superior"))
        if hist > 0 and hist < hist_prev and rsi_now > 55:
            v.append(Vote("MACD", Decision.PUT, "divergencia de momento: histograma positivo contrayendose"))
        return v
