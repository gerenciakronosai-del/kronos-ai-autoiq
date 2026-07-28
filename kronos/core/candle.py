"""Estructuras de datos de mercado.

Una vela (`Candle`) es inmutable. Una `Series` es una secuencia ordenada de velas
con accesos vectorizados perezosos (`closes`, `highs`, ...) cacheados.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cached_property
from typing import Iterable, Iterator, Sequence


@dataclass(frozen=True, slots=True)
class Candle:
    """Vela OHLCV. `ts` es epoch en segundos (UTC)."""

    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError(f"vela invalida en ts={self.ts}: high {self.high} < low {self.low}")
        hi = max(self.open, self.close)
        lo = min(self.open, self.close)
        if self.high < hi - 1e-12 or self.low > lo + 1e-12:
            raise ValueError(
                f"vela invalida en ts={self.ts}: cuerpo [{lo}, {hi}] fuera de rango [{self.low}, {self.high}]"
            )

    @property
    def dt(self) -> datetime:
        return datetime.fromtimestamp(self.ts, tz=timezone.utc)

    @property
    def body(self) -> float:
        return self.close - self.open

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def is_bull(self) -> bool:
        return self.close > self.open

    @property
    def typical(self) -> float:
        return (self.high + self.low + self.close) / 3.0


class Series(Sequence[Candle]):
    """Secuencia ordenada de velas de un activo/timeframe."""

    __slots__ = ("_candles", "symbol", "timeframe", "__dict__")

    def __init__(self, candles: Iterable[Candle], symbol: str = "UNKNOWN", timeframe: int = 60,
                 *, _validar: bool = True):
        self._candles: tuple[Candle, ...] = tuple(candles)
        self.symbol = symbol
        self.timeframe = timeframe
        if _validar:
            for a, b in zip(self._candles, self._candles[1:]):
                if b.ts <= a.ts:
                    raise ValueError(f"velas desordenadas o duplicadas: ts {a.ts} -> {b.ts}")

    @classmethod
    def unchecked(cls, candles: Iterable[Candle], symbol: str = "UNKNOWN",
                  timeframe: int = 60) -> "Series":
        """Construye sin revalidar el orden. Solo para sub-series de una ya valida.

        El backtest crea una sub-serie por vela; revalidar seria O(n^2) inutil.
        """
        return cls(candles, symbol, timeframe, _validar=False)

    def __len__(self) -> int:
        return len(self._candles)

    def __iter__(self) -> Iterator[Candle]:
        return iter(self._candles)

    def __getitem__(self, i):  # type: ignore[override]
        if isinstance(i, slice):
            return Series.unchecked(self._candles[i], self.symbol, self.timeframe)
        return self._candles[i]

    def __repr__(self) -> str:
        return f"<Series {self.symbol} tf={self.timeframe}s n={len(self)}>"

    @cached_property
    def opens(self) -> list[float]:
        return [c.open for c in self._candles]

    @cached_property
    def highs(self) -> list[float]:
        return [c.high for c in self._candles]

    @cached_property
    def lows(self) -> list[float]:
        return [c.low for c in self._candles]

    @cached_property
    def closes(self) -> list[float]:
        return [c.close for c in self._candles]

    @cached_property
    def volumes(self) -> list[float]:
        return [c.volume for c in self._candles]

    @cached_property
    def timestamps(self) -> list[int]:
        return [c.ts for c in self._candles]

    def tail(self, n: int) -> "Series":
        return self[-n:] if n < len(self) else self
