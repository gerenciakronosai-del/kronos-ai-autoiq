"""Generador de series sinteticas para pruebas y demos reproducibles.

No pretende imitar un mercado real: sirve para ejercitar el pipeline completo
sin depender de un broker ni de ficheros externos, y para que los tests sean
deterministas via `seed`.

El proceso alterna regimenes (tendencia alcista, bajista y rango) con
probabilidad de conmutacion fija. Cada vela se construye a partir de varios
sub-ticks, de modo que el OHLC resultante siempre es coherente.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from kronos.core.candle import Candle, Series


@dataclass(slots=True)
class SyntheticParams:
    n: int = 3000
    precio_inicial: float = 1.1000
    timeframe: int = 60
    ts_inicial: int = 1_700_000_000
    # Calibrado para que el ATR resultante (~0.013% del precio) se parezca al de
    # un par FX mayor en velas de 1 minuto, y los umbrales por defecto de la
    # estrategia tengan sentido sobre esta serie.
    vol_base: float = 0.000025         # desviacion por tick
    ticks_por_vela: int = 12
    prob_cambio_regimen: float = 0.01
    deriva_tendencia: float = 0.000008  # deriva por tick en regimen de tendencia
    decimales: int = 5

    def __post_init__(self) -> None:
        if self.n < 1:
            raise ValueError("n debe ser >= 1")
        if self.ticks_por_vela < 2:
            raise ValueError("ticks_por_vela debe ser >= 2")
        if self.vol_base <= 0:
            raise ValueError("vol_base debe ser > 0")


_REGIMENES = ("alcista", "bajista", "rango")


def generate(params: SyntheticParams | None = None, seed: int | None = 42,
             symbol: str = "SYNTH/USD") -> Series:
    p = params or SyntheticParams()
    rng = random.Random(seed)

    precio = p.precio_inicial
    regimen = rng.choice(_REGIMENES)
    velas: list[Candle] = []

    for k in range(p.n):
        if rng.random() < p.prob_cambio_regimen:
            regimen = rng.choice(_REGIMENES)

        if regimen == "alcista":
            deriva, vol = p.deriva_tendencia, p.vol_base
        elif regimen == "bajista":
            deriva, vol = -p.deriva_tendencia, p.vol_base
        else:
            deriva, vol = 0.0, p.vol_base * 0.55

        ticks: list[float] = []
        for _ in range(p.ticks_por_vela):
            precio *= 1.0 + deriva + rng.gauss(0.0, vol)
            precio = max(precio, 1e-6)
            ticks.append(round(precio, p.decimales))

        o, c = ticks[0], ticks[-1]
        velas.append(
            Candle(
                ts=p.ts_inicial + k * p.timeframe,
                open=o,
                high=max(ticks),
                low=min(ticks),
                close=c,
                volume=round(rng.uniform(50, 500), 2),
            )
        )

    return Series(velas, symbol=symbol, timeframe=p.timeframe)
