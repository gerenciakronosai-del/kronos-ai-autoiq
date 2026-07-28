"""Cerebro IA: consulta a la API de Anthropic y devuelve una decision tipada.

Requiere `pip install anthropic` y la variable de entorno `ANTHROPIC_API_KEY`
(o una sesion iniciada con `ant auth login`, que el SDK detecta solo).

Puntos de diseno que importan en un bucle de trading:

* **Salida garantizada.** Se usa `output_config.format` con un esquema JSON, asi
  que la API devuelve JSON valido por construccion. No hay que limpiar bloques
  markdown ni reintentar por texto mal formado.
* **Cache de prompt.** El prompt de sistema va marcado con `cache_control`: es
  identico en cada llamada, asi que a partir de la segunda se cobra ~10% de su
  precio. Con miles de llamadas al dia es la diferencia entre viable e inviable.
* **Timeout agresivo.** Una decision que llega tarde no sirve: la vela ya cerro.
  Mejor fallar rapido y perder el ciclo que bloquear el bucle.
* **Fallo cerrado.** Cualquier error (red, cuota, refusal) devuelve ESPERAR, no
  una excepcion que tumbe el bot ni una decision inventada.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from kronos.core.candle import Series
from kronos.ia.coste import Contador, Uso, coste_usd
from kronos.ia.prompt import ESQUEMA_DECISION, SISTEMA, construir_snapshot
from kronos.strategy.base import Confidence, Decision

MODELO_POR_DEFECTO = "claude-opus-5"


@dataclass(slots=True)
class RespuestaIA:
    """Resultado de una consulta, exito o fallo."""

    decision: Decision
    confianza: Confidence
    razon: str
    ts: int = 0
    modelo: str = MODELO_POR_DEFECTO
    latencia_ms: float = 0.0
    uso: Uso = field(default_factory=Uso)
    coste_usd: float = 0.0
    error: Optional[str] = None
    snapshot: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_contract(self) -> dict[str, str]:
        return {
            "decision": str(self.decision),
            "confianza": str(self.confianza),
            "razon": self.razon,
        }

    @classmethod
    def fallo(cls, motivo: str, **kw: Any) -> "RespuestaIA":
        """Ante cualquier problema, la decision segura es no operar."""
        return cls(
            decision=Decision.ESPERAR,
            confianza=Confidence.BAJA,
            razon=f"Sin decision de la IA: {motivo}",
            error=motivo,
            **kw,
        )


class CerebroNoDisponible(RuntimeError):
    """Falta la libreria o las credenciales."""


class CerebroIA:
    """Cliente fino sobre la API de Anthropic, especializado en este contrato."""

    def __init__(
        self,
        modelo: str = MODELO_POR_DEFECTO,
        *,
        effort: str = "low",
        max_tokens: int = 1024,
        timeout_seg: float = 25.0,
        reintentos: int = 1,
        usar_fallback: bool = True,
        api_key: Optional[str] = None,
    ):
        try:
            import anthropic  # noqa: F401
        except ImportError as e:
            raise CerebroNoDisponible(
                "Falta la libreria oficial. Instalala con: pip install anthropic"
            ) from e

        import anthropic

        self._anthropic = anthropic
        self.modelo = modelo
        self.effort = effort
        self.max_tokens = max_tokens
        self.usar_fallback = usar_fallback
        self.contador = Contador(modelo=modelo)

        try:
            # Sin api_key explicita el SDK resuelve ANTHROPIC_API_KEY, luego
            # ANTHROPIC_AUTH_TOKEN, luego el perfil de `ant auth login`.
            self._client = anthropic.Anthropic(
                **({"api_key": api_key} if api_key else {}),
                timeout=timeout_seg,
                max_retries=reintentos,
            )
        except Exception as e:  # credenciales ausentes o mal formadas
            raise CerebroNoDisponible(f"no se pudo crear el cliente: {e}") from e

    # ------------------------------------------------------------------ #
    def analizar(self, series: Series, *, expiry_velas: int = 5,
                 atr_min_pct: float = 0.00005,
                 atr_max_pct: float = 0.005) -> RespuestaIA:
        """Envia la fotografia del mercado y devuelve la decision del modelo."""
        ts = series[-1].ts if len(series) else 0
        snapshot = construir_snapshot(
            series, atr_min_pct=atr_min_pct, atr_max_pct=atr_max_pct,
            expiry_velas=expiry_velas,
        )
        if snapshot is None:
            return RespuestaIA.fallo("datos insuficientes para el snapshot", ts=ts,
                                     modelo=self.modelo)

        peticion: dict[str, Any] = {
            "model": self.modelo,
            "max_tokens": self.max_tokens,
            # El bloque de sistema se cachea: es identico en cada llamada.
            "system": [
                {"type": "text", "text": SISTEMA, "cache_control": {"type": "ephemeral"}}
            ],
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": ESQUEMA_DECISION},
            },
            "messages": [{"role": "user", "content": snapshot}],
        }

        inicio = time.perf_counter()
        try:
            if self.usar_fallback:
                # Si los clasificadores de seguridad rechazan la peticion, la
                # API la reintenta sola en el modelo de respaldo recomendado.
                respuesta = self._client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                    **peticion,
                )
            else:
                respuesta = self._client.messages.create(**peticion)
        except self._anthropic.RateLimitError:
            self.contador.registrar_error()
            return RespuestaIA.fallo("limite de peticiones alcanzado", ts=ts,
                                     modelo=self.modelo, snapshot=snapshot,
                                     latencia_ms=(time.perf_counter() - inicio) * 1000)
        except self._anthropic.APITimeoutError:
            self.contador.registrar_error()
            return RespuestaIA.fallo("timeout: la respuesta llego tarde", ts=ts,
                                     modelo=self.modelo, snapshot=snapshot,
                                     latencia_ms=(time.perf_counter() - inicio) * 1000)
        except self._anthropic.APIConnectionError:
            self.contador.registrar_error()
            return RespuestaIA.fallo("error de red", ts=ts, modelo=self.modelo,
                                     snapshot=snapshot)
        except self._anthropic.APIStatusError as e:
            self.contador.registrar_error()
            return RespuestaIA.fallo(f"error de API {e.status_code}: {e.message}",
                                     ts=ts, modelo=self.modelo, snapshot=snapshot)
        except Exception as e:  # nunca tumbar el bucle por el cerebro
            self.contador.registrar_error()
            return RespuestaIA.fallo(f"inesperado: {type(e).__name__}: {e}",
                                     ts=ts, modelo=self.modelo, snapshot=snapshot)

        latencia_ms = (time.perf_counter() - inicio) * 1000
        uso = Uso.desde_respuesta(respuesta.usage)
        coste = self.contador.registrar(uso, latencia_ms)
        comun = {
            "ts": ts, "modelo": getattr(respuesta, "model", self.modelo),
            "latencia_ms": latencia_ms, "uso": uso, "coste_usd": coste,
            "snapshot": snapshot,
        }

        # Comprobar el motivo de parada ANTES de leer el contenido: en un
        # rechazo `content` puede venir vacio y `content[0]` reventaria.
        if getattr(respuesta, "stop_reason", None) == "refusal":
            detalle = getattr(respuesta, "stop_details", None)
            categoria = getattr(detalle, "category", None) or "sin categoria"
            return RespuestaIA.fallo(f"peticion rechazada por seguridad ({categoria})", **comun)

        texto = next((b.text for b in respuesta.content if b.type == "text"), None)
        if not texto:
            return RespuestaIA.fallo("respuesta sin contenido de texto", **comun)

        try:
            datos = json.loads(texto)
            decision = Decision(str(datos["decision"]).upper())
            confianza = Confidence(str(datos["confianza"]).upper())
            razon = str(datos["razon"]).strip().replace("\n", " ")
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            return RespuestaIA.fallo(f"respuesta fuera de contrato: {e}", **comun)

        return RespuestaIA(decision=decision, confianza=confianza, razon=razon, **comun)

    # ------------------------------------------------------------------ #
    def prueba_de_conexion(self) -> tuple[bool, str]:
        """Llamada minima para validar credenciales antes de arrancar el bucle."""
        try:
            r = self._client.messages.create(
                model=self.modelo,
                max_tokens=16,
                messages=[{"role": "user", "content": "Responde unicamente: OK"}],
            )
            texto = next((b.text for b in r.content if b.type == "text"), "")
            return True, f"conexion correcta con {r.model} ({texto.strip()[:20]})"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
