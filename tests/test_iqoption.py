"""Tests del adaptador de IQ Option, con un doble de la API.

No se toca la red. Lo que se verifica es lo que puede arruinar una sesion
desatendida: que el sondeo NO bloquee, que un fallo del broker no tumbe el bot,
y que la cuenta REAL siga tras sus dos cerrojos.
"""

from __future__ import annotations

import os
import time
import unittest

from kronos.broker.base import EstadoOrden, TipoCuenta
from kronos.broker.iqoption import BrokerNoDisponible, IQOptionBroker
from kronos.strategy.base import Decision


class ApiFalsa:
    """Doble de `IQ_Option`. Solo lo que usa el adaptador."""

    def __init__(self, *, balance=1000.0, payout=0.85, comprar_ok=True):
        self._balance = balance
        self._payout = payout
        self.comprar_ok = comprar_ok
        self.ordenes: dict[int, dict] = {}
        self.siguiente_id = 1000
        self.compras = []

    def check_connect(self): return True
    def get_balance(self): return self._balance
    def get_all_profit(self): return {"EURUSD": {"turbo": self._payout, "binary": 0.75}}
    def get_all_open_time(self):
        return {"turbo": {"EURUSD": {"open": True}, "GBPUSD": {"open": False}}}

    def buy(self, price, actives, action, expirations):
        self.compras.append((price, actives, action, expirations))
        if not self.comprar_ok:
            return False, "mercado cerrado"
        oid = self.siguiente_id
        self.siguiente_id += 1
        self.ordenes[oid] = {"option-closed": {}}  # abierta
        return True, oid

    def cerrar_orden(self, oid: int, profit: float, amount: float):
        self.ordenes[oid] = {"option-closed": {"msg": {"profit_amount": profit,
                                                       "amount": amount}}}

    def get_async_order(self, oid):
        return self.ordenes[int(oid)]  # KeyError si no existe, como la real


def broker_conectado(api: ApiFalsa) -> IQOptionBroker:
    b = IQOptionBroker(tipo_cuenta=TipoCuenta.DEMO)
    b._api = api
    return b


class TestCerrojosDeCuentaReal(unittest.TestCase):
    def test_real_bloqueada_sin_variable_de_entorno(self):
        previo = os.environ.pop("KRONOS_ALLOW_REAL", None)
        try:
            with self.assertRaises(BrokerNoDisponible) as ctx:
                IQOptionBroker(tipo_cuenta=TipoCuenta.REAL)
            self.assertIn("KRONOS_ALLOW_REAL", str(ctx.exception))
        finally:
            if previo is not None:
                os.environ["KRONOS_ALLOW_REAL"] = previo

    def test_demo_no_necesita_permiso(self):
        self.assertIs(IQOptionBroker().tipo_cuenta, TipoCuenta.DEMO)

    def test_real_permitida_con_los_dos_gestos(self):
        previo = os.environ.get("KRONOS_ALLOW_REAL")
        os.environ["KRONOS_ALLOW_REAL"] = "1"
        try:
            b = IQOptionBroker(tipo_cuenta=TipoCuenta.REAL)
            self.assertIs(b.tipo_cuenta, TipoCuenta.REAL)
        finally:
            if previo is None:
                os.environ.pop("KRONOS_ALLOW_REAL", None)
            else:
                os.environ["KRONOS_ALLOW_REAL"] = previo


class TestSinConexion(unittest.TestCase):
    def test_operaciones_fallan_claro_sin_conectar(self):
        b = IQOptionBroker()
        for fn in (b.balance, lambda: b.payout("EURUSD", 300)):
            with self.assertRaises(BrokerNoDisponible):
                fn()

    def test_conectado_es_falso_sin_api(self):
        self.assertFalse(IQOptionBroker().conectado)

    def test_activos_abiertos_devuelve_vacio_sin_api(self):
        self.assertEqual(IQOptionBroker().activos_abiertos(), [])


class TestPayout(unittest.TestCase):
    def test_lee_el_payout_del_broker(self):
        b = broker_conectado(ApiFalsa(payout=0.87))
        self.assertAlmostEqual(b.payout("EURUSD", 300), 0.87)

    def test_convierte_porcentaje_a_fraccion(self):
        b = broker_conectado(ApiFalsa(payout=87.0))
        self.assertAlmostEqual(b.payout("EURUSD", 300), 0.87)

    def test_cachea(self):
        api = ApiFalsa(payout=0.80)
        b = broker_conectado(api)
        self.assertAlmostEqual(b.payout("EURUSD", 300), 0.80)
        api._payout = 0.10  # cambia por debajo; la cache debe mantener el valor
        self.assertAlmostEqual(b.payout("EURUSD", 300), 0.80)

    def test_activo_desconocido_falla_claro(self):
        b = broker_conectado(ApiFalsa())
        with self.assertRaises(BrokerNoDisponible):
            b.payout("NO-EXISTE", 300)


