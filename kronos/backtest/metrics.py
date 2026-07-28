"""Metricas de rendimiento para opciones binarias.

La metrica que manda en este instrumento no es el porcentaje de aciertos, es la
comparacion contra el UMBRAL DE EQUILIBRIO:

    breakeven = 1 / (1 + payout)

Con un payout del 80% hace falta acertar el 55.6% solo para no perder dinero.
Un 53% de aciertos, que suena bien, pierde de forma sostenida. Por eso todas las
salidas de este modulo muestran el edge (winrate - breakeven) y un contraste de
significancia: sin suficientes operaciones, un edge positivo es indistinguible
del azar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional, Sequence


class Resultado(StrEnum):
    WIN = "WIN"
    LOSS = "LOSS"
    TIE = "TIE"


@dataclass(slots=True)
class Trade:
    ts_entrada: int
    ts_salida: int
    symbol: str
    decision: str
    confianza: str
    regimen: str
    precio_entrada: float
    precio_salida: float
    stake: float
    payout: float
    resultado: Resultado
    pnl: float
    balance_despues: float
    score: int = 0
    razon: str = ""


def breakeven_winrate(payout: float) -> float:
    """Winrate minimo para no perder dinero con un payout dado."""
    if payout <= 0:
        raise ValueError("payout debe ser > 0")
    return 1.0 / (1.0 + payout)


def expectancy(winrate: float, payout: float) -> float:
    """Esperanza por unidad de stake. Negativa = el sistema pierde dinero."""
    return winrate * payout - (1.0 - winrate)


def _log_binom_pmf(k: int, n: int, p: float) -> float:
    return (
        math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
        + k * math.log(p) + (n - k) * math.log1p(-p)
    )


def binomial_p_value(wins: int, n: int, p0: float) -> float:
    """P(X >= wins | n, p0), una cola. Exacta hasta n=5000, luego normal."""
    if n <= 0:
        return 1.0
    if wins <= 0:
        return 1.0
    if wins > n:
        return 0.0
    if n <= 5000:
        return min(1.0, sum(math.exp(_log_binom_pmf(k, n, p0)) for k in range(wins, n + 1)))
    mu = n * p0
    sigma = math.sqrt(n * p0 * (1 - p0))
    if sigma <= 0:
        return 1.0
    z = (wins - 0.5 - mu) / sigma
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalo de confianza de Wilson para el winrate (95% por defecto)."""
    if n == 0:
        return (0.0, 0.0)
    phat = wins / n
    d = 1 + z * z / n
    centro = (phat + z * z / (2 * n)) / d
    margen = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centro - margen), min(1.0, centro + margen))


def max_drawdown(equity: Sequence[float]) -> tuple[float, float]:
    """Devuelve (drawdown absoluto, drawdown relativo) sobre la curva de capital."""
    if not equity:
        return (0.0, 0.0)
    pico = equity[0]
    dd_abs = 0.0
    dd_rel = 0.0
    for v in equity:
        pico = max(pico, v)
        caida = pico - v
        if caida > dd_abs:
            dd_abs = caida
        if pico > 0 and caida / pico > dd_rel:
            dd_rel = caida / pico
    return (dd_abs, dd_rel)


def rachas(trades: Sequence[Trade]) -> tuple[int, int]:
    """Racha maxima ganadora y perdedora (los empates no rompen racha)."""
    mejor = peor = actual_w = actual_l = 0
    for t in trades:
        if t.resultado is Resultado.WIN:
            actual_w += 1
            actual_l = 0
        elif t.resultado is Resultado.LOSS:
            actual_l += 1
            actual_w = 0
        mejor = max(mejor, actual_w)
        peor = max(peor, actual_l)
    return (mejor, peor)


@dataclass(slots=True)
class Desglose:
    etiqueta: str
    n: int
    wins: int
    losses: int
    ties: int
    pnl: float

    @property
    def winrate(self) -> float:
        dec = self.wins + self.losses
        return self.wins / dec if dec else 0.0


