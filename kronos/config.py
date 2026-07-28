"""Configuracion de la aplicacion, cargable desde JSON.

Un unico fichero describe estrategia, riesgo y parametros de simulacion, de modo
que un backtest sea reproducible a partir de el.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from kronos.backtest.engine import BacktestConfig
from kronos.risk.manager import RiskParams
from kronos.strategy.base import Confidence
from kronos.strategy.registry import build as build_strategy


@dataclass(slots=True)
class AppConfig:
    estrategia: str = "confluence"
    parametros_estrategia: dict[str, Any] = field(default_factory=dict)
    riesgo: dict[str, Any] = field(default_factory=dict)
    backtest: dict[str, Any] = field(default_factory=dict)
    symbol: str = "EURUSD"
    timeframe: int = 60

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"no existe la configuracion {p}")
        datos = json.loads(p.read_text(encoding="utf-8"))
        validos = set(cls.__dataclass_fields__)
        desconocidos = set(datos) - validos
        if desconocidos:
            raise KeyError(
                f"claves desconocidas en {p}: {', '.join(sorted(desconocidos))}. "
                f"Validas: {', '.join(sorted(validos))}"
            )
        return cls(**datos)

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        return p

    # -- construccion de objetos ------------------------------------------ #
    def strategy(self):
        return build_strategy(self.estrategia, self.parametros_estrategia)

    def risk_params(self) -> RiskParams:
        datos = dict(self.riesgo)
        if "confianza_minima" in datos:
            datos["confianza_minima"] = Confidence(str(datos["confianza_minima"]).upper())
        desconocidos = set(datos) - set(RiskParams.__dataclass_fields__)
        if desconocidos:
            raise KeyError(f"parametros de riesgo desconocidos: {', '.join(sorted(desconocidos))}")
        return RiskParams(**datos)

    def backtest_config(self) -> BacktestConfig:
        desconocidos = set(self.backtest) - set(BacktestConfig.__dataclass_fields__)
        if desconocidos:
            raise KeyError(f"parametros de backtest desconocidos: {', '.join(sorted(desconocidos))}")
        return BacktestConfig(**self.backtest)


def default_config() -> AppConfig:
    return AppConfig(
        estrategia="confluence",
        parametros_estrategia={},
        riesgo={
            "balance_inicial": 1000.0,
            "riesgo_por_operacion": 0.01,
            "max_perdida_diaria": 0.05,
            "max_operaciones_dia": 20,
            "max_perdidas_seguidas": 3,
            "confianza_minima": "MEDIA",
        },
        backtest={"payout": 0.80, "expiry_velas": 5, "ventana": 150},
    )
