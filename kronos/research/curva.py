"""Operaciones individuales y curva de capital.

Los modulos de evaluacion (`barrido`, `stops`) devuelven agregados: cuantas se
ganaron, cuantas se perdieron, el p-valor. Eso basta para decidir si desplegar,
pero no para entender POR QUE una estrategia se comporta como lo hace.

Aqui se reconstruye la lista de operaciones una por una, con su indice de
entrada y de salida, para poder dibujarlas sobre el precio y acumular la curva
de capital.

## La regla que gobierna este modulo

**Los recuentos tienen que coincidir exactamente con los de los agregados.** Si
el grafico ensenya 40 ganadas y el veredicto dice 38, el grafico esta mintiendo y
es peor que no tenerlo, porque se ve mas convincente. Los tests
`test_coincide_con_el_agregado_*` fijan esa igualdad y son la razon de que la
logica de resolucion este duplicada aqui en vez de refactorizada: replicarla
literalmente y verificar la igualdad es mas seguro que compartirla y confiar.

## Sobre el dibujo de la curva

La curva usa **riesgo fijo por operacion**, nunca fraccion compuesta del capital.
Con esperanza negativa, el interes compuesto dibuja una caida exponencial preciosa
que exagera lo malo; con esperanza positiva, exagera lo bueno. El riesgo fijo
ensenya la realidad estadistica sin el efecto de la progresion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from kronos.core import indicators as ind
from kronos.core.candle import Series


@dataclass(frozen=True, slots=True)
class Operacion:
    """Una operacion cerrada, con todo lo necesario para dibujarla."""

    i_entrada: int
    i_salida: int
    direccion: int          # +1 CALL, -1 PUT
    precio_entrada: float
    precio_salida: float
    resultado_r: float      # ganancia/perdida en unidades de riesgo
    ganada: bool

    @property
    def velas(self) -> int:
        return self.i_salida - self.i_entrada


def operaciones_binarias(series: Series, senales: Sequence[int], *, expiry: int,
                         payout: float = 0.84, spread_pips: float = 0.5,
                         valor_pip: float = 0.0001) -> list[Operacion]:
    """Reproduce `barrido.evaluar` operacion a operacion.

    Los empates (precio identico al vencimiento) devuelven el stake y no se
    incluyen: no son ni ganadas ni perdidas.
    """
    closes = series.closes
    n = len(closes)
    slip = spread_pips * valor_pip
    ops: list[Operacion] = []

    for i, s in enumerate(senales):
        if s == 0 or i + expiry >= n:
            continue
        entrada = closes[i] + slip if s > 0 else closes[i] - slip
        salida = closes[i + expiry]
        diff = salida - entrada
        if abs(diff) < 1e-12:
            continue                    # empate: stake devuelto
        ganada = (diff > 0) == (s > 0)
        ops.append(Operacion(
            i_entrada=i, i_salida=i + expiry, direccion=s,
            precio_entrada=entrada, precio_salida=salida,
            resultado_r=payout if ganada else -1.0, ganada=ganada,
        ))
    return ops


def operaciones_stops(series: Series, senales: Sequence[int], *, rr: float = 2.0,
                      atr_mult: float = 1.5, atr_period: int = 14,
                      max_velas: int = 48, spread_pips: float = 0.5,
                      valor_pip: float = 0.0001) -> list[Operacion]:
    """Reproduce `stops.evaluar_con_stops` operacion a operacion.

    Mantiene la convencion pesimista: si en una vela caben stop y objetivo,
    cuenta STOP. Las que no se resuelven dentro del horizonte se descartan,
    igual que en el agregado.
    """
    closes, highs, lows = series.closes, series.highs, series.lows
    atr = ind.atr(highs, lows, closes, atr_period)
    n = len(closes)
    slip = spread_pips * valor_pip
    ops: list[Operacion] = []

    for i, s in enumerate(senales):
        if s == 0 or atr[i] is None or atr[i] <= 0 or i + 1 >= n:
            continue
        riesgo = atr_mult * atr[i]
        if riesgo <= 0:
            continue

        entrada = closes[i] + slip if s > 0 else closes[i] - slip
        if s > 0:
            stop, objetivo = entrada - riesgo, entrada + riesgo * rr
        else:
            stop, objetivo = entrada + riesgo, entrada - riesgo * rr

        coste = slip / riesgo       # el spread, en unidades de R
        for j in range(i + 1, min(i + 1 + max_velas, n)):
            toca_stop = lows[j] <= stop if s > 0 else highs[j] >= stop
            toca_obj = highs[j] >= objetivo if s > 0 else lows[j] <= objetivo
            if toca_stop:
                ops.append(Operacion(i, j, s, entrada, stop, -1.0 - coste, False))
                break
            if toca_obj:
                ops.append(Operacion(i, j, s, entrada, objetivo, rr - coste, True))
                break
    return ops


def curva_de_capital(operaciones: Sequence[Operacion], *,
                     capital_inicial: float = 1000.0,
                     riesgo_por_operacion: float = 0.01) -> list[float]:
    """Capital acumulado tras cada operacion, con riesgo FIJO.

    `riesgo_por_operacion` es la fraccion del capital INICIAL arriesgada en cada
    una, no del capital vigente. Es deliberado: componer exagera la pendiente en
    ambos sentidos y convierte la curva en una ilustracion del interes compuesto
    en vez de una del rendimiento de la estrategia.
    """
    if capital_inicial <= 0:
        raise ValueError("capital_inicial debe ser > 0")
    if not 0 < riesgo_por_operacion <= 1:
        raise ValueError("riesgo_por_operacion debe estar en (0, 1]")

    stake = capital_inicial * riesgo_por_operacion
    capital = capital_inicial
    curva = [capital_inicial]
    for op in operaciones:
        capital += op.resultado_r * stake
        curva.append(capital)
    return curva


@dataclass(frozen=True, slots=True)
class Racha:
    """La peor secuencia de perdidas y la mayor caida desde un maximo."""

    perdidas_seguidas: int
    max_drawdown: float         # fraccion del pico, en [0, 1]
    pico: float
    valle: float


def analizar_curva(curva: Sequence[float]) -> Racha:
    """Metricas de dolor: lo que decide si una estrategia es operable en la practica.

    Una curva con esperanza positiva y un drawdown del 60% es inoperable: nadie
    aguanta la racha sin desviarse del plan, y desviarse destruye la esperanza.
    """
    if len(curva) < 2:
        return Racha(0, 0.0, curva[0] if curva else 0.0, curva[0] if curva else 0.0)

    pico = curva[0]
    peor_dd = 0.0
    pico_dd = valle_dd = curva[0]
    seguidas = peor_seguidas = 0

    for anterior, actual in zip(curva, curva[1:]):
        if actual < anterior:
            seguidas += 1
            peor_seguidas = max(peor_seguidas, seguidas)
        else:
            seguidas = 0
        pico = max(pico, actual)
        if pico > 0:
            dd = (pico - actual) / pico
            if dd > peor_dd:
                peor_dd, pico_dd, valle_dd = dd, pico, actual

    return Racha(peor_seguidas, peor_dd, pico_dd, valle_dd)


def resumen(operaciones: Sequence[Operacion], curva: Sequence[float]) -> str:
    """Resumen ASCII de la curva, para consola y para el panel."""
    if not operaciones:
        return "  Sin operaciones que resumir."
    r = analizar_curva(curva)
    final = curva[-1]
    inicial = curva[0]
    ganadas = sum(1 for o in operaciones if o.ganada)
    duracion = sum(o.velas for o in operaciones) / len(operaciones)
    return "\n".join([
        f"  Operaciones            {len(operaciones):,} ({ganadas:,} ganadas)",
        f"  Capital                {inicial:,.2f} -> {final:,.2f} "
        f"({(final / inicial - 1) * 100:+.2f}%)",
        f"  Peor caida             {r.max_drawdown * 100:.2f}% "
        f"(de {r.pico:,.2f} a {r.valle:,.2f})",
        f"  Perdidas seguidas      {r.perdidas_seguidas}",
        f"  Duracion media         {duracion:.1f} velas",
    ])
