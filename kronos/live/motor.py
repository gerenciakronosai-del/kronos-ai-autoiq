"""Motor de automatizacion en vivo.

Ejecuta el ciclo completo en un hilo de fondo: leer mercado -> consultar los dos
cerebros -> filtrar por riesgo -> ejecutar en el broker -> registrar. El panel
solo lee instantaneas; nunca toca el estado interno.

Dos cerebros corren en paralelo sobre los mismos datos:

* **IA** — la API de Anthropic, no determinista, con coste y latencia por
  decision.
* **Local** — el motor de confluencia de `kronos.strategy`, determinista, gratis
  e instantaneo.

Se registran ambas decisiones aunque solo una ejecute. Esa comparativa es el
dato que de verdad importa: dice si la IA aporta algo sobre unas reglas fijas, o
si estas pagando por replicarlas.

Sobre el intervalo de 5 segundos: en velas de 1 a 5 minutos, once de cada doce
consultas analizan la MISMA vela y devuelven la misma respuesta. Por eso
`solo_en_cierre_de_vela` viene activado: el bucle sigue latiendo cada 5 s (datos,
liquidacion de posiciones, panel), pero la API solo se consulta cuando hay vela
nueva. Desactivarlo multiplica el coste por doce sin aportar informacion.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Literal, Optional

from kronos.broker.paper import PaperBroker
from kronos.ia.cerebro import CerebroIA, CerebroNoDisponible, RespuestaIA
from kronos.ia.coste import estimar_coste_diario
from kronos.live.feed import Feed
from kronos.risk.manager import RiskManager, RiskParams, Veto
from kronos.strategy.base import Confidence, Decision, Signal
from kronos.strategy.confluence import ConfluenceStrategy

Cerebro = Literal["ia", "local"]


@dataclass(slots=True)
class ConfigMotor:
    intervalo_seg: float = 5.0
    solo_en_cierre_de_vela: bool = True   # ver nota del modulo: 12x de coste si se apaga
    expiry_velas: int = 5
    payout: float = 0.80
    spread_pips: float = 0.5              # coste de entrada; a cero el panel miente
    cerebro_ejecutor: Cerebro = "ia"      # cual de los dos manda las ordenes
    usar_ia: bool = True
    usar_local: bool = True
    registro: Optional[str] = "data/decisiones.jsonl"
    max_historial: int = 400
    # Segundos sin velas nuevas antes de dar el feed por muerto. Un bot
    # desatendido que deja de recibir datos sigue "corriendo" sin hacer nada:
    # sin esta deteccion, un instrumento que cierra de madrugada (los OTC
    # cierran cuando abre el forex real) pasa inadvertido hasta la manana.
    alerta_sin_velas_seg: float = 300.0

    def __post_init__(self) -> None:
        if self.intervalo_seg < 0.5:
            raise ValueError("intervalo_seg minimo 0.5 s")
        if self.expiry_velas < 1:
            raise ValueError("expiry_velas debe ser >= 1")
        if not 0 < self.payout < 5:
            raise ValueError("payout fuera de rango")
        if self.spread_pips < 0:
            raise ValueError("spread_pips no puede ser negativo")
        if self.cerebro_ejecutor not in ("ia", "local"):
            raise ValueError("cerebro_ejecutor debe ser 'ia' o 'local'")
        # Pedirle al ejecutor que use un cerebro apagado veta TODAS las ordenes
        # en silencio: el bot parece funcionar y no opera nunca. Mejor fallar al
        # arrancar que descubrirlo tras media hora de registro vacio.
        if self.cerebro_ejecutor == "ia" and not self.usar_ia:
            raise ValueError(
                "cerebro_ejecutor='ia' con usar_ia=False: el ejecutor se quedaria "
                "sin decisiones y vetaria todo. Usa cerebro_ejecutor='local'."
            )
        if self.cerebro_ejecutor == "local" and not self.usar_local:
            raise ValueError(
                "cerebro_ejecutor='local' con usar_local=False: mismo problema."
            )


@dataclass(slots=True)
class Ciclo:
    """Registro de una iteracion que consulto a los cerebros."""

    n: int
    ts_vela: int
    hora: str
    precio: float
    ia_decision: str = "-"
    ia_confianza: str = "-"
    ia_razon: str = ""
    ia_latencia_ms: float = 0.0
    ia_coste_usd: float = 0.0
    ia_error: Optional[str] = None
    local_decision: str = "-"
    local_confianza: str = "-"
    local_razon: str = ""
    acuerdo: Optional[bool] = None
    ejecutada: bool = False
    veto: str = ""
    stake: float = 0.0
    orden_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Estadisticas:
    ticks: int = 0
    consultas: int = 0
    ticks_retrasados: int = 0      # el ciclo tardo mas que el intervalo
    ordenes: int = 0
    ganadas: int = 0
    perdidas: int = 0
    empates: int = 0
    acuerdos: int = 0
    desacuerdos: int = 0
    vetos: dict[str, int] = field(default_factory=dict)
    # PnL de las ordenes que abrio ESTE bot. No se puede usar el cambio del
    # balance de la cuenta: cualquier operacion manual, comision o bono se
    # atribuiria al bot y falsearia su resultado.
    pnl_bot: float = 0.0

    @property
    def decisivas(self) -> int:
        return self.ganadas + self.perdidas

    @property
    def winrate(self) -> float:
        return self.ganadas / self.decisivas if self.decisivas else 0.0

    @property
    def tasa_acuerdo(self) -> float:
        total = self.acuerdos + self.desacuerdos
        return self.acuerdos / total if total else 0.0


class MotorEnVivo:
    """Bucle de automatizacion. Arranca un hilo; el panel solo lee instantaneas."""

    def __init__(self, feed: Feed, config: Optional[ConfigMotor] = None,
                 riesgo: Optional[RiskParams] = None,
                 cerebro: Optional[CerebroIA] = None,
                 broker: Optional[Any] = None):
        self.feed = feed
        self.cfg = config or ConfigMotor()
        self.riesgo = riesgo or RiskParams()
        self.estrategia = ConfluenceStrategy()

        self._cerebro = cerebro
        self._error_cerebro: Optional[str] = None
        if self.cfg.usar_ia and cerebro is None:
            try:
                self._cerebro = CerebroIA()
            except CerebroNoDisponible as e:
                self._error_cerebro = str(e)
                self.cfg.usar_ia = False

        # Si no se inyecta broker, se opera contra el simulador. Un broker real
        # se pasa ya conectado desde fuera: conectar implica credenciales, y eso
        # no es responsabilidad del motor.
        if broker is None:
            self.broker = PaperBroker(balance_inicial=self.riesgo.balance_inicial,
                                      payout_por_defecto=self.cfg.payout,
                                      spread_pips=self.cfg.spread_pips)
            self.broker.conectar()
            self.broker_real = False
        else:
            self.broker = broker
            self.broker_real = True
            # El balance de partida lo manda la cuenta real, no la config: si no,
            # el PnL compara peras con manzanas e inventa un beneficio que no
            # existe (1.000 configurados frente a 10.000 de la demo = +9.000).
            try:
                self.riesgo.balance_inicial = broker.balance()
            except Exception:
                pass
        self.rm = RiskManager(self.riesgo)

        self.stats = Estadisticas()
        self.historial: Deque[Ciclo] = deque(maxlen=self.cfg.max_historial)
        # Curva de capital: (ts, balance). Se muestrea al cerrar cada operacion,
        # que es cuando el balance cambia de verdad.
        self.equity: Deque[tuple[int, float]] = deque(
            [(0, self.riesgo.balance_inicial)], maxlen=self.cfg.max_historial
        )
        self.ultimo_error: Optional[str] = None
        self.arrancado_en: Optional[float] = None

        self._lock = threading.Lock()
        self._parar = threading.Event()
        self._hilo: Optional[threading.Thread] = None
        self._ultimo_ts_consultado: int = 0
        self._ultima_vela_en: float = time.time()
        self.feed_estancado: bool = False

    # -- ciclo de vida ---------------------------------------------------- #
    @property
    def corriendo(self) -> bool:
        return self._hilo is not None and self._hilo.is_alive()

    def iniciar(self) -> None:
        if self.corriendo:
            return
        self._parar.clear()
        self.arrancado_en = time.time()
        self._hilo = threading.Thread(target=self._bucle, name="kronos-motor", daemon=True)
        self._hilo.start()

    def detener(self, timeout: float = 10.0) -> None:
        self._parar.set()
        if self._hilo is not None:
            self._hilo.join(timeout=timeout)
        self._hilo = None

    # -- bucle principal --------------------------------------------------- #
    def _bucle(self) -> None:
        while not self._parar.is_set():
            inicio = time.monotonic()
            try:
                self._tick()
            except Exception as e:  # un fallo puntual no puede tumbar el bot
                with self._lock:
                    self.ultimo_error = f"{type(e).__name__}: {e}"

            transcurrido = time.monotonic() - inicio
            if transcurrido > self.cfg.intervalo_seg:
                with self._lock:
                    self.stats.ticks_retrasados += 1
            self._parar.wait(max(0.0, self.cfg.intervalo_seg - transcurrido))

    def _tick(self) -> None:
        hay_vela_nueva = self.feed.avanzar()
        precio = self.feed.precio
        ahora = time.time()
        with self._lock:
            self.stats.ticks += 1
            if hay_vela_nueva:
                self._ultima_vela_en = ahora
                if self.feed_estancado:
                    self.feed_estancado = False
                    self.ultimo_error = None
            elif ahora - self._ultima_vela_en > self.cfg.alerta_sin_velas_seg:
                if not self.feed_estancado:
                    self.feed_estancado = True
                    minutos = (ahora - self._ultima_vela_en) / 60
                    self.ultimo_error = (
                        f"FEED ESTANCADO: {minutos:.0f} min sin velas nuevas de "
                        f"{self.feed.symbol}. El instrumento pudo cerrar (los OTC "
                        "cierran al abrir el forex real) o se corto la conexion."
                    )

        if precio is None or not self.feed.listo:
            return

        serie = self.feed.serie
        ts = serie[-1].ts

        # 1) Liquidar vencimientos, siempre. Contra el simulador decide el
        #    precio; contra un broker real decide el servidor.
        for orden in self.broker.liquidar(self.feed.symbol, precio, ts):
            devolucion = orden.stake + orden.pnl if orden.pnl >= 0 else 0.0
            self.rm.on_close(orden.pnl, devolucion)
            with self._lock:
                if orden.estado.value == "GANADA":
                    self.stats.ganadas += 1
                elif orden.estado.value == "PERDIDA":
                    self.stats.perdidas += 1
                else:
                    self.stats.empates += 1
                self.stats.pnl_bot += orden.pnl
                self.equity.append((ts, self.broker.balance()))

        self.rm.on_new_bar(ts)
        if self.rm.state.kill_switch:
            return

        # 2) Compuerta de consulta: sin vela nueva no hay informacion nueva.
        if self.cfg.solo_en_cierre_de_vela:
            if not hay_vela_nueva or ts == self._ultimo_ts_consultado:
                return
        self._ultimo_ts_consultado = ts

        # 3) Consultar a los cerebros.
        senal_local: Optional[Signal] = None
        if self.cfg.usar_local:
            senal_local = self.estrategia.evaluate(serie)

        respuesta_ia: Optional[RespuestaIA] = None
        if self.cfg.usar_ia and self._cerebro is not None:
            respuesta_ia = self._cerebro.analizar(serie, expiry_velas=self.cfg.expiry_velas)

        ciclo = self._construir_ciclo(ts, precio, respuesta_ia, senal_local)

        # 4) Ejecutar la decision del cerebro designado.
        self._ejecutar(ciclo, respuesta_ia, senal_local, precio)

        with self._lock:
            self.stats.consultas += 1
            if ciclo.acuerdo is True:
                self.stats.acuerdos += 1
            elif ciclo.acuerdo is False:
                self.stats.desacuerdos += 1
            self.historial.append(ciclo)
        self._persistir(ciclo)

    def _construir_ciclo(self, ts: int, precio: float,
                         ia: Optional[RespuestaIA], local: Optional[Signal]) -> Ciclo:
        with self._lock:
            n = self.stats.consultas + 1
        c = Ciclo(
            n=n, ts_vela=ts, precio=precio,
            hora=datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        )
        if ia is not None:
            c.ia_decision = str(ia.decision)
            c.ia_confianza = str(ia.confianza)
            c.ia_razon = ia.razon
            c.ia_latencia_ms = round(ia.latencia_ms, 1)
            c.ia_coste_usd = round(ia.coste_usd, 6)
            c.ia_error = ia.error
        if local is not None:
            c.local_decision = str(local.decision)
            c.local_confianza = str(local.confianza)
            c.local_razon = local.razon
        if ia is not None and local is not None and ia.ok:
            c.acuerdo = ia.decision is local.decision
        return c

    def _ejecutar(self, ciclo: Ciclo, ia: Optional[RespuestaIA],
                  local: Optional[Signal], precio: float) -> None:
        if self.cfg.cerebro_ejecutor == "ia":
            if ia is None or not ia.ok:
                ciclo.veto = "SIN_DECISION_IA"
                return
            senal = Signal(decision=ia.decision, confianza=ia.confianza,
                           razon=ia.razon, symbol=self.feed.symbol, ts=ia.ts)
        else:
            if local is None:
                ciclo.veto = "SIN_DECISION_LOCAL"
                return
            senal = local

        decision = self.rm.evaluate(senal)
        if not decision.permitido:
            ciclo.veto = str(decision.veto)
            if decision.veto is not Veto.OK:
                with self._lock:
                    self.stats.vetos[str(decision.veto)] = (
                        self.stats.vetos.get(str(decision.veto), 0) + 1
                    )
            return

        orden = self.broker.comprar(
            self.feed.symbol, senal.decision, decision.stake,
            self.cfg.expiry_velas * self.feed.timeframe,
        )
        if orden.estado.value == "RECHAZADA":
            ciclo.veto = f"BROKER: {orden.detalle}"
            return

        self.rm.on_open(decision.stake)
        ciclo.ejecutada = True
        ciclo.stake = decision.stake
        ciclo.orden_id = orden.id
        with self._lock:
            self.stats.ordenes += 1

    def _persistir(self, ciclo: Ciclo) -> None:
        if not self.cfg.registro:
            return
        try:
            p = Path(self.cfg.registro)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(ciclo.to_dict(), ensure_ascii=False) + "\n")
        except OSError as e:
            with self._lock:
                self.ultimo_error = f"no se pudo escribir el registro: {e}"

    # -- lectura para el panel --------------------------------------------- #
    def snapshot(self) -> dict[str, Any]:
        """Copia coherente del estado. Es lo unico que el panel debe leer."""
        with self._lock:
            stats = Estadisticas(**{**asdict(self.stats), "vetos": dict(self.stats.vetos)})
            historial = list(self.historial)
            equity = list(self.equity)
            ultimo_error = self.ultimo_error
        serie = self.feed.serie
        precios = [(c.ts, c.close) for c in serie.tail(180)]
        contador = self._cerebro.contador if self._cerebro else None
        balance = self.broker.balance()
        return {
            "corriendo": self.corriendo,
            "arrancado_en": self.arrancado_en,
            "feed": self.feed.descripcion(),
            "symbol": self.feed.symbol,
            "precio": self.feed.precio,
            "stats": stats,
            "historial": historial,
            "equity": equity,
            "precios": precios,
            "ultimo_error": ultimo_error,
            "error_cerebro": self._error_cerebro,
            "balance": balance,
            "balance_inicial": self.riesgo.balance_inicial,
            "pnl": balance - self.riesgo.balance_inicial,
            "posiciones_abiertas": len(self.broker.abiertas),
            "kill_switch": self.rm.state.kill_switch,
            "motivo_kill": self.rm.state.motivo_kill,
            "perdidas_seguidas": self.rm.state.perdidas_seguidas,
            "operaciones_hoy": self.rm.state.operaciones_hoy,
            "coste_total": contador.coste_total if contador else 0.0,
            "coste_medio": contador.coste_medio if contador else 0.0,
            "latencia_media": contador.latencia_media if contador else 0.0,
            "latencia_p95": contador.latencia_p95 if contador else 0.0,
            "tasa_cache": contador.tasa_cache if contador else 0.0,
            "llamadas_ia": contador.llamadas if contador else 0,
            "errores_ia": contador.errores if contador else 0,
            "umbral_equilibrio": 1.0 / (1.0 + self.cfg.payout),
            "spread_pips": self.cfg.spread_pips,
            "broker_real": self.broker_real,
            "feed_estancado": self.feed_estancado,
            "seg_sin_velas": time.time() - self._ultima_vela_en,
            "tipo_cuenta": str(getattr(self.broker, "tipo_cuenta", "DEMO")),
            "coste_diario_proyectado": self._coste_diario_proyectado(contador),
        }

    def _coste_diario_proyectado(self, contador: Any) -> float:
        """Extrapola el gasto a 24 h al ritmo real de consultas observado."""
        if contador is None or contador.llamadas == 0 or not self.arrancado_en:
            return estimar_coste_diario(
                self.cfg.intervalo_seg if not self.cfg.solo_en_cierre_de_vela
                else self.feed.timeframe
            )
        transcurrido = max(time.time() - self.arrancado_en, 1.0)
        return contador.coste_total * (86400 / transcurrido)
