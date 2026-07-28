"""Fuentes de datos de mercado para el motor en vivo.

Todas exponen la misma interfaz, asi que el motor no sabe si esta operando
sobre un historico reproducido, sobre ruido sintetico o sobre un broker real:

    feed.avanzar()   -> True si hay vela nueva desde la ultima llamada
    feed.serie       -> ventana rodante de velas cerradas
"""

from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from collections import deque
from pathlib import Path
from typing import Deque, Optional

from kronos.core.candle import Candle, Series
from kronos.data import loader, synthetic


class Feed(ABC):
    """Fuente de velas con ventana rodante."""

    def __init__(self, symbol: str, timeframe: int, ventana: int = 200):
        self.symbol = symbol
        self.timeframe = timeframe
        self.ventana = ventana
        self._buffer: Deque[Candle] = deque(maxlen=ventana)

    @property
    def serie(self) -> Series:
        return Series.unchecked(tuple(self._buffer), self.symbol, self.timeframe)

    @property
    def precio(self) -> Optional[float]:
        return self._buffer[-1].close if self._buffer else None

    @property
    def listo(self) -> bool:
        return len(self._buffer) >= 60

    @abstractmethod
    def avanzar(self) -> bool:
        """Incorpora las velas nuevas. True si el buffer cambio."""

    @abstractmethod
    def descripcion(self) -> str: ...


class FeedReplay(Feed):
    """Reproduce un CSV historico como si llegara en tiempo real.

    `velocidad` es el multiplicador de tiempo: 60 significa que un segundo real
    equivale a un minuto de mercado, de modo que con velas de 1 minuto entra una
    vela nueva por segundo. Con `velocidad=1` avanza a tiempo real.

    Es la forma honesta de probar el bot completo: son precios que ocurrieron de
    verdad, con sus huecos, sus spikes y sus horas muertas.
    """

    def __init__(self, ruta: str | Path, *, velocidad: float = 60.0,
                 ventana: int = 200, symbol: Optional[str] = None,
                 desde: Optional[int] = None, limite: Optional[int] = 20_000):
        serie = loader.load_csv(ruta, symbol=symbol)
        super().__init__(serie.symbol, serie.timeframe, ventana)
        # Un historico de dos anos son ~750.000 velas: cargarlo entero para una
        # sesion en vivo tarda y no aporta. Por defecto se toma solo la cola.
        self._velas = list(serie[-limite:] if limite and len(serie) > limite else serie)
        # Arrancar en 0 obligaria a esperar un minuto real a que se llene el
        # buffer antes de la primera decision. Por defecto se empieza con la
        # ventana ya precargada, como tendria un bot que lleva rato conectado.
        if desde is None:
            desde = min(ventana, max(0, len(self._velas) - 1))
        self._i = max(0, min(desde, len(self._velas) - 1))
        self.velocidad = max(velocidad, 0.01)
        self._t0 = time.monotonic()
        self._precargar()

    def _precargar(self) -> None:
        inicio = max(0, self._i - self.ventana)
        for c in self._velas[inicio : self._i]:
            self._buffer.append(c)

    @property
    def agotado(self) -> bool:
        return self._i >= len(self._velas)

    @property
    def progreso(self) -> float:
        return self._i / len(self._velas) if self._velas else 1.0

    def avanzar(self) -> bool:
        if self.agotado:
            return False
        transcurrido = (time.monotonic() - self._t0) * self.velocidad
        objetivo = int(transcurrido / self.timeframe)
        inicio = self._i
        while self._i < len(self._velas) and (self._i - inicio) < max(1, objetivo - inicio):
            self._buffer.append(self._velas[self._i])
            self._i += 1
            if self._i - inicio > self.ventana:  # evita bucles largos tras una pausa
                break
        return self._i > inicio

    def descripcion(self) -> str:
        return (f"Replay {self.symbol} x{self.velocidad:.0f} "
                f"({self._i}/{len(self._velas)} velas, {self.progreso * 100:.1f}%)")


