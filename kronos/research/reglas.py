"""Estrategias declarativas: reglas como datos, no como codigo.

El catalogo de `hipotesis.py` esta escrito en Python, asi que ampliarlo exige
tocar el repositorio. Este modulo permite describir una estrategia como una
estructura de datos —serializable a JSON, construible desde una interfaz— y
convertirla en la misma lista de senales (+1/-1/0) que consume el resto del
sistema.

Eso es lo que separa una herramienta personal de una plataforma: el usuario
define la estrategia, no el programador.

## Lo que este modulo NO relaja

Sigue siendo imposible mirar al futuro. Los canales se calculan con los
indicadores de `core.indicators`, que ya garantizan que el valor en `i` solo
depende de datos `<= i`, y los operadores de cruce solo miran `i-1` e `i`. No
hay forma de expresar una condicion que lea hacia delante, porque el vocabulario
no la contiene.

## Conflictos

Si dos reglas apuntan en direcciones opuestas en la misma vela, el resultado es
0 (no operar). Es la misma logica de veto del motor de confluencia: ante
informacion contradictoria no se opera, no se vota a ver quien gana.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from kronos.core import indicators as ind
from kronos.core.candle import Series

Num = Optional[float]

# --------------------------------------------------------------------- #
# Canales: magnitudes comparables extraidas de la serie
# --------------------------------------------------------------------- #
# Cada canal devuelve una lista del mismo largo que la serie, con None en el
# calentamiento. El periodo por defecto es el convencional de cada indicador.

def _rsi(s: Series, p: int) -> list[Num]:
    return ind.rsi(s.closes, p)


def _adx(s: Series, p: int) -> list[Num]:
    return ind.adx(s.highs, s.lows, s.closes, p).adx


def _di_spread(s: Series, p: int) -> list[Num]:
    """+DI menos -DI: signo y fuerza de la direccion, en un solo numero."""
    a = ind.adx(s.highs, s.lows, s.closes, p)
    return [None if (x is None or y is None) else x - y
            for x, y in zip(a.plus_di, a.minus_di)]


def _percent_b(s: Series, p: int) -> list[Num]:
    """Posicion dentro del canal de Bollinger: 0 banda baja, 1 banda alta."""
    return ind.bollinger(s.closes, p, 2.0).percent_b


def _ancho_bb(s: Series, p: int) -> list[Num]:
    """Ancho del canal relativo al precio: mide compresion/expansion."""
    bb = ind.bollinger(s.closes, p, 2.0)
    return [None if (w is None or not c) else w / c
            for w, c in zip(bb.width, s.closes)]


def _macd_hist(s: Series, p: int) -> list[Num]:
    """Histograma normalizado por precio, para que sea comparable entre activos."""
    m = ind.macd(s.closes)
    return [None if (h is None or not c) else h / c
            for h, c in zip(m.histogram, s.closes)]


def _estocastico(s: Series, p: int) -> list[Num]:
    return ind.stochastic(s.highs, s.lows, s.closes, p).k


def _atr_pct(s: Series, p: int) -> list[Num]:
    """Volatilidad como fraccion del precio."""
    a = ind.atr(s.highs, s.lows, s.closes, p)
    return [None if (v is None or not c) else v / c for v, c in zip(a, s.closes)]


def _dist_ema(s: Series, p: int) -> list[Num]:
    """Separacion del precio respecto a su EMA, en fraccion. Signo = por encima."""
    e = ind.ema(s.closes, p)
    return [None if (v is None or not v) else (c - v) / v
            for v, c in zip(e, s.closes)]


def _cuerpo(s: Series, p: int) -> list[Num]:
    """Cuerpo de la vela como fraccion del precio. Signo = alcista/bajista."""
    return [None if not v.close else (v.close - v.open) / v.close for v in s]


def _rango_atr(s: Series, p: int) -> list[Num]:
    """Rango de la vela en multiplos de ATR: detecta velas anomalas."""
    a = ind.atr(s.highs, s.lows, s.closes, p)
    return [None if (v is None or v <= 0) else vela.range / v
            for v, vela in zip(a, s)]


@dataclass(frozen=True, slots=True)
class Canal:
    """Un canal disponible, con su periodo por defecto y su rango tipico."""

    nombre: str
    calcular: Callable[[Series, int], list[Num]]
    periodo: int
    descripcion: str
    tipico: tuple[float, float]


CANALES: dict[str, Canal] = {c.nombre: c for c in (
    Canal("rsi", _rsi, 14, "Fuerza relativa (0-100)", (0.0, 100.0)),
    Canal("adx", _adx, 14, "Fuerza de tendencia (0-100)", (0.0, 100.0)),
    Canal("di_spread", _di_spread, 14, "+DI menos -DI: direccion", (-40.0, 40.0)),
    Canal("percent_b", _percent_b, 20, "Posicion en el canal de Bollinger", (0.0, 1.0)),
    Canal("ancho_bb", _ancho_bb, 20, "Ancho del canal / precio", (0.0, 0.05)),
    Canal("macd_hist", _macd_hist, 12, "Histograma MACD / precio", (-0.01, 0.01)),
    Canal("estocastico", _estocastico, 14, "Estocastico %K (0-100)", (0.0, 100.0)),
    Canal("atr_pct", _atr_pct, 14, "Volatilidad / precio", (0.0, 0.05)),
    Canal("dist_ema", _dist_ema, 21, "Distancia del precio a su EMA", (-0.05, 0.05)),
    Canal("cuerpo", _cuerpo, 1, "Cuerpo de la vela / precio", (-0.03, 0.03)),
    Canal("rango_atr", _rango_atr, 14, "Rango de la vela en multiplos de ATR", (0.0, 5.0)),
)}


# --------------------------------------------------------------------- #
# Operadores
# --------------------------------------------------------------------- #
# Los de cruce miran i-1 e i. Nunca mas alla, y nunca hacia delante.

OPERADORES: dict[str, str] = {
    "<": "menor que",
    ">": "mayor que",
    "<=": "menor o igual que",
    ">=": "mayor o igual que",
    "cruza_arriba": "cruza el nivel de abajo hacia arriba",
    "cruza_abajo": "cruza el nivel de arriba hacia abajo",
}

_DE_CRUCE = frozenset(("cruza_arriba", "cruza_abajo"))


@dataclass(frozen=True, slots=True)
class Condicion:
    """Una comparacion sobre un canal. El ladrillo de toda regla."""

    canal: str
    operador: str
    valor: float
    periodo: Optional[int] = None   # None = el periodo por defecto del canal

    def __post_init__(self) -> None:
        if self.canal not in CANALES:
            raise ValueError(
                f"canal desconocido {self.canal!r}; disponibles: "
                + ", ".join(sorted(CANALES))
            )
        if self.operador not in OPERADORES:
            raise ValueError(
                f"operador desconocido {self.operador!r}; disponibles: "
                + ", ".join(OPERADORES)
            )
        if self.periodo is not None and self.periodo < 1:
            raise ValueError(f"periodo debe ser >= 1, recibido {self.periodo}")

    @property
    def periodo_efectivo(self) -> int:
        return self.periodo if self.periodo is not None else CANALES[self.canal].periodo

    def describir(self) -> str:
        p = self.periodo_efectivo
        sufijo = "" if p == CANALES[self.canal].periodo else f"({p})"
        return f"{self.canal}{sufijo} {self.operador} {self.valor:g}"

    def a_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"canal": self.canal, "operador": self.operador,
                             "valor": self.valor}
        if self.periodo is not None:
            d["periodo"] = self.periodo
        return d


@dataclass(frozen=True, slots=True)
class Regla:
    """Condiciones que deben cumplirse A LA VEZ para emitir una direccion."""

    condiciones: tuple[Condicion, ...]
    direccion: int      # +1 CALL, -1 PUT

    def __post_init__(self) -> None:
        if not self.condiciones:
            raise ValueError("una regla necesita al menos una condicion")
        if self.direccion not in (1, -1):
            raise ValueError(f"direccion debe ser 1 o -1, recibido {self.direccion}")

    def describir(self) -> str:
        que = " Y ".join(c.describir() for c in self.condiciones)
        return f"SI {que} -> {'CALL' if self.direccion > 0 else 'PUT'}"

    def a_dict(self) -> dict[str, Any]:
        return {"condiciones": [c.a_dict() for c in self.condiciones],
                "direccion": self.direccion}


@dataclass(frozen=True, slots=True)
class EstrategiaDeclarativa:
    """Conjunto de reglas evaluables sobre una serie."""

    nombre: str
    reglas: tuple[Regla, ...]

    def __post_init__(self) -> None:
        if not self.reglas:
            raise ValueError("una estrategia necesita al menos una regla")

    def describir(self) -> str:
        return "\n".join([self.nombre] + [f"  {r.describir()}" for r in self.reglas])

    def a_dict(self) -> dict[str, Any]:
        return {"nombre": self.nombre, "reglas": [r.a_dict() for r in self.reglas]}

    def a_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.a_dict(), indent=indent, ensure_ascii=True)

    @classmethod
    def desde_dict(cls, d: dict[str, Any]) -> "EstrategiaDeclarativa":
        try:
            reglas = tuple(
                Regla(
                    condiciones=tuple(Condicion(**c) for c in r["condiciones"]),
                    direccion=int(r["direccion"]),
                )
                for r in d["reglas"]
            )
        except (KeyError, TypeError) as e:
            raise ValueError(f"estructura de estrategia invalida: {e}") from e
        return cls(nombre=str(d.get("nombre", "sin nombre")), reglas=reglas)

    @classmethod
    def desde_json(cls, texto: str) -> "EstrategiaDeclarativa":
        try:
            d = json.loads(texto)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON invalido: {e}") from e
        if not isinstance(d, dict):
            raise ValueError("la estrategia debe ser un objeto JSON")
        return cls.desde_dict(d)

    def senales(self, series: Series) -> list[int]:
        return senales(series, self)


# --------------------------------------------------------------------- #
# Evaluacion
# --------------------------------------------------------------------- #
def _valores(series: Series, condiciones: Sequence[Condicion]) -> dict[tuple[str, int], list[Num]]:
    """Calcula cada canal una sola vez, aunque lo usen varias condiciones."""
    cache: dict[tuple[str, int], list[Num]] = {}
    for c in condiciones:
        clave = (c.canal, c.periodo_efectivo)
        if clave not in cache:
            cache[clave] = CANALES[c.canal].calcular(series, c.periodo_efectivo)
    return cache


def _cumple(vals: list[Num], i: int, cond: Condicion) -> bool:
    v = vals[i]
    if v is None:
        return False
    op, ref = cond.operador, cond.valor
    if op == "<":
        return v < ref
    if op == ">":
        return v > ref
    if op == "<=":
        return v <= ref
    if op == ">=":
        return v >= ref
    # Los cruces necesitan la vela anterior; en i=0 no hay con que comparar.
    if i == 0:
        return False
    prev = vals[i - 1]
    if prev is None:
        return False
    if op == "cruza_arriba":
        return prev <= ref < v
    return prev >= ref > v      # cruza_abajo


def senales(series: Series, estrategia: EstrategiaDeclarativa) -> list[int]:
    """Convierte la estrategia en la lista +1/-1/0 que consume el backtest.

    Si dos reglas apuntan en sentidos opuestos en la misma vela, la vela no
    opera. Ante contradiccion, no operar: es la regla del resto del sistema.
    """
    n = len(series)
    out = [0] * n
    if n == 0:
        return out

    todas = [c for r in estrategia.reglas for c in r.condiciones]
    cache = _valores(series, todas)

    for i in range(n):
        direccion = 0
        for regla in estrategia.reglas:
            if not all(_cumple(cache[(c.canal, c.periodo_efectivo)], i, c)
                       for c in regla.condiciones):
                continue
            if direccion and direccion != regla.direccion:
                direccion = 0       # conflicto: se descarta la vela entera
                break
            direccion = regla.direccion
        out[i] = direccion
    return out


def catalogo() -> str:
    """Tabla ASCII de canales disponibles, para la ayuda de la CLI y del panel."""
    L = [f"  {'canal':<14}{'periodo':>8}  {'rango tipico':<18}descripcion",
         "  " + "-" * 74]
    for c in CANALES.values():
        rango = f"[{c.tipico[0]:g}, {c.tipico[1]:g}]"
        L.append(f"  {c.nombre:<14}{c.periodo:>8}  {rango:<18}{c.descripcion}")
    return "\n".join(L)
