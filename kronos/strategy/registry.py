"""Registro de estrategias, para seleccionarlas por nombre desde config/CLI."""

from __future__ import annotations

from typing import Any, Callable

from kronos.strategy.base import Strategy
from kronos.strategy.confluence import ConfluenceParams, ConfluenceStrategy

_BUILDERS: dict[str, Callable[[dict[str, Any]], Strategy]] = {}


def register(name: str, builder: Callable[[dict[str, Any]], Strategy]) -> None:
    _BUILDERS[name] = builder


def available() -> list[str]:
    return sorted(_BUILDERS)


def build(name: str, params: dict[str, Any] | None = None) -> Strategy:
    if name not in _BUILDERS:
        raise KeyError(f"estrategia desconocida {name!r}; disponibles: {', '.join(available())}")
    return _BUILDERS[name](params or {})


def _build_confluence(params: dict[str, Any]) -> Strategy:
    validos = {f for f in ConfluenceParams.__dataclass_fields__}
    desconocidos = set(params) - validos
    if desconocidos:
        raise KeyError(f"parametros desconocidos para 'confluence': {', '.join(sorted(desconocidos))}")
    return ConfluenceStrategy(ConfluenceParams(**params))


register("confluence", _build_confluence)
