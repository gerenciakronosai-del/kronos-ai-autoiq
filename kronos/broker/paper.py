"""Broker simulado: ejecuta ordenes contra una serie de precios, sin dinero real.

Es el broker por defecto de todo el sistema. Permite correr el bot completo
(estrategia + riesgo + ejecucion + registro) exactamente igual que en vivo,
cambiando solo la clase del broker.
"""

from __future__ import annotations

import itertools
from typing import Optional

from kronos.broker.base import Broker, EstadoOrden, Orden, TipoCuenta
from kronos.strategy.base import Decision


class PaperBroker(Broker):
    tipo_cuenta = TipoCuenta.DEMO

    def __init__(self, balance_inicial: float = 1000.0, payout_por_defecto: float = 0.80,
                 payouts: Optional[dict[str, float]] = None,
                 spread_pips: float = 0.5, valor_pip: float = 0.0001):
        if balance_inicial <= 0:
            raise ValueError("balance_inicial debe ser > 0")
        if spread_pips < 0:
            raise ValueError("spread_pips no puede ser negativo")
        self._balance = balance_inicial
        self._payout = payout_por_defecto
        self._payouts = payouts or {}
        # El spread por defecto NO es cero. En horizontes de 1-10 minutos el
        # movimiento predecible del precio es del orden del spread, asi que un
        # broker simulado sin spread produce resultados sistematicamente
        # optimistas: exactamente el autoengaño que este proyecto evita.
        self.spread_pips = spread_pips
        self.valor_pip = valor_pip
        self._precios: dict[str, float] = {}
        self._reloj: int = 0
        self._abiertas: dict[str, Orden] = {}
        self._historial: list[Orden] = []
        self._ids = itertools.count(1)
        self._conectado = False

    # -- alimentacion del simulador -------------------------------------- #
    def marcar_precio(self, symbol: str, precio: float, ts: int) -> list[Orden]:
        """Actualiza el precio y liquida las ordenes que hayan vencido."""
        self._precios[symbol] = precio
        self._reloj = max(self._reloj, ts)
        return self._liquidar_vencidas(symbol, precio, ts)

    def liquidar(self, symbol: str, precio: float, ts: int) -> list[Orden]:
        """Interfaz comun con el broker real. Aqui el precio SI decide."""
        return self.marcar_precio(symbol, precio, ts)

    def _liquidar_vencidas(self, symbol: str, precio: float, ts: int) -> list[Orden]:
        cerradas: list[Orden] = []
        for oid, o in list(self._abiertas.items()):
            if o.symbol != symbol or ts < o.ts_apertura + o.expiracion_seg:
                continue
            diff = precio - o.precio_entrada
            if abs(diff) < 1e-12:
                o.estado, o.pnl = EstadoOrden.EMPATE, 0.0
                self._balance += o.stake
            else:
                acierto = (diff > 0) if o.direccion is Decision.CALL else (diff < 0)
                if acierto:
                    o.estado = EstadoOrden.GANADA
                    o.pnl = o.stake * self.payout(symbol, o.expiracion_seg)
                    self._balance += o.stake + o.pnl
                else:
                    o.estado, o.pnl = EstadoOrden.PERDIDA, -o.stake
            o.precio_salida = precio
            o.ts_cierre = ts
            del self._abiertas[oid]
            self._historial.append(o)
            cerradas.append(o)
        return cerradas

    # -- interfaz Broker -------------------------------------------------- #
    def conectar(self) -> None:
        self._conectado = True

    def cerrar(self) -> None:
        self._conectado = False

    def balance(self) -> float:
        return round(self._balance, 2)

    def payout(self, symbol: str, expiracion_seg: int) -> float:
        return self._payouts.get(symbol, self._payout)

    def comprar(self, symbol: str, direccion: Decision, stake: float,
                expiracion_seg: int) -> Orden:
        if not self._conectado:
            raise RuntimeError("broker no conectado: llama a conectar() primero")
        if not direccion.is_trade:
            raise ValueError("no se puede comprar con decision ESPERAR")
        precio = self._precios.get(symbol)
        if precio is None:
            raise RuntimeError(f"sin precio para {symbol}: llama a marcar_precio() antes")
        if stake <= 0:
            raise ValueError("stake debe ser > 0")
        if stake > self._balance:
            return Orden(
                id="rechazada", symbol=symbol, direccion=direccion, stake=stake,
                precio_entrada=precio, ts_apertura=self._reloj,
                expiracion_seg=expiracion_seg, estado=EstadoOrden.RECHAZADA,
                detalle="balance insuficiente",
            )
        self._balance -= stake
        # Se entra siempre por el lado malo del spread: comprando caro (CALL) o
        # vendiendo barato (PUT).
        deslizamiento = self.spread_pips * self.valor_pip
        entrada = (precio + deslizamiento if direccion is Decision.CALL
                   else precio - deslizamiento)
        o = Orden(
            id=f"paper-{next(self._ids)}", symbol=symbol, direccion=direccion, stake=stake,
            precio_entrada=entrada, ts_apertura=self._reloj, expiracion_seg=expiracion_seg,
        )
        self._abiertas[o.id] = o
        return o

    def estado_orden(self, orden: Orden) -> Orden:
        return self._abiertas.get(orden.id, orden)

    # -- inspeccion ------------------------------------------------------- #
    @property
    def historial(self) -> list[Orden]:
        return list(self._historial)

    @property
    def abiertas(self) -> list[Orden]:
        return list(self._abiertas.values())
