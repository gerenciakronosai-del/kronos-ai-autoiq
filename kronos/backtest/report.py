"""Renderizado de resultados de backtest en texto plano (ASCII, seguro en cmd).

El informe termina siempre con un VEREDICTO explicito. La razon es practica:
una tabla de metricas bonita invita a leer solo el "winrate" y desplegar. El
veredicto obliga a mirar el edge y la significancia estadistica.
"""

from __future__ import annotations

import math
from typing import Sequence

from kronos.backtest.metrics import BacktestResult, Trade

ANCHO = 72


def _linea(ch: str = "-") -> str:
    return ch * ANCHO


def _titulo(txt: str) -> str:
    return f"{_linea('=')}\n  {txt}\n{_linea('=')}"


def _fila(etiqueta: str, valor: str, ancho: int = 34) -> str:
    return f"  {etiqueta:<{ancho}} {valor}"


def _pct(x: float, dec: int = 2) -> str:
    return f"{x*100:.{dec}f}%"


def veredicto(r: BacktestResult) -> tuple[str, str]:
    """Devuelve (titulo, explicacion) del veredicto operativo."""
    if r.decisivas < 30:
        return (
            "MUESTRA INSUFICIENTE",
            f"Solo {r.decisivas} operaciones decisivas. Por debajo de 30 cualquier "
            "winrate es ruido. Amplia el historico antes de concluir nada.",
        )
    if r.edge <= 0:
        falta = r.breakeven - r.winrate
        return (
            "NO DESPLEGAR - ESPERANZA NEGATIVA",
            f"El winrate {_pct(r.winrate)} esta {_pct(falta)} por DEBAJO del umbral de "
            f"equilibrio {_pct(r.breakeven)} que exige un payout del {_pct(r.payout,0)}. "
            "Con estos numeros el sistema pierde dinero de forma sostenida por diseno, "
            "no por mala suerte.",
        )
    if r.p_value >= 0.05:
        faltan = r.trades_minimos_necesarios()
        extra = f" Harian falta ~{faltan} operaciones para poder afirmarlo." if faltan else ""
        return (
            "NO CONCLUYENTE",
            f"El edge de {_pct(r.edge)} es positivo pero p={r.p_value:.3f} (>= 0.05): "
            f"con {r.decisivas} operaciones no se distingue del azar.{extra}",
        )
    return (
        "EDGE SIGNIFICATIVO EN ESTA MUESTRA",
        f"Edge {_pct(r.edge)} sobre el umbral, p={r.p_value:.4f}. Es condicion "
        "necesaria pero NO suficiente: valida en datos fuera de muestra y en "
        "cuenta demo antes de arriesgar dinero real.",
    )


