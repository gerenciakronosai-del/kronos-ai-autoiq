"""Contrato comun de los brokers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional

from kronos.strategy.base import Decision


class TipoCuenta(StrEnum):
    DEMO = "DEMO"
    REAL = "REAL"


class EstadoOrden(StrEnum):
    ABIERTA = "ABIERTA"
    GANADA = "GANADA"
    PERDIDA = "PERDIDA"
    EMPATE = "EMPATE"
    RECHAZADA = "RECHAZADA"


@dataclass(slots=True)
class Orden:
    id: str
    symbol: str
    direccion: Decision
    stake: float
    precio_entrada: float
    ts_apertura: int
    expiracion_seg: int
    estado: EstadoOrden = EstadoOrden.ABIERTA
    precio_salida: Optional[float] = None
    ts_cierre: Optional[int] = None
    pnl: float = 0.0
    detalle: str = ""

    @property
    def cerrada(self) -> bool:
        return self.estado is not EstadoOrden.ABIERTA


class Broker(ABC):
    """Interfaz minima que necesita el ejecutor."""

    tipo_cuenta: TipoCuenta = TipoCuenta.DEMO

    @abstractmethod
    def conectar(self) -> None: ...

    @abstractmethod
    def balance(self) -> float: ...

    @abstractmethod
    def payout(self, symbol: str, expiracion_seg: int) -> float: ...

    @abstractmethod
    def comprar(self, symbol: str, direccion: Decision, stake: float,
                expiracion_seg: int) -> Orden: ...

    @abstractmethod
    def estado_orden(self, orden: Orden) -> Orden: ...

    @abstractmethod
    def liquidar(self, symbol: str, precio: float, ts: int) -> list[Orden]:
        """Cierra lo que haya vencido y devuelve las ordenes recien cerradas.

        El broker simulado necesita `precio` para decidir el resultado; uno real
        lo ignora y pregunta al servidor. Esta firma comun permite que el motor
        no sepa contra cual de los dos esta operando.
        """

    @abstractmethod
    def cerrar(self) -> None: ...

    def __enter__(self):
        self.conectar()
        return self

    def __exit__(self, *exc) -> None:
        self.cerrar()