class TestOrdenes(unittest.TestCase):
    def test_compra_registra_la_orden(self):
        api = ApiFalsa()
        b = broker_conectado(api)
        o = b.comprar("EURUSD", Decision.CALL, 10.0, 300)
        self.assertIs(o.estado, EstadoOrden.ABIERTA)
        self.assertEqual(len(b.abiertas), 1)
        self.assertEqual(api.compras[0][2], "call")

    def test_put_manda_la_accion_correcta(self):
        api = ApiFalsa()
        broker_conectado(api).comprar("EURUSD", Decision.PUT, 10.0, 300)
        self.assertEqual(api.compras[0][2], "put")

    def test_expiracion_se_convierte_a_minutos(self):
        api = ApiFalsa()
        broker_conectado(api).comprar("EURUSD", Decision.CALL, 10.0, 300)
        self.assertEqual(api.compras[0][3], 5)

    def test_rechazo_del_broker_no_lanza(self):
        b = broker_conectado(ApiFalsa(comprar_ok=False))
        o = b.comprar("EURUSD", Decision.CALL, 10.0, 300)
        self.assertIs(o.estado, EstadoOrden.RECHAZADA)
        self.assertEqual(len(b.abiertas), 0)

    def test_esperar_no_es_comprable(self):
        with self.assertRaises(ValueError):
            broker_conectado(ApiFalsa()).comprar("EURUSD", Decision.ESPERAR, 10.0, 300)

    def test_stake_no_positivo(self):
        with self.assertRaises(ValueError):
            broker_conectado(ApiFalsa()).comprar("EURUSD", Decision.CALL, 0.0, 300)


class TestSondeoNoBloqueante(unittest.TestCase):
    """El fallo que este adaptador existe para evitar.

    `check_win_v3()` de la libreria es un `while True:` sin pausa ni timeout:
    bloquea el hilo hasta que la orden vence y quema un nucleo al 100%.
    """

    def test_orden_abierta_devuelve_rapido(self):
        b = broker_conectado(ApiFalsa())
        o = b.comprar("EURUSD", Decision.CALL, 10.0, 300)
        inicio = time.monotonic()
        actualizada = b.estado_orden(o)
        self.assertLess(time.monotonic() - inicio, 0.5, "el sondeo bloqueo")
        self.assertIs(actualizada.estado, EstadoOrden.ABIERTA)

    def test_liquidar_con_todo_abierto_no_bloquea(self):
        b = broker_conectado(ApiFalsa())
        for _ in range(5):
            b.comprar("EURUSD", Decision.CALL, 10.0, 300)
        inicio = time.monotonic()
        cerradas = b.liquidar("EURUSD", 1.1, int(time.time()))
        self.assertLess(time.monotonic() - inicio, 0.5)
        self.assertEqual(cerradas, [])
        self.assertEqual(len(b.abiertas), 5)

    def test_ganancia_se_liquida(self):
        api = ApiFalsa()
        b = broker_conectado(api)
        o = b.comprar("EURUSD", Decision.CALL, 10.0, 300)
        api.cerrar_orden(int(o.id), profit=18.5, amount=10.0)
        cerradas = b.liquidar("EURUSD", 1.1, int(time.time()))
        self.assertEqual(len(cerradas), 1)
        self.assertIs(cerradas[0].estado, EstadoOrden.GANADA)
        self.assertAlmostEqual(cerradas[0].pnl, 8.5)
        self.assertEqual(len(b.abiertas), 0)

    def test_perdida_se_liquida(self):
        api = ApiFalsa()
        b = broker_conectado(api)
        o = b.comprar("EURUSD", Decision.PUT, 10.0, 300)
        api.cerrar_orden(int(o.id), profit=0.0, amount=10.0)
        cerradas = b.liquidar("EURUSD", 1.1, int(time.time()))
        self.assertIs(cerradas[0].estado, EstadoOrden.PERDIDA)
        self.assertAlmostEqual(cerradas[0].pnl, -10.0)

    def test_empate_se_liquida(self):
        api = ApiFalsa()
        b = broker_conectado(api)
        o = b.comprar("EURUSD", Decision.CALL, 10.0, 300)
        api.cerrar_orden(int(o.id), profit=10.0, amount=10.0)
        cerradas = b.liquidar("EURUSD", 1.1, int(time.time()))
        self.assertIs(cerradas[0].estado, EstadoOrden.EMPATE)

    def test_orden_desconocida_no_lanza(self):
        """La API real lanza KeyError si el id no esta en su cache."""
        api = ApiFalsa()
        b = broker_conectado(api)
        o = b.comprar("EURUSD", Decision.CALL, 10.0, 300)
        api.ordenes.clear()
        self.assertIs(b.estado_orden(o).estado, EstadoOrden.ABIERTA)

    def test_respuesta_malformada_no_lanza(self):
        api = ApiFalsa()
        b = broker_conectado(api)
        o = b.comprar("EURUSD", Decision.CALL, 10.0, 300)
        api.ordenes[int(o.id)] = {"option-closed": {"msg": {"sin": "campos"}}}
        self.assertIs(b.estado_orden(o).estado, EstadoOrden.ABIERTA)

    def test_una_orden_ya_cerrada_no_se_reevalua(self):
        api = ApiFalsa()
        b = broker_conectado(api)
        o = b.comprar("EURUSD", Decision.CALL, 10.0, 300)
        api.cerrar_orden(int(o.id), profit=18.0, amount=10.0)
        b.liquidar("EURUSD", 1.1, int(time.time()))
        self.assertEqual(b.liquidar("EURUSD", 1.1, int(time.time())), [])