def render(r: BacktestResult, *, max_trades: int = 0) -> str:
    dd_abs, dd_rel = r.drawdown
    mejor, peor = r.rachas
    lo, hi = r.ic95
    pf = r.profit_factor
    out: list[str] = []

    out.append(_titulo(f"KRONOS - BACKTEST {r.symbol} | estrategia '{r.estrategia}'"))
    out.append("")
    out.append("  ACTIVIDAD")
    out.append(_fila("Velas evaluadas", f"{r.velas_evaluadas}"))
    out.append(_fila("Senales emitidas por la estrategia", f"{r.senales_emitidas}"))
    out.append(_fila("Operaciones ejecutadas", f"{r.n}"))
    out.append(_fila("Tasa de actividad", f"{_pct(r.tasa_actividad)} de las velas"))
    out.append("")

    out.append("  RESULTADO")
    out.append(_fila("Ganadas / Perdidas / Empates", f"{r.wins} / {r.losses} / {r.ties}"))
    out.append(_fila("Winrate (sobre decisivas)", _pct(r.winrate)))
    out.append(_fila(f"Umbral equilibrio (payout {_pct(r.payout,0)})", _pct(r.breakeven)))
    out.append(_fila("EDGE (winrate - umbral)", f"{_pct(r.edge)}  {'[+]' if r.edge > 0 else '[-]'}"))
    out.append(_fila("IC 95% del winrate", f"[{_pct(lo)}, {_pct(hi)}]"))
    out.append(_fila("p-valor (una cola vs umbral)", f"{r.p_value:.4f}"))
    out.append(_fila("Esperanza por operacion", f"{r.esperanza_por_operacion:+.4f} x stake"))
    out.append("")

    out.append("  CAPITAL")
    out.append(_fila("Balance inicial", f"{r.balance_inicial:,.2f}"))
    out.append(_fila("Balance final", f"{r.balance_final:,.2f}"))
    out.append(_fila("PnL neto", f"{r.pnl:+,.2f}  ({r.retorno*100:+.2f}%)"))
    out.append(_fila("Profit factor", "inf" if math.isinf(pf) else f"{pf:.3f}"))
    out.append(_fila("Max drawdown", f"{dd_abs:,.2f}  ({_pct(dd_rel)})"))
    out.append(_fila("Racha ganadora / perdedora", f"{mejor} / {peor}"))
    if r.kill_switch:
        out.append(_fila("KILL SWITCH", f"ACTIVADO - {r.motivo_kill}"))
    out.append("")

    if r.n:
        out.append("  DESGLOSE POR CONFIANZA")
        out.append(_desglose_tabla(r, "confianza"))
        out.append("")
        out.append("  DESGLOSE POR DIRECCION")
        out.append(_desglose_tabla(r, "decision"))
        out.append("")
        regimenes = r.desglose("regimen")
        if len(regimenes) > 1:
            out.append("  DESGLOSE POR REGIMEN")
            out.append(_desglose_tabla(r, "regimen"))
            out.append("")

    if r.vetos:
        out.append("  VETOS DE GESTION DE RIESGO (senales bloqueadas)")
        total = sum(r.vetos.values())
        for k, v in sorted(r.vetos.items(), key=lambda kv: -kv[1]):
            out.append(_fila(f"  {k}", f"{v} ({v/total*100:.1f}%)"))
        out.append("")

    if max_trades and r.trades:
        out.append(f"  ULTIMAS {min(max_trades, len(r.trades))} OPERACIONES")
        out.append(_trades_tabla(r.trades[-max_trades:]))
        out.append("")

    tit, txt = veredicto(r)
    out.append(_linea("="))
    out.append(f"  VEREDICTO: {tit}")
    out.append(_linea("="))
    for linea in _envolver(txt, ANCHO - 4):
        out.append(f"  {linea}")
    out.append(_linea("="))
    return "\n".join(out)


def _desglose_tabla(r: BacktestResult, campo: str) -> str:
    filas = [f"  {'Grupo':<14}{'N':>6}{'Win':>6}{'Loss':>6}{'Tie':>5}{'Winrate':>10}{'PnL':>12}"]
    filas.append(f"  {'-'*59}")
    for g in r.desglose(campo):
        filas.append(
            f"  {g.etiqueta:<14}{g.n:>6}{g.wins:>6}{g.losses:>6}{g.ties:>5}"
            f"{_pct(g.winrate):>10}{g.pnl:>+12.2f}"
        )
    return "\n".join(filas)


def _trades_tabla(trades: Sequence[Trade]) -> str:
    filas = [f"  {'Entrada':<20}{'Dir':<6}{'Conf':<7}{'Res':<6}{'PnL':>10}{'Balance':>12}"]
    filas.append(f"  {'-'*61}")
    for t in trades:
        from datetime import datetime, timezone
        fecha = datetime.fromtimestamp(t.ts_entrada, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        filas.append(
            f"  {fecha:<20}{t.decision:<6}{t.confianza:<7}{str(t.resultado):<6}"
            f"{t.pnl:>+10.2f}{t.balance_despues:>12.2f}"
        )
    return "\n".join(filas)


def _envolver(texto: str, ancho: int) -> list[str]:
    palabras = texto.split()
    lineas: list[str] = []
    actual = ""
    for p in palabras:
        if len(actual) + len(p) + 1 > ancho:
            lineas.append(actual)
            actual = p
        else:
            actual = f"{actual} {p}".strip()
    if actual:
        lineas.append(actual)
    return lineas
