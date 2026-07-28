"""Barrido de hipotesis: mide edge bruto de muchas señales, rapido.

Un backtest completo tarda minutos porque reconstruye la serie vela a vela y
pasa por gestor de riesgo y broker. Para EXPLORAR eso sobra: lo que interesa es
el poder predictivo crudo de una señal, sin limites de posicion ni sizing que
enmascaren la estadistica. Aqui se evalua sobre arrays, en O(n).

El peligro de un barrido es evidente: si pruebas 50 hipotesis contra los mismos
datos, unas cuantas pareceran ganadoras por puro azar. Con 50 tests al 5% de
significancia esperas 2.5 falsos positivos aunque NINGUNA sirva. Por eso este
modulo nunca reporta un p-valor crudo solo:

* aplica correccion de Bonferroni sobre el numero real de hipotesis probadas,
* separa dentro y fuera de muestra desde el principio,
* y solo llama "superviviente" a lo que pasa AMBOS filtros.

Sin eso, un barrido es una maquina de fabricar estrategias que solo funcionan
en el pasado.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

from kronos.backtest.metrics import binomial_p_value, breakeven_winrate, wilson_interval
from kronos.core.candle import Series

# Una señal: para cada indice devuelve +1 (CALL), -1 (PUT) o 0 (no operar).
GeneradorSenal = Callable[[Series], list[int]]


@dataclass(slots=True)
class Resultado:
    nombre: str
    n: int
    wins: int
    losses: int
    ties: int
    payout: float

    @property
    def decisivas(self) -> int:
        return self.wins + self.losses

    @property
    def winrate(self) -> float:
        return self.wins / self.decisivas if self.decisivas else 0.0

    @property
    def umbral(self) -> float:
        return breakeven_winrate(self.payout)

    @property
    def edge(self) -> float:
        return self.winrate - self.umbral

    @property
    def p_valor(self) -> float:
        return binomial_p_value(self.wins, self.decisivas, self.umbral)

    @property
    def ic95(self) -> tuple[float, float]:
        return wilson_interval(self.wins, self.decisivas)

    @property
    def esperanza(self) -> float:
        return self.winrate * self.payout - (1 - self.winrate)


@dataclass(slots=True)
class Hallazgo:
    """Una hipotesis evaluada dentro y fuera de muestra."""

    nombre: str
    dentro: Resultado
    fuera: Optional[Resultado]
    p_corregido: float = 1.0

    @property
    def superviviente(self) -> bool:
        """Edge positivo y significativo en AMBOS tramos, tras corregir."""
        if self.fuera is None or self.dentro.decisivas < 100 or self.fuera.decisivas < 100:
            return False
        return (
            self.dentro.edge > 0 and self.p_corregido < 0.05
            and self.fuera.edge > 0 and self.fuera.p_valor < 0.05
        )


# --------------------------------------------------------------------- #
# Evaluacion
# --------------------------------------------------------------------- #
def evaluar(series: Series, senales: Sequence[int], expiry: int,
            payout: float = 0.80, nombre: str = "",
            spread_pips: float = 0.0, valor_pip: float = 0.0001) -> Resultado:
    """Cuenta aciertos comparando el cierre de entrada con el de vencimiento.

    Misma convencion que el backtest real: entrada al cierre de la vela que
    genera la señal, liquidacion al cierre de `expiry` velas despues, empate
    (precio identico) devuelve el stake.

    `spread_pips` NO es un detalle opcional. En horizontes de 1-10 minutos el
    movimiento predecible del precio es del orden del spread, asi que una señal
    que parece rentable a spread cero puede ser perdedora a 0.2 pips. Evaluar
    sin spread es la forma mas rapida de fabricar un falso positivo.
    """
    closes = series.closes
    n = len(closes)
    slip = spread_pips * valor_pip
    wins = losses = ties = 0
    for i, s in enumerate(senales):
        if s == 0 or i + expiry >= n:
            continue
        # El spread siempre juega en contra: se entra al lado malo del precio.
        entrada = closes[i] + slip if s > 0 else closes[i] - slip
        diff = closes[i + expiry] - entrada
        if abs(diff) < 1e-12:
            ties += 1
        elif (diff > 0) == (s > 0):
            wins += 1
        else:
            losses += 1
    return Resultado(nombre or "?", wins + losses + ties, wins, losses, ties, payout)


def barrer(series: Series, hipotesis: dict[str, GeneradorSenal], *,
           expiries: Sequence[int] = (1, 3, 5, 10),
           payout: float = 0.80, split: float = 0.6,
           spread_pips: float = 0.5) -> list[Hallazgo]:
    """Evalua cada hipotesis a cada vencimiento, dentro y fuera de muestra.

    El spread por defecto es 0.5 pips, no cero: es el orden de magnitud real en
    EURUSD y omitirlo invalida las conclusiones (ver `evaluar`).
    """
    corte = int(len(series) * split)
    s_dentro, s_fuera = series[:corte], series[corte:]

    hallazgos: list[Hallazgo] = []
    for nombre, generador in hipotesis.items():
        sen_dentro = generador(s_dentro)
        sen_fuera = generador(s_fuera)
        for exp in expiries:
            etiqueta = f"{nombre} @{exp}v"
            hallazgos.append(Hallazgo(
                nombre=etiqueta,
                dentro=evaluar(s_dentro, sen_dentro, exp, payout, etiqueta, spread_pips),
                fuera=evaluar(s_fuera, sen_fuera, exp, payout, etiqueta, spread_pips),
            ))

    # Bonferroni sobre el numero REAL de hipotesis probadas.
    k = max(1, len(hallazgos))
    for h in hallazgos:
        h.p_corregido = min(1.0, h.dentro.p_valor * k)
    return hallazgos


def por_hora(series: Series, senales: Sequence[int], expiry: int,
             payout: float = 0.80) -> list[Resultado]:
    """Desglosa una señal por hora UTC. Las sesiones no se comportan igual."""
    grupos: dict[int, list[int]] = {h: [] for h in range(24)}
    indices: dict[int, list[int]] = {h: [] for h in range(24)}
    for i, c in enumerate(series):
        h = datetime.fromtimestamp(c.ts, tz=timezone.utc).hour
        indices[h].append(i)

    closes = series.closes
    n = len(closes)
    out: list[Resultado] = []
    for h in range(24):
        wins = losses = ties = 0
        for i in indices[h]:
            if i >= len(senales) or senales[i] == 0 or i + expiry >= n:
                continue
            diff = closes[i + expiry] - closes[i]
            if abs(diff) < 1e-12:
                ties += 1
            elif (diff > 0) == (senales[i] > 0):
                wins += 1
            else:
                losses += 1
        if wins + losses:
            out.append(Resultado(f"{h:02d}:00 UTC", wins + losses + ties,
                                 wins, losses, ties, payout))
    return out


def informe(hallazgos: list[Hallazgo], *, top: int = 25) -> str:
    """Tabla ordenada por edge fuera de muestra, con el veredicto al final."""
    k = len(hallazgos)
    ordenados = sorted(hallazgos, key=lambda h: -(h.fuera.edge if h.fuera else -9))
    lineas = [
        "=" * 88,
        f"  BARRIDO DE HIPOTESIS - {k} combinaciones probadas",
        "=" * 88,
        "",
        f"  {'hipotesis':<26}{'N dentro':>9}{'edge in':>9}{'p corr':>9}"
        f"{'N fuera':>9}{'edge out':>10}{'p out':>8}",
        "  " + "-" * 82,
    ]
    for h in ordenados[:top]:
        f = h.fuera
        marca = "  <== SUPERVIVIENTE" if h.superviviente else ""
        lineas.append(
            f"  {h.nombre:<26}{h.dentro.decisivas:>9,}{h.dentro.edge * 100:>8.2f}%"
            f"{h.p_corregido:>9.3f}{f.decisivas if f else 0:>9,}"
            f"{(f.edge * 100 if f else 0):>9.2f}%{(f.p_valor if f else 1):>8.3f}{marca}"
        )

    supervivientes = [h for h in hallazgos if h.superviviente]
    esperados = k * 0.05
    lineas += [
        "",
        "=" * 88,
        f"  VEREDICTO: {len(supervivientes)} superviviente(s) de {k} hipotesis",
        "=" * 88,
    ]
    if supervivientes:
        for h in supervivientes:
            lineas.append(f"  * {h.nombre}: edge fuera de muestra "
                          f"{h.fuera.edge * 100:+.2f}% (p={h.fuera.p_valor:.4f})")
        lineas.append("")
        lineas.append("  Condicion necesaria, no suficiente. Reproduce el resultado en un")
        lineas.append("  tramo temporal distinto antes de darlo por bueno.")
    else:
        lineas.append(f"  Ninguna hipotesis supera el umbral de equilibrio de forma")
        lineas.append(f"  sostenida dentro y fuera de muestra.")
        lineas.append("")
        lineas.append(f"  Con {k} pruebas al 5%, se esperarian ~{esperados:.1f} falsos")
        lineas.append("  positivos por azar. Cero supervivientes tras corregir significa")
        lineas.append("  que no hay senal, no que falte ajustar parametros.")
    lineas.append("=" * 88)
    return "\n".join(lineas)