class TestDiagnostico(unittest.TestCase):
    def test_reporta_payout_y_umbral(self):
        info = broker_conectado(ApiFalsa(payout=0.85)).diagnostico("EURUSD", 300)
        self.assertAlmostEqual(info["payout"], 0.85)
        self.assertAlmostEqual(info["umbral_equilibrio"], 1 / 1.85, places=6)
        self.assertTrue(info["symbol_operable"])

    def test_activo_cerrado_se_detecta(self):
        info = broker_conectado(ApiFalsa()).diagnostico("GBPUSD", 300)
        self.assertFalse(info["symbol_operable"])

    def test_no_lanza_si_falla_el_payout(self):
        info = broker_conectado(ApiFalsa()).diagnostico("NO-EXISTE", 300)
        self.assertIn("payout_error", info)

    def test_detecta_mercado_real_cerrado(self):
        """Con forex cerrado solo quedan OTC: precios sinteticos del broker.

        Importa avisarlo porque un backtest sobre el par real no dice nada
        sobre el OTC del mismo nombre.
        """
        class SoloOtc(ApiFalsa):
            def get_all_open_time(self):
                return {"turbo": {"EURUSD-OTC": {"open": True},
                                  "GBPUSD-OTC": {"open": True}}}

        info = broker_conectado(SoloOtc()).diagnostico("EURUSD", 300)
        self.assertTrue(info["solo_otc"])
        self.assertFalse(info["symbol_operable"])
        self.assertEqual(len(info["lista_abiertos"]), 2)

    def test_mercado_abierto_no_marca_solo_otc(self):
        info = broker_conectado(ApiFalsa()).diagnostico("EURUSD", 300)
        self.assertFalse(info["solo_otc"])
        self.assertIn("EURUSD", info["lista_abiertos"])


if __name__ == "__main__":
    unittest.main()


class TestNormalizacionPayout(unittest.TestCase):
    """La API no oficial devuelve el payout en formatos distintos segun cuenta
    y version. Asumir uno solo garantiza que rompa en otra cuenta."""

    def test_formatos_validos(self):
        from kronos.broker.iqoption import _a_fraccion
        for entrada, esperado in (
            (0.85, 0.85),                      # fraccion
            (85, 0.85),                        # porcentaje
            ("85", 0.85),                      # texto
            ("85%", 0.85),                     # texto con simbolo
            ({"profit": 0.85}, 0.85),          # dict con clave conocida
            ({"turbo": {"profit": 87}}, 0.87),  # dict anidado
            ({"raro": {"otro": 0.9}}, 0.9),    # dict sin clave conocida
        ):
            self.assertAlmostEqual(_a_fraccion(entrada), esperado, places=9,
                                   msg=f"fallo con {entrada!r}")

    def test_formatos_ilegibles_devuelven_none(self):
        from kronos.broker.iqoption import _a_fraccion
        for entrada in ({}, None, "abc", 0, -1, True, [], object()):
            self.assertIsNone(_a_fraccion(entrada), f"deberia ser None: {entrada!r}")

    def test_payout_ilegible_da_error_con_el_dato_crudo(self):
        """Si no se puede leer, el mensaje debe mostrar lo que devolvio el
        broker: sin eso es imposible diagnosticar una API sin contrato."""
        class ApiRara(ApiFalsa):
            def get_all_profit(self):
                return {"EURUSD": {"turbo": {"formato": "inesperado"}}}

        b = broker_conectado(ApiRara())
        with self.assertRaises(BrokerNoDisponible) as ctx:
            b.payout("EURUSD", 300)
        self.assertIn("formato", str(ctx.exception))


