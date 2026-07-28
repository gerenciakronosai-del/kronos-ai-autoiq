"""Adaptador para IQ Option (cuenta DEMO por defecto).

Depende de `iqoptionapi` de Lu-Yi-Hsun, un cliente de ingenieria inversa NO
oficial. Instalacion:

    pip install "git+https://github.com/Lu-Yi-Hsun/iqoptionapi.git"

El paquete `iqoptionapi` que hay en PyPI es OTRO proyecto distinto y abandonado
(no tiene `stable_api`); no sirve.

## Lo que este adaptador corrige de la libreria

`check_win_v3()` de la libreria es un `while True:` sin pausa ni timeout que
bloquea hasta que la orden vence. Usado dentro de un bucle de trading congela el
hilo y quema un nucleo al 100%. Aqui se sondea `get_async_order()` de forma no
bloqueante y se decide en nuestro codigo.

## Salvaguardas de cuenta REAL

1. La cuenta por defecto es DEMO (`PRACTICE`).
2. Operar en REAL exige ADEMAS la variable de entorno `KRONOS_ALLOW_REAL=1`.
   Dos gestos independientes, para que ninguna orden real salga de un descuido.
3. Las credenciales se leen SOLO de `IQ_EMAIL` / `IQ_PASSWORD`. Nunca por CLI ni
   por fichero, para que no acaben en el historial del shell ni en git.

## Advertencias que debes conocer

* Automatizar mediante un cliente no oficial puede infringir los terminos de
  servicio del broker y acarrear la suspension de la cuenta. Es tu decision.
* La libreria es de terceros y no esta auditada; recibe tus credenciales.
* La API no oficial se rompe cuando el broker cambia su protocolo.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Optional

from kronos.broker.base import Broker, EstadoOrden, Orden, TipoCuenta
from kronos.strategy.base import Decision

_ENV_ALLOW_REAL = "KRONOS_ALLOW_REAL"
_ENV_EMAIL = "IQ_EMAIL"
_ENV_PASSWORD = "IQ_PASSWORD"

INSTALACION = 'pip install "git+https://github.com/Lu-Yi-Hsun/iqoptionapi.git"'


class BrokerNoDisponible(RuntimeError):
    """La integracion no puede usarse (falta libreria, credenciales o permiso)."""


def _a_fraccion(valor: Any) -> Optional[float]:
    """Normaliza el payout venga como venga. None si no hay forma de leerlo.

    La API no oficial no tiene contrato estable: segun cuenta y version devuelve
    un numero (0.85), un porcentaje (85) o un diccionario anidado con el valor
    dentro. Asumir una sola forma es garantizar que rompa en otra cuenta.
    """
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        p = float(valor)
    elif isinstance(valor, str):
        try:
            p = float(valor.strip().rstrip("%"))
        except ValueError:
            return None
    elif isinstance(valor, dict):
        # Preferir claves con nombre reconocible; si no, el primer numerico.
        for k in ("profit", "payout", "percent", "value", "turbo", "binary"):
            if k in valor:
                anidado = _a_fraccion(valor[k])
                if anidado is not None:
                    return anidado
        for v in valor.values():
            anidado = _a_fraccion(v)
            if anidado is not None:
                return anidado
        return None
    else:
        return None

    if p <= 0:
        return None
    return p / 100.0 if p > 1.0 else p


class IQOptionBroker(Broker):
    """Adaptador sobre `iqoptionapi.stable_api.IQ_Option`, sondeo no bloqueante."""

    def __init__(self, tipo_cuenta: TipoCuenta = TipoCuenta.DEMO,
                 timeout_conexion: float = 30.0, reintentos: int = 3):
        self.tipo_cuenta = tipo_cuenta
        self.timeout_conexion = timeout_conexion
        self.reintentos = reintentos
        self._api: Optional[Any] = None
        self._lock = threading.Lock()
        self._abiertas: dict[str, Orden] = {}
        self._historial: list[Orden] = []
        self._payout_cache: dict[str, tuple[float, float]] = {}  # symbol -> (payout, ts)
        self._payout_ttl = 300.0

        if tipo_cuenta is TipoCuenta.REAL and os.environ.get(_ENV_ALLOW_REAL) != "1":
            raise BrokerNoDisponible(
                "Operativa en cuenta REAL bloqueada. Para habilitarla debes definir "
                f"{_ENV_ALLOW_REAL}=1 en el entorno, ademas de pedir tipo_cuenta=REAL. "
                "Valida primero en DEMO con resultados fuera de muestra."
            )

    # -- conexion --------------------------------------------------------- #
    def conectar(self) -> None:
        try:
            from iqoptionapi.stable_api import IQ_Option  # type: ignore
        except ImportError as e:
            raise BrokerNoDisponible(
                f"Falta el cliente no oficial de IQ Option. Instalalo con:\n    {INSTALACION}\n"
                "Ojo: el paquete 'iqoptionapi' de PyPI es otro proyecto y no sirve."
            ) from e

        email = os.environ.get(_ENV_EMAIL)
        password = os.environ.get(_ENV_PASSWORD)
        if not email or not password:
            raise BrokerNoDisponible(
                f"Faltan credenciales: define {_ENV_EMAIL} y {_ENV_PASSWORD} como "
                "variables de entorno. No se leen de la linea de comandos ni de ficheros."
            )

        api = IQ_Option(email, password)
        ok, motivo = api.connect()
        if not ok:
            raise BrokerNoDisponible(f"conexion rechazada por el broker: {motivo}")

        modo = "REAL" if self.tipo_cuenta is TipoCuenta.REAL else "PRACTICE"
        api.change_balance(modo)

        inicio = time.time()
        while not api.check_connect():
            if time.time() - inicio > self.timeout_conexion:
                raise BrokerNoDisponible("timeout esperando a que la sesion quede activa")
            time.sleep(0.5)
        self._api = api

    def cerrar(self) -> None:
        api, self._api = self._api, None
        if api is None:
            return
        for metodo in ("close_connect", "logout"):
            cerrar = getattr(api, metodo, None)
            if callable(cerrar):
                try:
                    cerrar()
                except Exception:  # el broker cierra el socket por su cuenta
                    pass
                break

    def _requerir(self) -> Any:
        if self._api is None:
            raise BrokerNoDisponible("broker no conectado: usa conectar() o el context manager")
        return self._api

    @property
    def conectado(self) -> bool:
        try:
            return self._api is not None and bool(self._api.check_connect())
        except Exception:
            return False

    def reconectar(self) -> bool:
        """Reintenta la conexion tras una caida. True si quedo operativo."""
        for intento in range(self.reintentos):
            try:
                self.cerrar()
                self.conectar()
                return True
            except BrokerNoDisponible:
                time.sleep(min(2 ** intento, 10))
        return False

    # -- interfaz Broker --------------------------------------------------- #
    def balance(self) -> float:
        return float(self._requerir().get_balance())

    def payout(self, symbol: str, expiracion_seg: int) -> float:
        """Payout REAL del broker, como fraccion (0.80 = 80%). Cacheado 5 min.

        Este es el numero que decide si una estrategia puede ser rentable, y es
        el unico que no se puede sacar de un backtest: hay que preguntarselo al
        broker.
        """
        ahora = time.time()
        cacheado = self._payout_cache.get(symbol)
        if cacheado and ahora - cacheado[1] < self._payout_ttl:
            return cacheado[0]

        api = self._requerir()
        try:
            datos = api.get_all_profit()
            entrada = datos[symbol]
        except Exception as e:
            raise BrokerNoDisponible(
                f"no se pudo leer el payout de {symbol}: {type(e).__name__}: {e}"
            ) from e

        # Un contenedor vacio no es un formato raro: es que el broker no cotiza
        # ese activo ahora mismo (mercado cerrado). Merece un mensaje distinto.
        if isinstance(entrada, dict) and not entrada:
            raise BrokerNoDisponible(
                f"el broker no cotiza {symbol} ahora mismo (respuesta vacia). "
                "Con el forex cerrado solo hay activos OTC; prueba con "
                f"{symbol}-OTC o espera a que abra el mercado."
            )

        clave = "turbo" if expiracion_seg <= 300 else "binary"
        bruto = entrada.get(clave, entrada) if isinstance(entrada, dict) else entrada
        payout = _a_fraccion(bruto)
        if payout is None:
            raise BrokerNoDisponible(
                f"no se pudo interpretar el payout de {symbol}. "
                f"El broker devolvio: {entrada!r}"
            )
        self._payout_cache[symbol] = (payout, ahora)
        return payout

    def comprar(self, symbol: str, direccion: Decision, stake: float,
                expiracion_seg: int) -> Orden:
        if not direccion.is_trade:
            raise ValueError("no se puede comprar con decision ESPERAR")
        if stake <= 0:
            raise ValueError("stake debe ser > 0")
        api = self._requerir()
        minutos = max(1, round(expiracion_seg / 60))
        accion = "call" if direccion is Decision.CALL else "put"
        ts = int(time.time())

        try:
            ok, order_id = api.buy(stake, symbol, accion, minutos)
        except Exception as e:
            return Orden(
                id="rechazada", symbol=symbol, direccion=direccion, stake=stake,
                precio_entrada=0.0, ts_apertura=ts, expiracion_seg=expiracion_seg,
                estado=EstadoOrden.RECHAZADA,
                detalle=f"excepcion al comprar: {type(e).__name__}: {e}",
            )

        if not ok or order_id is None:
            return Orden(
                id="rechazada", symbol=symbol, direccion=direccion, stake=stake,
                precio_entrada=0.0, ts_apertura=ts, expiracion_seg=expiracion_seg,
                estado=EstadoOrden.RECHAZADA, detalle=f"broker rechazo la orden: {order_id}",
            )

        orden = Orden(
            id=str(order_id), symbol=symbol, direccion=direccion, stake=stake,
            precio_entrada=0.0, ts_apertura=ts, expiracion_seg=expiracion_seg,
        )
        with self._lock:
            self._abiertas[orden.id] = orden
        return orden

    def estado_orden(self, orden: Orden) -> Orden:
        """Consulta NO bloqueante. Devuelve la orden intacta si aun no vencio.

        Deliberadamente NO se usa `check_win_v3()` de la libreria: es un
        `while True:` sin pausa que bloquea hasta el vencimiento.
        """
        if orden.cerrada:
            return orden
        api = self._requerir()
        try:
            asincrona = api.get_async_order(int(orden.id))
        except (KeyError, ValueError, TypeError, AttributeError):
            return orden
        except Exception:
            return orden

        if not asincrona:
            return orden
        cerrada = asincrona.get("option-closed") if isinstance(asincrona, dict) else None
        if not cerrada:
            return orden

        try:
            msg = cerrada["msg"]
            pnl = float(msg["profit_amount"]) - float(msg["amount"])
        except (KeyError, TypeError, ValueError):
            return orden

        orden.pnl = pnl
        orden.ts_cierre = int(time.time())
        orden.estado = (
            EstadoOrden.GANADA if pnl > 0
            else EstadoOrden.PERDIDA if pnl < 0
            else EstadoOrden.EMPATE
        )
        return orden

    def liquidar(self, symbol: str, precio: float, ts: int) -> list[Orden]:
        """Sondea las ordenes abiertas y devuelve las que acaban de cerrarse.

        `precio` se ignora: aqui manda el broker, no nuestra serie de velas.
        """
        cerradas: list[Orden] = []
        with self._lock:
            pendientes = list(self._abiertas.values())
        for orden in pendientes:
            actualizada = self.estado_orden(orden)
            if actualizada.cerrada:
                with self._lock:
                    self._abiertas.pop(actualizada.id, None)
                    self._historial.append(actualizada)
                cerradas.append(actualizada)
        return cerradas

    # -- inspeccion --------------------------------------------------------- #
    @property
    def historial(self) -> list[Orden]:
        with self._lock:
            return list(self._historial)

    @property
    def abiertas(self) -> list[Orden]:
        with self._lock:
            return list(self._abiertas.values())

    def activos_abiertos(self, tipo: Optional[str] = None) -> list[str]:
        """Activos que el broker acepta ahora mismo.

        Sin `tipo` recorre TODAS las secciones (turbo, binary, digital, forex...)
        porque la API no garantiza donde aparece cada instrumento: los OTC de
        fin de semana suelen vivir en una seccion distinta a los pares normales.
        """
        try:
            datos = self._requerir().get_all_open_time()
        except Exception:
            return []
        if not isinstance(datos, dict):
            return []

        secciones = [tipo] if tipo else list(datos.keys())
        abiertos: set[str] = set()
        for sec in secciones:
            contenido = datos.get(sec) or {}
            if not isinstance(contenido, dict):
                continue
            for nombre, estado in contenido.items():
                if isinstance(estado, dict) and estado.get("open"):
                    abiertos.add(str(nombre))
        return sorted(abiertos)

    def descargar_velas(self, symbol: str, timeframe: int = 60, total: int = 20_000,
                        lote: int = 1000, progreso: Optional[Any] = None,
                        pausa: float = 0.2) -> list[dict[str, Any]]:
        """Descarga histórico hacia atrás en lotes.

        La API limita cada llamada, asi que se encadenan peticiones usando la
        vela mas antigua recibida como nuevo `endtime`. Sirve para conseguir
        muestra suficiente de los instrumentos OTC, cuyo historico no publica
        nadie: es la unica forma de saber si se comportan como el mercado real
        o son otra cosa.
        """
        api = self._requerir()
        crudas: dict[int, dict[str, Any]] = {}
        endtime = int(time.time())

        while len(crudas) < total:
            try:
                bloque = api.get_candles(symbol, timeframe, min(lote, total), endtime)
            except Exception as e:
                raise BrokerNoDisponible(
                    f"fallo descargando velas de {symbol}: {type(e).__name__}: {e}"
                ) from e
            if not bloque:
                break

            nuevas = 0
            for c in bloque:
                try:
                    ts = int(c["from"])
                except (KeyError, TypeError, ValueError):
                    continue
                if ts not in crudas:
                    crudas[ts] = c
                    nuevas += 1
            if nuevas == 0:  # el broker ya no tiene mas historico
                break

            endtime = min(crudas) - 1
            if progreso:
                progreso(len(crudas), total)
            if pausa > 0:
                time.sleep(pausa)  # no martillear la API

        return [crudas[k] for k in sorted(crudas)]

    def crudo(self) -> dict[str, Any]:
        """Vuelca lo que devuelve la API tal cual, para diagnosticar.

        Una API sin contrato cambia de forma entre cuentas y versiones; sin ver
        la estructura real no se puede adaptar el codigo, solo adivinar.
        """
        api = self._requerir()
        out: dict[str, Any] = {}
        for nombre, fn in (("get_all_open_time", api.get_all_open_time),
                           ("get_all_profit", api.get_all_profit)):
            try:
                out[nombre] = fn()
            except Exception as e:
                out[nombre] = f"ERROR {type(e).__name__}: {e}"
        return out

    def diagnostico(self, symbol: str = "EURUSD", expiracion_seg: int = 300) -> dict[str, Any]:
        """Comprobacion completa antes de dejar el bot corriendo solo."""
        info: dict[str, Any] = {
            "conectado": self.conectado,
            "tipo_cuenta": str(self.tipo_cuenta),
        }
        try:
            info["balance"] = self.balance()
        except Exception as e:
            info["balance_error"] = f"{type(e).__name__}: {e}"
        try:
            p = self.payout(symbol, expiracion_seg)
            info["payout"] = p
            info["umbral_equilibrio"] = 1.0 / (1.0 + p) if p > 0 else None
        except Exception as e:
            info["payout_error"] = f"{type(e).__name__}: {e}"
        abiertos = self.activos_abiertos()
        info["activos_abiertos"] = len(abiertos)
        info["lista_abiertos"] = abiertos
        info["symbol_operable"] = symbol in abiertos if abiertos else None
        # Los activos "-OTC" son precios SINTETICOS que genera el propio broker,
        # no el mercado. Son los unicos disponibles con el forex cerrado (noches
        # y fines de semana) y no se comportan como el par real: un backtest
        # sobre EURUSD real no dice nada sobre EURUSD-OTC.
        info["solo_otc"] = bool(abiertos) and all("OTC" in a.upper() for a in abiertos)
        return info
