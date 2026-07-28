"""Kronos AI - AutoIQ.

Sistema de decision y backtesting para opciones binarias de corto plazo.
Python puro, sin dependencias externas.

El paquete separa deliberadamente cuatro responsabilidades:

    core/      indicadores y estructuras de mercado
    strategy/  reglas de decision deterministas -> Signal
    risk/      limites y sizing, con derecho de veto sobre la estrategia
    backtest/  simulacion honesta y metricas con contraste estadistico
    broker/    ejecucion (papel por defecto, adaptadores reales opcionales)
"""

from kronos.core.candle import Candle, Series
from kronos.strategy.base import Confidence, Decision, Signal, Strategy
from kronos.strategy.confluence import ConfluenceParams, ConfluenceStrategy

__version__ = "1.0.0"

__all__ = [
    "Candle", "Series",
    "Decision", "Confidence", "Signal", "Strategy",
    "ConfluenceStrategy", "ConfluenceParams",
    "__version__",
]
