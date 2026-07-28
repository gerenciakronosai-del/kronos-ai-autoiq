"""Gestion de riesgo: sizing y cortacircuitos.

Regla de diseno: el gestor de riesgo tiene DERECHO DE VETO sobre la estrategia.
Una senal valida puede ser rechazada aqui y eso no es un error, es el
funcionamiento normal del sistema.

No se implementa martingala ni ninguna progresion tras perdida. En un
instrumento con esperanza negativa, doblar la apuesta no mejora la esperanza:
solo concentra toda la ruina en una unica racha. El sizing es plano o fraccion
fija del balance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional

from kronos.strategy.base import Confidence, Signal


class Veto(StrEnum):
    OK = "OK"
    KILL_SWITCH = "KILL_SWITCH"
    PERDIDA_DIARIA = "PERDIDA_DIARIA"
    GANANCIA_DIARIA = "GANANCIA_DIARIA"
    MAX_OPERACIONES = "MAX_OPERACIONES"
    RACHA_PERDEDORA = "RACHA_PERDEDORA"
    ENFRIAMIENTO = "ENFRIAMIENTO"
    BALANCE_MINIMO = "BALANCE_MINIMO"
    CONFIANZA_BAJA = "CONFIANZA_BAJA"
    POSICION_ABIERTA = "POSICION_ABIERTA"


@dataclass(slots=True)
class RiskParams:
    balance_inicial: float = 1000.0
    riesgo_por_operacion: float = 0.01     # fraccion del balance por entrada
    stake_fijo: Optional[float] = None      # si se define, ignora riesgo_por_operacion
    stake_minimo: float = 1.0
    max_perdida_diaria: float = 0.05        # 5% del balance de inicio de dia
    max_ganancia_diaria: float = 0.10       # objetivo: parar en verde tambien
    max_operaciones_dia: int = 20
    max_perdidas_seguidas: int = 3
    enfriamiento_velas: int = 10            # velas de pausa tras la racha
    balance_minimo: float = 100.0
    confianza_minima: Confidence = Confidence.MEDIA
    una_posicion_a_la_vez: bool = True

    def __post_init__(self) -> None:
        if not 0 < self.riesgo_por_operacion <= 0.25:
            raise ValueError("riesgo_por_operacion debe estar en (0, 0.25]")
        if self.stake_fijo is not None and self.stake_fijo <= 0:
            raise ValueError("stake_fijo debe ser > 0")
        if self.max_perdidas_seguidas < 1:
            raise ValueError("max_perdidas_seguidas debe ser >= 1")
        if self.balance_inicial <= 0:
            raise ValueError("balance_inicial debe ser > 0")


@dataclass(slots=True)
class RiskDecision:
    permitido: bool
    veto: Veto
    stake: float = 0.0
    motivo: str = ""


@dataclass(slots=True)
class RiskState:
    balance: float = 0.0
    balance_inicio_dia: float = 0.0
    dia: Optional[int] = None
    operaciones_hoy: int = 0
    perdidas_seguidas: int = 0
    ganancias_seguidas: int = 0
    enfriamiento_restante: int = 0
    kill_switch: bool = False
    motivo_kill: str = ""
    posiciones_abiertas: int = 0
    historial_pnl: list[float] = field(default_factory=list)


class RiskManager:
    """Aplica los limites de riesgo antes de cada entrada y tras cada cierre."""

    def __init__(self, params: Optional[RiskParams] = None):
        self.p = params or RiskParams()
        self.state = RiskState(
            balance=self.p.balance_inicial,
            balance_inicio_dia=self.p.balance_inicial,
        )

    # -- ciclo de vida --------------------------------------------------- #
    def on_new_bar(self, ts: int) -> None:
        """Avanza el reloj interno: consume enfriamiento y detecta cambio de dia."""
        dia = ts // 86400
        if self.state.dia is None:
            self.state.dia = dia
            self.state.balance_inicio_dia = self.state.balance
        elif dia != self.state.dia:
            self.state.dia = dia
            self.state.balance_inicio_dia = self.state.balance
            self.state.operaciones_hoy = 0
            self.state.perdidas_seguidas = 0
            self.state.enfriamiento_restante = 0
        if self.state.enfriamiento_restante > 0:
            self.state.enfriamiento_restante -= 1

    def evaluate(self, signal: Signal) -> RiskDecision:
        """Decide si la senal puede ejecutarse y con que stake."""
        s, p = self.state, self.p

        if not signal.decision.is_trade:
            return RiskDecision(False, Veto.OK, 0.0, "la estrategia no emitio orden")
        if s.kill_switch:
            return RiskDecision(False, Veto.KILL_SWITCH, 0.0, s.motivo_kill)
        if s.balance < p.balance_minimo:
            self._activar_kill(f"balance {s.balance:.2f} bajo el minimo {p.balance_minimo:.2f}")
            return RiskDecision(False, Veto.BALANCE_MINIMO, 0.0, s.motivo_kill)
        if p.una_posicion_a_la_vez and s.posiciones_abiertas > 0:
            return RiskDecision(False, Veto.POSICION_ABIERTA, 0.0, "ya hay una posicion abierta")
        if signal.confianza.rank < p.confianza_minima.rank:
            return RiskDecision(
                False, Veto.CONFIANZA_BAJA, 0.0,
                f"confianza {signal.confianza} < minima {p.confianza_minima}",
            )
        if s.enfriamiento_restante > 0:
            return RiskDecision(
                False, Veto.ENFRIAMIENTO, 0.0,
                f"enfriamiento activo, quedan {s.enfriamiento_restante} velas",
            )
        if s.perdidas_seguidas >= p.max_perdidas_seguidas:
            return RiskDecision(
                False, Veto.RACHA_PERDEDORA, 0.0,
                f"{s.perdidas_seguidas} perdidas consecutivas",
            )
        if s.operaciones_hoy >= p.max_operaciones_dia:
            return RiskDecision(
                False, Veto.MAX_OPERACIONES, 0.0,
                f"limite diario de {p.max_operaciones_dia} operaciones alcanzado",
            )

        pnl_dia = s.balance - s.balance_inicio_dia
        if pnl_dia <= -p.max_perdida_diaria * s.balance_inicio_dia:
            return RiskDecision(
                False, Veto.PERDIDA_DIARIA, 0.0,
                f"perdida diaria {pnl_dia:.2f} supera el limite del {p.max_perdida_diaria*100:.0f}%",
            )
        if pnl_dia >= p.max_ganancia_diaria * s.balance_inicio_dia:
            return RiskDecision(
                False, Veto.GANANCIA_DIARIA, 0.0,
                f"objetivo diario del {p.max_ganancia_diaria*100:.0f}% alcanzado",
            )

        stake = self._stake()
        if stake > s.balance:
            return RiskDecision(False, Veto.BALANCE_MINIMO, 0.0, "stake superior al balance")
        return RiskDecision(True, Veto.OK, stake, "dentro de limites")

    def _stake(self) -> float:
        p, s = self.p, self.state
        raw = p.stake_fijo if p.stake_fijo is not None else s.balance * p.riesgo_por_operacion
        return round(max(raw, p.stake_minimo), 2)

    # -- callbacks de ejecucion ------------------------------------------ #
    def on_open(self, stake: float) -> None:
        self.state.posiciones_abiertas += 1
        self.state.operaciones_hoy += 1
        self.state.balance -= stake

    def on_close(self, pnl: float, devolucion: float) -> None:
        """`devolucion` es el efectivo que vuelve a caja (stake+beneficio, o 0)."""
        s = self.state
        s.posiciones_abiertas = max(0, s.posiciones_abiertas - 1)
        s.balance += devolucion
        s.historial_pnl.append(pnl)
        if pnl < 0:
            s.perdidas_seguidas += 1
            s.ganancias_seguidas = 0
            if s.perdidas_seguidas >= self.p.max_perdidas_seguidas:
                s.enfriamiento_restante = self.p.enfriamiento_velas
        elif pnl > 0:
            s.ganancias_seguidas += 1
            s.perdidas_seguidas = 0
        if s.balance < self.p.balance_minimo:
            self._activar_kill(f"balance {s.balance:.2f} bajo el minimo {self.p.balance_minimo:.2f}")

    def _activar_kill(self, motivo: str) -> None:
        self.state.kill_switch = True
        self.state.motivo_kill = motivo

    def reset_racha(self) -> None:
        """Reinicio manual tras enfriamiento (uso operativo, no automatico)."""
        self.state.perdidas_seguidas = 0
        self.state.enfriamiento_restante = 0
