"""Duelo IA contra reglas: quien acerto mas sobre las mismas velas.

El motor registra las dos decisiones de cada ciclo pero no su resultado: cuando
decide, el futuro todavia no ha ocurrido. Este modulo cierra el circulo — lee el
registro, lo cruza con los precios reales y responde la unica pregunta que
justifica pagar por la API:

    ¿acierta mas la IA que unas reglas deterministas que son gratis?

El corte que mas informa no es el winrate global, sino **los casos en que las
dos discrepan**. Cuando ambas dicen lo mismo, no distingues cual aporta; el
valor de la IA, si existe, esta donde se separa de las reglas.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from kronos.backtest.metrics import binomial_p_value, breakeven_winrate, wilson_interval
from kronos.core.candle import Series


@dataclass(slots=True)
class Marcador:
    """Aciertos de un cerebro sobre un subconjunto de decisiones."""

    nombre: str
    wins: int = 0
    losses: int = 0
    ties: int = 0
    esperas: int = 0
    # Ciclos sin decision de este cerebro (estaba apagado o fallo la llamada).
    # NO es lo mismo que ESPERAR: "no se pronuncio" y "decidio no operar" son
    # cosas distintas y mezclarlas falsea la tasa de abstencion.
    sin_dato: int = 0
    coste_usd: float = 0.0

    @property
    def decisivas(self) -> int:
        return self.wins + self.losses

    @property
    def operadas(self) -> int:
        return self.decisivas + self.ties

    @property
    def winrate(self) -> float:
        return self.wins / self.decisivas if self.decisivas else 0.0

    def edge(self, payout: float) -> float:
        return self.winrate - breakeven_winrate(payout)

    def p_valor(self, payout: float) -> float:
        return binomial_p_value(self.wins, self.decisivas, breakeven_winrate(payout))

    @property
    def ic95(self) -> tuple[float, float]:
        return wilson_interval(self.wins, self.decisivas)


@dataclass(slots=True)
class ResultadoDuelo:
    payout: float
    spread_pips: float
    expiry_velas: int
    total_registros: int = 0
    emparejados: int = 0
    ia: Marcador = field(default_factory=lambda: Marcador("IA"))
    local: Marcador = field(default_factory=lambda: Marcador("local"))
    # Solo los ciclos donde las dos emitieron orden y discreparon.
    ia_en_discrepancia: Marcador = field(default_factory=lambda: Marcador("IA (discrepan)"))
    local_en_discrepancia: Marcador = field(default_factory=lambda: Marcador("local (discrepan)"))
    acuerdos: int = 0
    desacuerdos: int = 0

    @property
    def umbral(self) -> float:
        return breakeven_winrate(self.payout)


def cargar_registro(ruta: str | Path) -> list[dict]:
    """Lee el JSONL del motor, saltando lineas corruptas sin fallar."""
    p = Path(ruta)
    if not p.exists():
        raise FileNotFoundError(f"no existe el registro {p}")
    filas = []
    for linea in p.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea:
            continue
        try:
            filas.append(json.loads(linea))
        except json.JSONDecodeError:
            continue
    return filas


def _acierta(decision: str, entrada: float, salida: float) -> Optional[bool]:
    """None si empate o si no era una orden."""
    if decision not in ("CALL", "PUT"):
        return None
    diff = salida - entrada
    if abs(diff) < 1e-12:
        return None
    return (diff > 0) == (decision == "CALL")


def evaluar(registros: Iterable[dict], series: Series, *, expiry_velas: int = 5,
            payout: float = 0.80, spread_pips: float = 0.5,
            valor_pip: float = 0.0001) -> ResultadoDuelo:
    """Cruza cada decision registrada con el precio real al vencimiento."""
    r = ResultadoDuelo(payout=payout, spread_pips=spread_pips, expiry_velas=expiry_velas)
    indice = {c.ts: i for i, c in enumerate(series)}
    closes = series.closes
    n = len(closes)
    slip = spread_pips * valor_pip

    for fila in registros:
        r.total_registros += 1
        i = indice.get(fila.get("ts_vela"))
        if i is None or i + expiry_velas >= n:
            continue
        r.emparejados += 1
        salida = closes[i + expiry_velas]
        precio = closes[i]

        d_ia = str(fila.get("ia_decision", "-"))
        d_local = str(fila.get("local_decision", "-"))
        r.ia.coste_usd += float(fila.get("ia_coste_usd") or 0.0)

        for decision, marcador in ((d_ia, r.ia), (d_local, r.local)):
            if decision == "ESPERAR":
                marcador.esperas += 1
                continue
            if decision not in ("CALL", "PUT"):
                marcador.sin_dato += 1
                continue
            entrada = precio + slip if decision == "CALL" else precio - slip
            ok = _acierta(decision, entrada, salida)
            if ok is None:
                marcador.ties += 1
            elif ok:
                marcador.wins += 1
            else:
                marcador.losses += 1

        # Discrepancia: ambas emiten orden y apuntan a lados distintos.
        ambas_operan = d_ia in ("CALL", "PUT") and d_local in ("CALL", "PUT")
        if ambas_operan:
            if d_ia == d_local:
                r.acuerdos += 1
            else:
                r.desacuerdos += 1
                for decision, marcador in ((d_ia, r.ia_en_discrepancia),
                                           (d_local, r.local_en_discrepancia)):
                    entrada = precio + slip if decision == "CALL" else precio - slip
                    ok = _acierta(decision, entrada, salida)
                    if ok is None:
                        marcador.ties += 1
                    elif ok:
                        marcador.wins += 1
                    else:
                        marcador.losses += 1
    return r


def informe(r: ResultadoDuelo) -> str:
    ancho = 78
    L = ["=" * ancho,
         "  DUELO: CEREBRO IA CONTRA REGLAS DETERMINISTAS",
         "=" * ancho, ""]
    L.append(f"  Registros leidos: {r.total_registros:,}  |  emparejados con precio: "
             f"{r.emparejados:,}")
    L.append(f"  Vencimiento {r.expiry_velas} velas  |  payout {r.payout * 100:.0f}%  "
             f"|  spread {r.spread_pips:.1f} pips")
    L.append(f"  Umbral de equilibrio: {r.umbral * 100:.2f}%")
    L.append("")

    L.append(f"  {'cerebro':<16}{'ordenes':>9}{'esperar':>9}{'sin dato':>10}"
             f"{'winrate':>10}{'edge':>9}{'p':>8}")
    L.append("  " + "-" * 71)
    for m in (r.ia, r.local):
        wr = f"{m.winrate * 100:.2f}%" if m.decisivas else "-"
        ed = f"{m.edge(r.payout) * 100:+.2f}%" if m.decisivas else "-"
        pv = f"{m.p_valor(r.payout):.3f}" if m.decisivas else "-"
        L.append(f"  {m.nombre:<16}{m.operadas:>9,}{m.esperas:>9,}{m.sin_dato:>10,}"
                 f"{wr:>10}{ed:>9}{pv:>8}")
    L.append("")
    if r.ia.sin_dato:
        L.append(f"  Aviso: {r.ia.sin_dato:,} ciclos sin decision de la IA (estaba apagada")
        L.append("  o fallo la llamada). Esos ciclos no cuentan para la comparativa.")
        L.append("")

    total_comparables = r.acuerdos + r.desacuerdos
    if total_comparables:
        L.append(f"  COINCIDENCIA (solo ciclos donde ambas emiten orden)")
        L.append(f"    De acuerdo:  {r.acuerdos:,} ({r.acuerdos / total_comparables * 100:.1f}%)")
        L.append(f"    Discrepan:   {r.desacuerdos:,} ({r.desacuerdos / total_comparables * 100:.1f}%)")
        L.append("")

    if r.desacuerdos:
        L.append("  QUIEN ACIERTA CUANDO DISCREPAN  (el corte que de verdad informa)")
        L.append(f"  {'cerebro':<24}{'N':>8}{'winrate':>10}")
        L.append("  " + "-" * 42)
        for m in (r.ia_en_discrepancia, r.local_en_discrepancia):
            wr = f"{m.winrate * 100:.2f}%" if m.decisivas else "-"
            L.append(f"  {m.nombre:<24}{m.decisivas:>8,}{wr:>10}")
        L.append("")

    L.append("=" * ancho)
    L.append("  VEREDICTO")
    L.append("=" * ancho)
    for linea in _veredicto(r):
        L.append(f"  {linea}")
    L.append("=" * ancho)
    return "\n".join(L)


def _veredicto(r: ResultadoDuelo) -> list[str]:
    if r.ia.decisivas < 30 or r.local.decisivas < 30:
        return [
            f"MUESTRA INSUFICIENTE. IA {r.ia.decisivas} operaciones decisivas, "
            f"local {r.local.decisivas}.",
            "Por debajo de 30 cualquier diferencia entre los dos es ruido.",
            "Deja el motor corriendo y vuelve a ejecutar este informe.",
        ]

    out = []
    dif = (r.ia.winrate - r.local.winrate) * 100
    if r.desacuerdos >= 30:
        d = (r.ia_en_discrepancia.winrate - r.local_en_discrepancia.winrate) * 100
        if d > 0:
            out.append(f"Cuando discrepan, la IA acierta {d:+.1f} puntos mas que las reglas.")
        else:
            out.append(f"Cuando discrepan, la IA acierta {d:+.1f} puntos que las reglas.")
    out.append(f"Winrate global: IA {r.ia.winrate * 100:.2f}% vs local "
               f"{r.local.winrate * 100:.2f}% ({dif:+.2f} puntos).")

    if r.ia.edge(r.payout) > 0 and r.ia.p_valor(r.payout) < 0.05:
        out.append("La IA supera el umbral de equilibrio con significancia. Reproducelo")
        out.append("en otro tramo temporal antes de darlo por bueno.")
    else:
        out.append(f"La IA NO supera el umbral de equilibrio ({r.umbral * 100:.2f}%).")
        if r.ia.coste_usd > 0:
            coste_op = r.ia.coste_usd / max(1, r.ia.operadas)
            out.append(f"Coste acumulado de la API: ${r.ia.coste_usd:.4f} "
                       f"(${coste_op:.5f} por decision), sin ventaja que lo justifique.")
    return out
