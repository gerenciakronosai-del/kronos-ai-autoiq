"""Tipos base del motor de decision."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from kronos.core.candle import Series


class Decision(StrEnum):
    CALL = "CALL"
    PUT = "PUT"
    ESPERAR = "ESPERAR"

    @property
    def is_trade(self) -> bool:
        return self is not Decision.ESPERAR


class Confidence(StrEnum):
    ALTA = "ALTA"
    MEDIA = "MEDIA"
    BAJA = "BAJA"

    @property
    def rank(self) -> int:
        return {"BAJA": 1, "MEDIA": 2, "ALTA": 3}[self.value]


class Regime(StrEnum):
    TENDENCIA = "TENDENCIA"
    REVERSION = "REVERSION"
    INDEFINIDO = "INDEFINIDO"


@dataclass(frozen=True, slots=True)
class Vote:
    """Voto individual de un indicador dentro de la confluencia."""

    indicador: str
    direccion: Decision
    detalle: str


@dataclass(slots=True)
class Signal:
    """Resultado de evaluar una estrategia sobre una serie.

    `to_contract()` produce el JSON exacto que consume el ejecutor:
    `{"decision", "confianza", "razon"}`.
    """

    decision: Decision
    confianza: Confidence
    razon: str
    ts: int = 0
    symbol: str = "UNKNOWN"
    regimen: Regime = Regime.INDEFINIDO
    score: int = 0
    votos: list[Vote] = field(default_factory=list)
    contexto: dict[str, Any] = field(default_factory=dict)

    def to_contract(self) -> dict[str, str]:
        """Contrato minimo de salida (el que parsea el script ejecutor)."""
        return {
            "decision": str(self.decision),
            "confianza": str(self.confianza),
            "razon": self.razon,
        }

    def to_json(self, *, full: bool = False, indent: int | None = None) -> str:
        if not full:
            return json.dumps(self.to_contract(), ensure_ascii=False, indent=indent)
        payload: dict[str, Any] = {
            **self.to_contract(),
            "ts": self.ts,
            "symbol": self.symbol,
            "regimen": str(self.regimen),
            "score": self.score,
            "votos": [
                {"indicador": v.indicador, "direccion": str(v.direccion), "detalle": v.detalle}
                for v in self.votos
            ],
            "contexto": {
                k: (round(v, 6) if isinstance(v, float) else v) for k, v in self.contexto.items()
            },
        }
        return json.dumps(payload, ensure_ascii=False, indent=indent)

    @classmethod
    def esperar(cls, razon: str, **kw: Any) -> "Signal":
        kw.setdefault("confianza", Confidence.BAJA)
        return cls(decision=Decision.ESPERAR, razon=razon, **kw)


class Strategy(ABC):
    """Contrato de toda estrategia."""

    name: str = "base"

    @property
    @abstractmethod
    def min_bars(self) -> int:
        """Velas minimas necesarias para emitir una senal fiable."""

    @abstractmethod
    def evaluate(self, series: Series) -> Signal:
        """Evalua la ULTIMA vela cerrada de `series` y devuelve una decision.

        La implementacion nunca debe leer datos posteriores al ultimo indice.
        """

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"
