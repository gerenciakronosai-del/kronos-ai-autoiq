"""Backtest con stop y objetivo, en vez de vencimiento fijo.

Es el cambio que separa las opciones binarias del trading direccional:

* **Binarias**: el payoff lo fija el broker (ganas 0.84x, pierdes 1.00x) y no se
  puede tocar. El umbral de equilibrio es 54.35% y es infranqueable si el mercado
  no te da esa ventaja.
* **Stop y objetivo**: TU eliges la relacion. Con objetivo 2:1 basta acertar el
  33.3%. El problema pasa de imposible a resoluble — no resuelto, resoluble.

## La trampa que este modulo evita

Dentro de una vela OHLC no sabes si el maximo llego antes que el minimo. Si en
la misma vela se tocan el stop y el objetivo, hay dos formas de resolverlo:

* Asumir que gano el objetivo -> infla los resultados y es la causa numero uno
  de backtests de stops que no se reproducen en real.
* Asumir que gano el stop -> pesimista, pero nunca te miente a favor.

Aqui se asume SIEMPRE el stop. Un resultado bueno con esta convencion es
creible; uno bueno con la contraria no dice nada.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from kronos.backtest.metrics import binomial_p_value, wilson_interval
from kronos.core import indicators as ind
from kronos.core.candle import Series


@dataclass(slots=True)
class ResultadoStops:
    """Resultado de operar una señal con stop y objetivo."""

    nombre: str
    ganadas: int = 0
    perdidas: int = 0
    sin_resolver: int = 0      # ni stop ni objetivo dentro del horizonte
    rr: float = 2.0
    coste_r: float = 0.0       # spread pagado, en unidades de R

    @property
    def n(self) -> int:
        return self.ganadas + self.perdidas

    @property
    def winrate(self) -> float:
        return self.ganadas / self.n if self.n else 0.0

    @property
    def umbral(self) -> float:
        """Winrate minimo para no perder, dado el ratio objetivo:riesgo."""
        return 1.0 / (1.0 + self.rr)

    @property
    def edge(self) -> float:
        return self.winrate - self.umbral

    @property
    def esperanza_r(self) -> float:
        """Esperanza por operacion en unidades de riesgo (R), spread incluido."""
        if not self.n:
            return 0.0
        bruto = self.winrate * self.rr - (1 - self.winrate)
        return bruto - self.coste_r

    @property
    def p_valor(self) -> float:
        return binomial_p_value(self.ganadas, self.n, self.umbral)

    @property
    def ic95(self) -> tuple[float, float]:
        return wilson_interval(self.ganadas, self.n)


def evaluar_con_stops(series: Series, senales: Sequence[int], *,
                      rr: float = 2.0, atr_mult: float = 1.5,
                      atr_period: int = 14, max_velas: int = 48,
                      spread_pips: float = 0.5, valor_pip: float = 0.0001,
                      nombre: str = "") -> ResultadoStops:
    """Simula cada señal con stop y objetivo proporcionales a la volatilidad.

    El stop se coloca a `atr_mult` veces el ATR, y el objetivo a `rr` veces esa
    distancia. Usar el ATR y no una distancia fija hace que la estrategia se
    adapte sola a mercados tranquilos y agitados.

    Recorre las velas posteriores mirando maximos y minimos. Si en una misma
    vela caben stop y objetivo, cuenta STOP (ver docstring del modulo).
    """
    if rr <= 0:
        raise ValueError("rr debe ser > 0")
    if atr_mult <= 0:
        raise ValueError("atr_mult debe ser > 0")

    closes, highs, lows = series.closes, series.highs, series.lows
    atr = ind.atr(highs, lows, closes, atr_period)
    n = len(closes)
    slip = spread_pips * valor_pip
    res = ResultadoStops(nombre or "?", rr=rr)
    costes: list[float] = []

    for i, s in enumerate(senales):
        if s == 0 or atr[i] is None or atr[i] <= 0 or i + 1 >= n:
            continue
        riesgo = atr_mult * atr[i]
        if riesgo <= 0:
            continue

        # El spread juega en contra en la entrada, como siempre.
        entrada = closes[i] + slip if s > 0 else closes[i] - slip
        if s > 0:
            stop, objetivo = entrada - riesgo, entrada + riesgo * rr
        else:
            stop, objetivo = entrada + riesgo, entrada - riesgo * rr

        resuelto = False
        for j in range(i + 1, min(i + 1 + max_velas, n)):
            toca_stop = lows[j] <= stop if s > 0 else highs[j] >= stop
            toca_obj = highs[j] >= objetivo if s > 0 else lows[j] <= objetivo
            if toca_stop:  # el stop manda aunque tambien se tocara el objetivo
                res.perdidas += 1
                resuelto = True
                break
            if toca_obj:
                res.ganadas += 1
                resuelto = True
                break
        if not resuelto:
            res.sin_resolver += 1
            continue
        costes.append(slip / riesgo)  # el spread, medido en unidades de R

    res.coste_r = sum(costes) / len(costes) if costes else 0.0
    return res


def informe(resultados: Sequence[ResultadoStops], *, top: int = 15) -> str:
    """Tabla ordenada por esperanza. Las lineas ASCII, como el resto."""
    ordenados = sorted(resultados, key=lambda r: -r.esperanza_r)
    L = ["=" * 84,
         f"  BACKTEST CON STOP Y OBJETIVO - {len(resultados)} combinaciones",
         "=" * 84,
         "",
         "  Convencion pesimista: si en una vela caben stop y objetivo, cuenta STOP.",
         ""]
    L.append(f"  {'hipotesis':<26}{'N':>7}{'winrate':>9}{'umbral':>8}"
             f"{'edge':>8}{'esp(R)':>9}{'p':>8}")
    L.append("  " + "-" * 76)
    for r in ordenados[:top]:
        if r.n < 30:
            continue
        L.append(f"  {r.nombre:<26}{r.n:>7,}{r.winrate * 100:>8.2f}%"
                 f"{r.umbral * 100:>7.1f}%{r.edge * 100:>+7.2f}%"
                 f"{r.esperanza_r:>+9.3f}{r.p_valor:>8.3f}")
    L.append("=" * 84)
    return "\n".join(L)
