"""Contabilidad de tokens y coste de las llamadas a la API.

En un bucle de trading el coste no es un detalle de facturacion: si cada
decision cuesta mas de lo que rinde una operacion media, el sistema pierde
dinero aunque acierte. Por eso el coste se mide en tiempo real y se muestra en
el panel junto al PnL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Precios en USD por millon de tokens (tarifa Claude API, primera parte).
# (entrada, salida)
PRECIOS: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# Multiplicadores sobre el precio de entrada.
MULT_LECTURA_CACHE = 0.10   # releer de cache cuesta ~10%
MULT_ESCRITURA_CACHE = 1.25  # escribirla cuesta ~125% (TTL de 5 min)


@dataclass(slots=True)
class Uso:
    """Desglose de tokens de una llamada."""

    entrada: int = 0
    salida: int = 0
    cache_lectura: int = 0
    cache_escritura: int = 0

    @classmethod
    def desde_respuesta(cls, usage: Any) -> "Uso":
        return cls(
            entrada=getattr(usage, "input_tokens", 0) or 0,
            salida=getattr(usage, "output_tokens", 0) or 0,
            cache_lectura=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_escritura=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )

    @property
    def total(self) -> int:
        return self.entrada + self.salida + self.cache_lectura + self.cache_escritura


def coste_usd(uso: Uso, modelo: str) -> float:
    """Coste de una llamada. Modelo desconocido -> se asume tarifa Opus."""
    p_in, p_out = PRECIOS.get(modelo, PRECIOS["claude-opus-5"])
    return (
        uso.entrada * p_in
        + uso.cache_lectura * p_in * MULT_LECTURA_CACHE
        + uso.cache_escritura * p_in * MULT_ESCRITURA_CACHE
        + uso.salida * p_out
    ) / 1_000_000


@dataclass(slots=True)
class Contador:
    """Acumulado de la sesion."""

    modelo: str = "claude-opus-5"
    llamadas: int = 0
    errores: int = 0
    uso: Uso = field(default_factory=Uso)
    coste_total: float = 0.0
    latencias_ms: list[float] = field(default_factory=list)

    def registrar(self, uso: Uso, latencia_ms: float) -> float:
        coste = coste_usd(uso, self.modelo)
        self.llamadas += 1
        self.uso.entrada += uso.entrada
        self.uso.salida += uso.salida
        self.uso.cache_lectura += uso.cache_lectura
        self.uso.cache_escritura += uso.cache_escritura
        self.coste_total += coste
        self.latencias_ms.append(latencia_ms)
        return coste

    def registrar_error(self) -> None:
        self.errores += 1

    @property
    def coste_medio(self) -> float:
        return self.coste_total / self.llamadas if self.llamadas else 0.0

    @property
    def latencia_media(self) -> float:
        return sum(self.latencias_ms) / len(self.latencias_ms) if self.latencias_ms else 0.0

    @property
    def latencia_p95(self) -> float:
        if not self.latencias_ms:
            return 0.0
        ordenadas = sorted(self.latencias_ms)
        return ordenadas[min(len(ordenadas) - 1, int(len(ordenadas) * 0.95))]

    @property
    def tasa_cache(self) -> float:
        """Fraccion de tokens de entrada servidos desde cache."""
        total_in = self.uso.entrada + self.uso.cache_lectura + self.uso.cache_escritura
        return self.uso.cache_lectura / total_in if total_in else 0.0

    def proyeccion_diaria(self, segundos_por_llamada: float) -> float:
        """Extrapola el coste a 24 h al ritmo indicado."""
        if segundos_por_llamada <= 0 or not self.llamadas:
            return 0.0
        return self.coste_medio * (86400 / segundos_por_llamada)


def estimar_coste_diario(intervalo_seg: float, modelo: str = "claude-opus-5",
                         tokens_entrada: int = 1400, tokens_salida: int = 120,
                         fraccion_cacheada: float = 0.8) -> float:
    """Estimacion a priori, para dimensionar antes de encender el bot."""
    if intervalo_seg <= 0:
        return 0.0
    llamadas = 86400 / intervalo_seg
    cacheados = int(tokens_entrada * fraccion_cacheada)
    uso = Uso(
        entrada=tokens_entrada - cacheados,
        salida=tokens_salida,
        cache_lectura=cacheados,
    )
    return coste_usd(uso, modelo) * llamadas