@dataclass(slots=True)
class BacktestResult:
    symbol: str
    estrategia: str
    payout: float
    balance_inicial: float
    balance_final: float
    trades: list[Trade] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    velas_evaluadas: int = 0
    senales_emitidas: int = 0
    vetos: dict[str, int] = field(default_factory=dict)
    kill_switch: bool = False
    motivo_kill: str = ""

    # -- conteos --------------------------------------------------------- #
    @property
    def n(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.resultado is Resultado.WIN)

    @property
    def losses(self) -> int:
        return sum(1 for t in self.trades if t.resultado is Resultado.LOSS)

    @property
    def ties(self) -> int:
        return sum(1 for t in self.trades if t.resultado is Resultado.TIE)

    @property
    def decisivas(self) -> int:
        """Operaciones con resultado real (los empates se devuelven integros)."""
        return self.wins + self.losses

    # -- rendimiento ------------------------------------------------------ #
    @property
    def winrate(self) -> float:
        return self.wins / self.decisivas if self.decisivas else 0.0

    @property
    def breakeven(self) -> float:
        return breakeven_winrate(self.payout)

    @property
    def edge(self) -> float:
        """Ventaja real: winrate - umbral de equilibrio. Si es <= 0, no hay sistema."""
        return self.winrate - self.breakeven

    @property
    def esperanza_por_operacion(self) -> float:
        return expectancy(self.winrate, self.payout)

    @property
    def pnl(self) -> float:
        return self.balance_final - self.balance_inicial

    @property
    def retorno(self) -> float:
        return self.pnl / self.balance_inicial if self.balance_inicial else 0.0

    @property
    def profit_factor(self) -> float:
        g = sum(t.pnl for t in self.trades if t.pnl > 0)
        p = -sum(t.pnl for t in self.trades if t.pnl < 0)
        return g / p if p > 0 else (math.inf if g > 0 else 0.0)

    @property
    def drawdown(self) -> tuple[float, float]:
        return max_drawdown(self.equity)

    @property
    def rachas(self) -> tuple[int, int]:
        return rachas(self.trades)

    @property
    def p_value(self) -> float:
        """Probabilidad de obtener este winrate o mejor por puro azar."""
        return binomial_p_value(self.wins, self.decisivas, self.breakeven)

    @property
    def ic95(self) -> tuple[float, float]:
        return wilson_interval(self.wins, self.decisivas)

    @property
    def significativo(self) -> bool:
        """Edge positivo y estadisticamente distinguible del azar (p < 0.05)."""
        return self.edge > 0 and self.p_value < 0.05

    @property
    def tasa_actividad(self) -> float:
        return self.n / self.velas_evaluadas if self.velas_evaluadas else 0.0

    def trades_minimos_necesarios(self) -> Optional[int]:
        """Operaciones que harian falta para que el edge actual sea significativo.

        Devuelve None si el edge es <= 0 (ninguna cantidad de datos lo salva).
        """
        if self.edge <= 0 or self.decisivas == 0:
            return None
        w = self.winrate
        b = self.breakeven
        z = 1.645  # 95% una cola
        n = math.ceil((z * math.sqrt(b * (1 - b)) / (w - b)) ** 2)
        return max(n, 30)

    def desglose(self, campo: str) -> list[Desglose]:
        """Agrupa resultados por un atributo del trade (confianza, decision...)."""
        grupos: dict[str, Desglose] = {}
        for t in self.trades:
            k = str(getattr(t, campo))
            g = grupos.setdefault(k, Desglose(k, 0, 0, 0, 0, 0.0))
            g.n += 1
            g.pnl += t.pnl
            if t.resultado is Resultado.WIN:
                g.wins += 1
            elif t.resultado is Resultado.LOSS:
                g.losses += 1
            else:
                g.ties += 1
        return sorted(grupos.values(), key=lambda g: -g.n)

    def to_dict(self) -> dict:
        dd_abs, dd_rel = self.drawdown
        mejor, peor = self.rachas
        return {
            "symbol": self.symbol,
            "estrategia": self.estrategia,
            "payout": self.payout,
            "velas_evaluadas": self.velas_evaluadas,
            "senales_emitidas": self.senales_emitidas,
            "operaciones": self.n,
            "wins": self.wins,
            "losses": self.losses,
            "ties": self.ties,
            "winrate": round(self.winrate, 4),
            "breakeven": round(self.breakeven, 4),
            "edge": round(self.edge, 4),
            "p_value": round(self.p_value, 5),
            "ic95": [round(x, 4) for x in self.ic95],
            "significativo": self.significativo,
            "esperanza_por_operacion": round(self.esperanza_por_operacion, 4),
            "balance_inicial": round(self.balance_inicial, 2),
            "balance_final": round(self.balance_final, 2),
            "pnl": round(self.pnl, 2),
            "retorno": round(self.retorno, 4),
            "profit_factor": (None if math.isinf(self.profit_factor) else round(self.profit_factor, 3)),
            "max_drawdown_abs": round(dd_abs, 2),
            "max_drawdown_rel": round(dd_rel, 4),
            "racha_ganadora": mejor,
            "racha_perdedora": peor,
            "tasa_actividad": round(self.tasa_actividad, 4),
            "vetos": dict(sorted(self.vetos.items(), key=lambda kv: -kv[1])),
            "kill_switch": self.kill_switch,
            "motivo_kill": self.motivo_kill,
        }