class FeedIQOption(Feed):
    """Velas en tiempo real del propio broker.

    Es la pieza que hace honesta una prueba en demo: si el bot opera contra IQ
    Option pero decide mirando un CSV, no esta probando nada. Aqui los precios
    que ve la estrategia son los mismos contra los que se liquidan las ordenes.
    """

    def __init__(self, broker, symbol: str = "EURUSD", timeframe: int = 60,
                 ventana: int = 200):
        super().__init__(symbol, timeframe, ventana)
        self.broker = broker
        self._ultimo_ts = 0
        self._fallos = 0
        self.precargar()

    def precargar(self) -> int:
        """Trae la ventana inicial para poder decidir desde el primer ciclo."""
        velas = self._pedir(self.ventana)
        for c in velas:
            self._buffer.append(c)
        if velas:
            self._ultimo_ts = velas[-1].ts
        return len(velas)

    def _pedir(self, cuantas: int) -> list[Candle]:
        api = getattr(self.broker, "_api", None)
        if api is None:
            return []
        try:
            crudas = api.get_candles(self.symbol, self.timeframe, cuantas,
                                     int(time.time()))
        except Exception:
            self._fallos += 1
            return []
        self._fallos = 0

        velas: list[Candle] = []
        for c in crudas or []:
            try:
                # La API usa 'min'/'max' en vez de low/high.
                vela = Candle(
                    ts=int(c["from"]), open=float(c["open"]), high=float(c["max"]),
                    low=float(c["min"]), close=float(c["close"]),
                    volume=float(c.get("volume", 0) or 0),
                )
            except (KeyError, TypeError, ValueError):
                continue
            # Solo velas ya CERRADAS: la ultima suele estar en formacion y su
            # cierre cambia a cada tick, lo que produciria señales fantasma.
            if vela.ts + self.timeframe <= int(time.time()):
                velas.append(vela)
        velas.sort(key=lambda v: v.ts)
        return velas

    def avanzar(self) -> bool:
        nuevas = [v for v in self._pedir(10) if v.ts > self._ultimo_ts]
        if not nuevas:
            return False
        for v in nuevas:
            self._buffer.append(v)
        self._ultimo_ts = nuevas[-1].ts
        return True

    def descripcion(self) -> str:
        estado = "conectado" if getattr(self.broker, "conectado", False) else "SIN CONEXION"
        return f"IQ Option {self.symbol} ({estado}, {len(self._buffer)} velas en buffer)"


class FeedSintetico(Feed):
    """Genera velas nuevas a ritmo real. Util para probar sin ficheros ni broker.

    No reproduce ningun mercado: es un paseo aleatorio con cambios de regimen.
    Sirve para verificar que el pipeline funciona, nunca para validar una
    estrategia.
    """

    def __init__(self, *, symbol: str = "SYNTH/USD", timeframe: int = 60,
                 ventana: int = 200, velocidad: float = 60.0, seed: int = 42,
                 precargar: int = 120):
        super().__init__(symbol, timeframe, ventana)
        self.velocidad = max(velocidad, 0.01)
        self._rng = random.Random(seed)
        self._t0 = time.monotonic()
        self._emitidas = 0
        base = synthetic.generate(
            synthetic.SyntheticParams(n=precargar, timeframe=timeframe), seed=seed,
            symbol=symbol,
        )
        for c in base:
            self._buffer.append(c)
        self._precio = self._buffer[-1].close
        self._ts = self._buffer[-1].ts

    def avanzar(self) -> bool:
        transcurrido = (time.monotonic() - self._t0) * self.velocidad
        objetivo = int(transcurrido / self.timeframe)
        if objetivo <= self._emitidas:
            return False
        for _ in range(min(objetivo - self._emitidas, self.ventana)):
            self._buffer.append(self._generar())
            self._emitidas += 1
        return True

    def _generar(self) -> Candle:
        ticks = []
        for _ in range(12):
            self._precio *= 1 + self._rng.gauss(0, 0.000025)
            ticks.append(round(self._precio, 5))
        self._ts += self.timeframe
        return Candle(
            ts=self._ts, open=ticks[0], high=max(ticks), low=min(ticks),
            close=ticks[-1], volume=round(self._rng.uniform(50, 500), 2),
        )

    def descripcion(self) -> str:
        return f"Sintetico {self.symbol} x{self.velocidad:.0f} ({self._emitidas} velas generadas)"
