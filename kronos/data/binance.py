"""Descarga de histórico desde la API pública de Binance.

Sin credenciales, sin dependencias: el endpoint de klines es público y se ataca
con `urllib` de la biblioteca estándar. Eso mantiene la promesa del núcleo — se
clona y funciona.

Por qué cripto después de medir forex durante toda una sesión:

* **Volatilidad mucho mayor respecto al spread.** La desigualdad que hunde el
  corto plazo en EURUSD (movimiento predecible < coste de operar) tiene aquí
  bastante más margen.
* **Mercado 24/7.** Ni fines de semana muertos ni instrumentos OTC sintéticos.
* **API oficial y documentada**, no ingeniería inversa.

Nada de esto garantiza que haya señal. Solo cambia el terreno donde buscarla, y
la búsqueda se hace con el mismo rigor: Bonferroni, fuera de muestra y spread.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional

from kronos.core.candle import Candle, Series

BASE = "https://api.binance.com/api/v3/klines"
LIMITE_POR_PETICION = 1000

# Intervalos que acepta la API, en segundos.
INTERVALOS = {
    60: "1m", 180: "3m", 300: "5m", 900: "15m", 1800: "30m",
    3600: "1h", 7200: "2h", 14400: "4h", 21600: "6h", 43200: "12h",
    86400: "1d", 259200: "3d", 604800: "1w",
}


class BinanceError(RuntimeError):
    """Fallo al hablar con la API pública de Binance."""


def _pedir(symbol: str, intervalo: str, limite: int,
           end_ms: Optional[int] = None, timeout: float = 20.0) -> list[list[Any]]:
    params = {"symbol": symbol.upper(), "interval": intervalo, "limit": limite}
    if end_ms is not None:
        params["endTime"] = end_ms
    url = f"{BASE}?{urllib.parse.urlencode(params)}"
    peticion = urllib.request.Request(url, headers={"User-Agent": "kronos/1.0"})
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detalle = e.read().decode("utf-8", errors="replace")[:200]
        raise BinanceError(f"HTTP {e.code} pidiendo {symbol}: {detalle}") from e
    except urllib.error.URLError as e:
        raise BinanceError(f"error de red: {e.reason}") from e
    except (json.JSONDecodeError, ValueError) as e:
        raise BinanceError(f"respuesta no interpretable: {e}") from e


def descargar(symbol: str = "BTCUSDT", timeframe: int = 3600, total: int = 50_000,
              progreso: Optional[Callable[[int, int], None]] = None,
              pausa: float = 0.25) -> Series:
    """Descarga `total` velas hacia atrás, encadenando peticiones.

    Binance sirve 1000 como máximo por llamada, así que se retrocede usando la
    vela más antigua recibida como nuevo `endTime`.
    """
    intervalo = INTERVALOS.get(timeframe)
    if intervalo is None:
        raise BinanceError(
            f"timeframe {timeframe}s no soportado. Validos: "
            + ", ".join(f"{k}s ({v})" for k, v in sorted(INTERVALOS.items()))
        )

    crudas: dict[int, list[Any]] = {}
    end_ms: Optional[int] = None

    while len(crudas) < total:
        bloque = _pedir(symbol, intervalo, min(LIMITE_POR_PETICION, total), end_ms)
        if not bloque:
            break
        nuevas = 0
        for k in bloque:
            try:
                ts_ms = int(k[0])
            except (IndexError, TypeError, ValueError):
                continue
            if ts_ms not in crudas:
                crudas[ts_ms] = k
                nuevas += 1
        if nuevas == 0:  # ya no hay mas historico hacia atras
            break
        end_ms = min(crudas) - 1
        if progreso:
            progreso(len(crudas), total)
        if pausa > 0:
            time.sleep(pausa)  # respetar los limites de la API

    velas: list[Candle] = []
    for ts_ms in sorted(crudas):
        k = crudas[ts_ms]
        try:
            velas.append(Candle(
                ts=int(ts_ms) // 1000, open=float(k[1]), high=float(k[2]),
                low=float(k[3]), close=float(k[4]), volume=float(k[5]),
            ))
        except (IndexError, TypeError, ValueError):
            continue

    if not velas:
        raise BinanceError(f"Binance no devolvio velas utilizables para {symbol}")
    return Series(velas, symbol=symbol.upper(), timeframe=timeframe)


def valor_pip(series: Series) -> float:
    """Unidad de precio equivalente a un 'pip' para este activo.

    En forex un pip es 0.0001 fijo. En cripto no existe tal cosa: BTC a 60.000 y
    ADA a 0.40 no comparten escala. Se usa un punto basico (0.01%) del precio,
    que es lo que hace comparable el coste entre activos.
    """
    return series.closes[-1] * 0.0001 if len(series) else 0.0001
