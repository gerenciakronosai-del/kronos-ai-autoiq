"""Motor de backtest walk-forward para opciones binarias.

Garantias de honestidad del simulador, todas cubiertas por tests:

* En la vela `i` la estrategia solo recibe `series[:i+1]`. No existe forma de
  leer el futuro.
* La entrada se ejecuta al CIERRE de la vela que genero la senal, nunca a un
  precio intermedio favorable.
* El vencimiento se liquida al cierre de la vela `i + expiry_velas`.
* Una operacion cuyo vencimiento caeria fuera de los datos no se abre; no se
  inventan resultados al final de la serie.
* El empate (precio identico) devuelve el stake integro, como hacen los brokers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from kronos.backtest.metrics import BacktestResult, Resultado, Trade
from kronos.core.candle import Series
from kronos.risk.manager import RiskManager, RiskParams, Veto
from kronos.strategy.base import Decision, Signal, Strategy


@dataclass(slots=True)
class BacktestConfig:
    payout: float = 0.80            # beneficio sobre el stake si acierta (80%)
    expiry_velas: int = 1           # vencimiento en numero de velas
    # Buffer rodante que ve la estrategia, igual que tendria un bot en vivo.
    # 150 velas dan margen de sobra para que converjan los indicadores de Wilder:
    # medido contra ventana=300 sobre 12.000 velas da resultados identicos en la
    # mitad de tiempo. Subirlo solo cuesta CPU.
    ventana: int = 150
    slippage_pct: float = 0.0       # deslizamiento adverso en la entrada
    verbose: bool = False

    def __post_init__(self) -> None:
        if not 0 < self.payout < 5:
            raise ValueError("payout fuera de rango razonable (0, 5)")
        if self.expiry_velas < 1:
            raise ValueError("expiry_velas debe ser >= 1")
        if self.slippage_pct < 0:
            raise ValueError("slippage_pct no puede ser negativo")


@dataclass(slots=True)
class _Pendiente:
    indice_salida: int
    signal: Signal
    stake: float
    precio_entrada: float
    ts_entrada: int


class Backtester:
    """Reproduce la serie vela a vela aplicando estrategia y gestion de riesgo."""

    def __init__(self, strategy: Strategy, config: Optional[BacktestConfig] = None,
                 risk: Optional[RiskParams] = None):
        self.strategy = strategy
        self.cfg = config or BacktestConfig()
        self.risk_params = risk or RiskParams()

    def run(self, series: Series,
            on_trade: Optional[Callable[[Trade], None]] = None) -> BacktestResult:
        cfg = self.cfg
        rm = RiskManager(self.risk_params)
        ventana = max(cfg.ventana, self.strategy.min_bars)
        n = len(series)

        result = BacktestResult(
            symbol=series.symbol,
            estrategia=self.strategy.name,
            payout=cfg.payout,
            balance_inicial=rm.state.balance,
            balance_final=rm.state.balance,
        )
        result.equity.append(rm.state.balance)

        if n <= self.strategy.min_bars + cfg.expiry_velas:
            return result

        pendiente: Optional[_Pendiente] = None

        for i in range(self.strategy.min_bars, n):
            vela = series[i]

            # 1) Liquidar vencimiento antes de considerar nada nuevo.
            if pendiente is not None and i >= pendiente.indice_salida:
                trade = self._liquidar(pendiente, vela.close, vela.ts, rm, cfg)
                result.trades.append(trade)
                result.equity.append(rm.state.balance)
                if on_trade:
                    on_trade(trade)
                pendiente = None

            rm.on_new_bar(vela.ts)

            if rm.state.kill_switch:
                break
            # 2) Sin espacio para que la operacion venza dentro de los datos.
            if i + cfg.expiry_velas >= n:
                continue

            # 3) La estrategia solo ve el pasado.
            inicio = max(0, i - ventana + 1)
            signal = self.strategy.evaluate(series[inicio : i + 1])
            result.velas_evaluadas += 1

            if not signal.decision.is_trade:
                continue
            result.senales_emitidas += 1

            decision = rm.evaluate(signal)
            if not decision.permitido:
                if decision.veto is not Veto.OK:
                    result.vetos[str(decision.veto)] = result.vetos.get(str(decision.veto), 0) + 1
                continue

            precio = self._precio_entrada(vela.close, signal.decision, cfg.slippage_pct)
            rm.on_open(decision.stake)
            pendiente = _Pendiente(
                indice_salida=i + cfg.expiry_velas,
                signal=signal,
                stake=decision.stake,
                precio_entrada=precio,
                ts_entrada=vela.ts,
            )

        # Una posicion aun abierta al agotarse los datos se descarta y se
        # devuelve el stake: no se puede saber como habria terminado.
        if pendiente is not None:
            rm.state.balance += pendiente.stake
            rm.state.posiciones_abiertas = max(0, rm.state.posiciones_abiertas - 1)

        result.balance_final = rm.state.balance
        result.kill_switch = rm.state.kill_switch
        result.motivo_kill = rm.state.motivo_kill
        result.equity.append(rm.state.balance)
        return result

    # ------------------------------------------------------------------ #
    @staticmethod
    def _precio_entrada(close: float, decision: Decision, slippage: float) -> float:
        """El deslizamiento siempre juega en contra: peor precio de entrada."""
        if slippage <= 0:
            return close
        return close * (1 + slippage) if decision is Decision.CALL else close * (1 - slippage)

    @staticmethod
    def _liquidar(p: _Pendiente, precio_salida: float, ts_salida: int,
                  rm: RiskManager, cfg: BacktestConfig) -> Trade:
        diff = precio_salida - p.precio_entrada
        if abs(diff) < 1e-12:
            resultado, pnl, devolucion = Resultado.TIE, 0.0, p.stake
        else:
            acierto = (diff > 0) if p.signal.decision is Decision.CALL else (diff < 0)
            if acierto:
                resultado = Resultado.WIN
                pnl = p.stake * cfg.payout
                devolucion = p.stake + pnl
            else:
                resultado = Resultado.LOSS
                pnl = -p.stake
                devolucion = 0.0

        rm.on_close(pnl, devolucion)
        return Trade(
            ts_entrada=p.ts_entrada,
            ts_salida=ts_salida,
            symbol=p.signal.symbol,
            decision=str(p.signal.decision),
            confianza=str(p.signal.confianza),
            regimen=str(p.signal.regimen),
            precio_entrada=p.precio_entrada,
            precio_salida=precio_salida,
            stake=p.stake,
            payout=cfg.payout,
            resultado=resultado,
            pnl=pnl,
            balance_despues=rm.state.balance,
            score=p.signal.score,
            razon=p.signal.razon,
        )
