"""Carga y guardado de series desde CSV/JSON.

Formato CSV esperado (con cabecera, orden de columnas libre):

    timestamp,open,high,low,close,volume

`timestamp` admite epoch en segundos, epoch en milisegundos o ISO-8601.
Las columnas se detectan por nombre con alias habituales, de modo que los
exportados tipicos de MetaTrader / TradingView / IQ Option entran sin editar.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from kronos.core.candle import Candle, Series

_ALIAS = {
    "ts": ("timestamp", "ts", "time", "date", "datetime", "from", "open_time"),
    "open": ("open", "o", "apertura", "op"),
    "high": ("high", "h", "max", "maximo", "hi"),
    "low": ("low", "l", "min", "minimo", "lo"),
    "close": ("close", "c", "cierre", "cl", "price"),
    "volume": ("volume", "vol", "v", "volumen", "tick_volume"),
}


class LoaderError(ValueError):
    """Error de formato en los datos de entrada."""


def _mapear_columnas(cabecera: Sequence[str]) -> dict[str, int]:
    norm = [h.strip().lower().lstrip("﻿") for h in cabecera]
    mapa: dict[str, int] = {}
    for campo, alias in _ALIAS.items():
        for a in alias:
            if a in norm:
                mapa[campo] = norm.index(a)
                break
    faltan = [c for c in ("ts", "open", "high", "low", "close") if c not in mapa]
    if faltan:
        raise LoaderError(
            f"faltan columnas obligatorias {faltan} en la cabecera {list(cabecera)}"
        )
    return mapa


def parse_timestamp(raw: str) -> int:
    """Convierte epoch(s), epoch(ms) o ISO-8601 a epoch en segundos UTC."""
    raw = raw.strip()
    if not raw:
        raise LoaderError("timestamp vacio")
    try:
        v = float(raw)
    except ValueError:
        pass
    else:
        # Referencia: 2023 son ~1.7e9 s, ~1.7e12 ms, ~1.7e15 us.
        if v > 1e14:       # microsegundos
            return int(v / 1e6)
        if v > 1e11:       # milisegundos
            return int(v / 1e3)
        return int(v)
    # Formato compacto de HistData: "20230102 000000". Lleva espacio, asi que no
    # se confunde con un epoch numerico (que ya habria entrado por float()).
    try:
        return int(datetime.strptime(raw, "%Y%m%d %H%M%S").replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        pass
    texto = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(texto)
    except ValueError as e:
        raise LoaderError(f"timestamp no reconocido: {raw!r}") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def load_csv(path: str | Path, symbol: str | None = None, timeframe: int | None = None) -> Series:
    p = Path(path)
    if not p.exists():
        raise LoaderError(f"no existe el fichero {p}")

    with p.open("r", encoding="utf-8-sig", newline="") as fh:
        lector = csv.reader(fh)
        try:
            cabecera = next(lector)
        except StopIteration:
            raise LoaderError(f"{p} esta vacio") from None
        mapa = _mapear_columnas(cabecera)
        idx_vol = mapa.get("volume")

        velas: list[Candle] = []
        for num, fila in enumerate(lector, start=2):
            if not fila or all(not c.strip() for c in fila):
                continue
            try:
                velas.append(
                    Candle(
                        ts=parse_timestamp(fila[mapa["ts"]]),
                        open=float(fila[mapa["open"]]),
                        high=float(fila[mapa["high"]]),
                        low=float(fila[mapa["low"]]),
                        close=float(fila[mapa["close"]]),
                        volume=float(fila[idx_vol]) if idx_vol is not None and fila[idx_vol].strip() else 0.0,
                    )
                )
            except (ValueError, IndexError) as e:
                raise LoaderError(f"{p}:{num} fila invalida ({e})") from e

    if not velas:
        raise LoaderError(f"{p} no contiene velas")
    velas.sort(key=lambda c: c.ts)
    velas = _deduplicar(velas)
    tf = timeframe or _inferir_timeframe(velas)
    return Series(velas, symbol=symbol or p.stem.upper(), timeframe=tf)


def _deduplicar(velas: list[Candle]) -> list[Candle]:
    """Conserva la ultima vela de cada timestamp repetido."""
    out: list[Candle] = []
    for c in velas:
        if out and out[-1].ts == c.ts:
            out[-1] = c
        else:
            out.append(c)
    return out


def _inferir_timeframe(velas: Sequence[Candle]) -> int:
    if len(velas) < 2:
        return 60
    difs: dict[int, int] = {}
    for a, b in zip(velas, velas[1:]):
        d = b.ts - a.ts
        if d > 0:
            difs[d] = difs.get(d, 0) + 1
    return max(difs, key=lambda k: difs[k]) if difs else 60


def save_csv(series: Series, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for c in series:
            w.writerow([c.ts, c.open, c.high, c.low, c.close, c.volume])
    return p


def load_json(path: str | Path, symbol: str | None = None) -> Series:
    """Lee una lista de objetos o de listas [ts, o, h, l, c, v]."""
    p = Path(path)
    datos = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(datos, list) or not datos:
        raise LoaderError(f"{p} debe contener una lista no vacia de velas")

    velas: list[Candle] = []
    for fila in datos:
        if isinstance(fila, dict):
            clave = {k.lower(): v for k, v in fila.items()}
            def get(campo: str):
                for a in _ALIAS[campo]:
                    if a in clave:
                        return clave[a]
                raise LoaderError(f"falta el campo {campo} en {fila}")
            velas.append(Candle(
                ts=parse_timestamp(str(get("ts"))),
                open=float(get("open")), high=float(get("high")),
                low=float(get("low")), close=float(get("close")),
                volume=float(clave.get("volume", 0.0) or 0.0),
            ))
        elif isinstance(fila, (list, tuple)) and len(fila) >= 5:
            velas.append(Candle(
                ts=parse_timestamp(str(fila[0])), open=float(fila[1]), high=float(fila[2]),
                low=float(fila[3]), close=float(fila[4]),
                volume=float(fila[5]) if len(fila) > 5 else 0.0,
            ))
        else:
            raise LoaderError(f"formato de vela no reconocido: {fila!r}")

    velas.sort(key=lambda c: c.ts)
    velas = _deduplicar(velas)
    return Series(velas, symbol=symbol or p.stem.upper(), timeframe=_inferir_timeframe(velas))


# --------------------------------------------------------------------- #
# HistData.com (Generic ASCII M1)
# --------------------------------------------------------------------- #
# Los ficheros vienen sin cabecera, con ';' como separador y la fecha en
# formato compacto:
#
#     20230102 000000;1.06997;1.07012;1.06995;1.07004;0
#
# Dos trampas que cuestan un backtest entero si se pasan por alto:
#
# 1. La marca temporal esta en EST *sin* horario de verano, es decir UTC-5 todo
#    el ano. Si se cargan como UTC, los limites de dia del gestor de riesgo se
#    desplazan cinco horas y las sesiones se parten por la mitad.
# 2. La columna de volumen es siempre 0 en forex. No es un fallo de descarga:
#    en un mercado descentralizado no hay volumen agregado real.

HISTDATA_TZ_OFFSET = -5  # EST sin DST


def _parse_histdata_lineas(lineas: Iterable[str], tz_offset_horas: int,
                           origen: str) -> list[Candle]:
    velas: list[Candle] = []
    desplazamiento = tz_offset_horas * 3600
    for num, linea in enumerate(lineas, start=1):
        linea = linea.strip()
        if not linea:
            continue
        # Se descartan primero las lineas que no empiezan por digito: cabeceras y
        # el texto del informe de huecos que HistData mete en el .txt del zip.
        if not linea[:1].isdigit():
            continue
        campos = linea.split(";") if ";" in linea else linea.split(",")
        if len(campos) < 5:
            raise LoaderError(f"{origen}:{num} se esperaban >=5 campos, hay {len(campos)}")
        try:
            velas.append(Candle(
                ts=parse_timestamp(campos[0]) - desplazamiento,
                open=float(campos[1]), high=float(campos[2]),
                low=float(campos[3]), close=float(campos[4]),
                volume=float(campos[5]) if len(campos) > 5 and campos[5].strip() else 0.0,
            ))
        except (ValueError, IndexError) as e:
            raise LoaderError(f"{origen}:{num} fila invalida ({e})") from e
    return velas


def load_histdata(origenes: str | Path | Sequence[str | Path], symbol: str = "EURUSD",
                  tz_offset_horas: int = HISTDATA_TZ_OFFSET,
                  resample_a: Optional[int] = None,
                  progreso: Optional[Any] = None) -> Series:
    """Carga ficheros M1 de HistData y los normaliza a UTC.

    Acepta un .zip, un .csv, un directorio o una lista de cualquiera de ellos, y
    los fusiona en una sola serie ordenada y sin duplicados.

    `resample_a` reagrupa CADA FICHERO segun se lee, en vez de al final. Con 25
    anos de velas de un minuto (~9 millones) cargarlo todo en memoria pasa del
    gigabyte; reagrupando por fichero el pico es un solo ano. Para estudiar
    horizontes largos no hacen falta los minutos, solo las velas destino.
    """
    import zipfile

    if isinstance(origenes, (str, Path)):
        origenes = [origenes]

    rutas: list[Path] = []
    for o in origenes:
        p = Path(o)
        if p.is_dir():
            rutas.extend(sorted(x for x in p.iterdir()
                                if x.suffix.lower() in (".zip", ".csv", ".txt")))
        elif p.exists():
            rutas.append(p)
        else:
            raise LoaderError(f"no existe {p}")
    if not rutas:
        raise LoaderError("ningun fichero de HistData que procesar")

    velas: list[Candle] = []
    for n_fichero, ruta in enumerate(rutas, start=1):
        del_fichero: list[Candle] = []
        if ruta.suffix.lower() == ".zip":
            with zipfile.ZipFile(ruta) as z:
                # Los zips de HistData traen el CSV de datos y un .txt que es un
                # informe de huecos, no datos. Si hay CSV, el .txt se ignora.
                internos = [n for n in z.namelist() if n.lower().endswith(".csv")]
                if not internos:
                    internos = [n for n in z.namelist() if n.lower().endswith(".txt")]
                if not internos:
                    raise LoaderError(f"{ruta} no contiene ningun CSV")
                for nombre in internos:
                    with z.open(nombre) as fh:
                        texto = fh.read().decode("utf-8", errors="replace")
                    del_fichero.extend(_parse_histdata_lineas(
                        texto.splitlines(), tz_offset_horas, f"{ruta.name}!{nombre}"))
        else:
            del_fichero.extend(_parse_histdata_lineas(
                ruta.read_text(encoding="utf-8", errors="replace").splitlines(),
                tz_offset_horas, ruta.name))

        if resample_a and del_fichero:
            # Reagrupar YA libera la memoria de los minutos de este fichero.
            del_fichero.sort(key=lambda c: c.ts)
            parcial = Series(_deduplicar(del_fichero), symbol=symbol,
                             timeframe=_inferir_timeframe(del_fichero))
            if resample_a > parcial.timeframe:
                del_fichero = list(reagrupar(parcial, resample_a))
        velas.extend(del_fichero)
        if progreso:
            progreso(n_fichero, len(rutas), ruta.name, len(velas))

    if not velas:
        raise LoaderError("los ficheros no contenian ninguna vela")
    velas.sort(key=lambda c: c.ts)
    velas = _deduplicar(velas)
    return Series(velas, symbol=symbol, timeframe=_inferir_timeframe(velas))


def reagrupar(series: Series, timeframe: int) -> Series:
    """Convierte una serie a un timeframe mayor (1 min -> 15 min, 1 h...).

    Es la forma barata de atacar la desigualdad que hunde el corto plazo:

        movimiento predecible  >  spread

    El spread es FIJO en pips, pero el movimiento del precio crece con la raiz
    del tiempo. Pasar de 5 minutos a 1 hora multiplica el movimiento por ~3.5
    sin tocar el coste. Si hay algo de señal, ahi tiene mas margen para asomar.
    """
    if timeframe <= series.timeframe:
        raise ValueError(
            f"el timeframe destino ({timeframe}s) debe ser mayor que el de "
            f"origen ({series.timeframe}s)"
        )
    if timeframe % series.timeframe:
        raise ValueError("el timeframe destino debe ser multiplo del de origen")

    velas: list[Candle] = []
    grupo: list[Candle] = []
    bloque_actual: Optional[int] = None

    for c in series:
        bloque = c.ts - (c.ts % timeframe)
        if bloque_actual is None:
            bloque_actual = bloque
        elif bloque != bloque_actual:
            if grupo:
                velas.append(_fusionar(grupo, bloque_actual))
            grupo = []
            bloque_actual = bloque
        grupo.append(c)

    if grupo and bloque_actual is not None:
        velas.append(_fusionar(grupo, bloque_actual))
    return Series(velas, symbol=series.symbol, timeframe=timeframe)


def _fusionar(grupo: Sequence[Candle], ts: int) -> Candle:
    """Apertura de la primera, cierre de la ultima, extremos del conjunto."""
    return Candle(
        ts=ts,
        open=grupo[0].open,
        high=max(c.high for c in grupo),
        low=min(c.low for c in grupo),
        close=grupo[-1].close,
        volume=sum(c.volume for c in grupo),
    )


def iter_replay(series: Series, ventana: int) -> Iterable[Series]:
    """Reproduce la serie como lo veria un bot en vivo: buffer rodante."""
    for i in range(ventana, len(series) + 1):
        yield series[i - ventana : i]