class TestMercadoCerrado(unittest.TestCase):
    """Fin de semana: el forex real no cotiza y solo quedan activos OTC.

    Distinguir "no hay datos" de "formato raro" importa: el primero es normal
    un domingo y el segundo es un bug que hay que arreglar.
    """

    class ApiFinDeSemana(ApiFalsa):
        def get_all_open_time(self):
            # Los OTC aparecen en secciones distintas segun la cuenta.
            return {"turbo": {"EURUSD": {"open": False}},
                    "binary": {"EURUSD-OTC": {"open": True}},
                    "digital": {"GBPUSD-OTC": {"open": True}}}

        def get_all_profit(self):
            return {"EURUSD": {}, "EURUSD-OTC": {"turbo": 0.85}}

    def test_respuesta_vacia_dice_que_no_cotiza(self):
        b = broker_conectado(self.ApiFinDeSemana())
        with self.assertRaises(BrokerNoDisponible) as ctx:
            b.payout("EURUSD", 300)
        mensaje = str(ctx.exception)
        self.assertIn("no cotiza", mensaje)
        self.assertIn("OTC", mensaje)

    def test_el_otc_si_da_payout(self):
        b = broker_conectado(self.ApiFinDeSemana())
        self.assertAlmostEqual(b.payout("EURUSD-OTC", 300), 0.85)

    def test_recorre_todas_las_secciones(self):
        """Buscar solo en 'turbo' se perderia los OTC de fin de semana."""
        b = broker_conectado(self.ApiFinDeSemana())
        self.assertEqual(b.activos_abiertos(), ["EURUSD-OTC", "GBPUSD-OTC"])

    def test_seccion_concreta_sigue_funcionando(self):
        b = broker_conectado(self.ApiFinDeSemana())
        self.assertEqual(b.activos_abiertos(tipo="turbo"), [])

    def test_crudo_vuelca_las_dos_llamadas(self):
        b = broker_conectado(self.ApiFinDeSemana())
        datos = b.crudo()
        self.assertEqual(sorted(datos), ["get_all_open_time", "get_all_profit"])

    def test_crudo_no_lanza_si_la_api_falla(self):
        class ApiRota(ApiFalsa):
            def get_all_profit(self): raise RuntimeError("socket cerrado")

        datos = broker_conectado(ApiRota()).crudo()
        self.assertIn("ERROR", str(datos["get_all_profit"]))


class TestDescargaDeHistorico(unittest.TestCase):
    """Los OTC no publican historico en ningun sitio. Sin poder descargarlo,
    la unica evidencia sobre ellos serian un punado de operaciones en vivo."""

    class ApiConHistorico(ApiFalsa):
        def __init__(self, disponibles=5000, **kw):
            super().__init__(**kw)
            self.llamadas_velas = 0
            # Suelo ABSOLUTO de historico: el broker no tiene nada anterior.
            # Calcularlo relativo a cada peticion haria que nunca se agotase.
            ahora = int(time.time())
            self.suelo = ahora - disponibles * 60

        def get_candles(self, actives, interval, count, endtime):
            self.llamadas_velas += 1
            base = endtime - (endtime % interval)
            out = []
            for i in range(count):
                ts = base - i * interval
                if ts < self.suelo:
                    break
                out.append({"from": ts, "open": 1.1, "close": 1.1,
                            "min": 1.09, "max": 1.11, "volume": 10})
            return out

    def test_encadena_lotes_hasta_el_total(self):
        api = self.ApiConHistorico()
        velas = broker_conectado(api).descargar_velas("EURUSD-OTC", 60, total=2500,
                                                      lote=1000, pausa=0)
        self.assertGreaterEqual(len(velas), 2000)
        self.assertGreater(api.llamadas_velas, 1, "no encadeno lotes")

    def test_devuelve_orden_cronologico_sin_duplicados(self):
        velas = broker_conectado(self.ApiConHistorico()).descargar_velas(
            "EURUSD-OTC", 60, total=1500, lote=500, pausa=0)
        ts = [v["from"] for v in velas]
        self.assertEqual(ts, sorted(ts))
        self.assertEqual(len(ts), len(set(ts)))

    def test_para_cuando_el_broker_se_queda_sin_historico(self):
        """Sin esta salida, la descarga giraria en un bucle infinito."""
        api = self.ApiConHistorico(disponibles=300)
        velas = broker_conectado(api).descargar_velas("EURUSD-OTC", 60,
                                                      total=100_000, lote=500, pausa=0)
        self.assertLess(len(velas), 1000)

    def test_error_de_red_se_reporta_claro(self):
        class ApiRota(ApiFalsa):
            def get_candles(self, *a, **k):
                raise RuntimeError("socket cerrado")

        with self.assertRaises(BrokerNoDisponible) as ctx:
            broker_conectado(ApiRota()).descargar_velas("EURUSD-OTC")
        self.assertIn("socket cerrado", str(ctx.exception))
