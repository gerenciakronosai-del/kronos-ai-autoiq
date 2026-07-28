"""Tests de carga de datos, estructuras de mercado y broker simulado."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kronos.broker.base import EstadoOrden, TipoCuenta
from kronos.broker.paper import PaperBroker
from kronos.core.candle import Candle, Series
from kronos.data import loader, synthetic
from kronos.strategy.base import Decision


class TestCandle(unittest.TestCase):
    def test_propiedades(self):
        c = Candle(ts=1_700_000_000, open=1.0, high=1.2, low=0.9, close=1.1, volume=10)
        self.assertTrue(c.is_bull)
        self.assertAlmostEqual(c.body, 0.1)
        self.assertAlmostEqual(c.range, 0.3)
        self.assertAlmostEqual(c.typical, (1.2 + 0.9 + 1.1) / 3)

    def test_high_menor_que_low_falla(self):
        with self.assertRaises(ValueError):
            Candle(ts=1, open=1.0, high=0.5, low=1.5, close=1.0)

    def test_cuerpo_fuera_de_rango_falla(self):
        with self.assertRaises(ValueError):
            Candle(ts=1, open=2.0, high=1.5, low=1.0, close=1.2)
        with self.assertRaises(ValueError):
            Candle(ts=1, open=1.2, high=1.5, low=1.0, close=0.5)

    def test_es_inmutable(self):
        c = Candle(ts=1, open=1.0, high=1.0, low=1.0, close=1.0)
        with self.assertRaises(Exception):
            c.close = 2.0  # type: ignore[misc]

    def test_dt_es_utc(self):
        self.assertEqual(Candle(ts=0, open=1, high=1, low=1, close=1).dt.year, 1970)


class TestSeries(unittest.TestCase):
    def setUp(self):
        self.s = synthetic.generate(synthetic.SyntheticParams(n=100), seed=1)

    def test_orden_obligatorio(self):
        velas = [
            Candle(ts=200, open=1, high=1, low=1, close=1),
            Candle(ts=100, open=1, high=1, low=1, close=1),
        ]
        with self.assertRaises(ValueError):
            Series(velas)

    def test_timestamps_duplicados_fallan(self):
        velas = [Candle(ts=100, open=1, high=1, low=1, close=1)] * 2
        with self.assertRaises(ValueError):
            Series(velas)

    def test_unchecked_no_valida(self):
        velas = [
            Candle(ts=200, open=1, high=1, low=1, close=1),
            Candle(ts=100, open=1, high=1, low=1, close=1),
        ]
        self.assertEqual(len(Series.unchecked(velas)), 2)

    def test_slicing_devuelve_series(self):
        sub = self.s[10:20]
        self.assertIsInstance(sub, Series)
        self.assertEqual(len(sub), 10)
        self.assertEqual(sub.symbol, self.s.symbol)

    def test_accesos_vectorizados(self):
        self.assertEqual(len(self.s.closes), 100)
        self.assertEqual(self.s.closes[0], self.s[0].close)
        self.assertEqual(self.s.highs[-1], self.s[-1].high)

    def test_tail(self):
        self.assertEqual(len(self.s.tail(20)), 20)
        self.assertEqual(len(self.s.tail(500)), 100)

    def test_serie_vacia(self):
        vacia = Series([])
        self.assertEqual(len(vacia), 0)
        self.assertEqual(vacia.closes, [])


class TestSintetico(unittest.TestCase):
    def test_reproducible_con_seed(self):
        a = synthetic.generate(synthetic.SyntheticParams(n=200), seed=99)
        b = synthetic.generate(synthetic.SyntheticParams(n=200), seed=99)
        self.assertEqual(a.closes, b.closes)

    def test_seeds_distintas_dan_series_distintas(self):
        a = synthetic.generate(synthetic.SyntheticParams(n=200), seed=1)
        b = synthetic.generate(synthetic.SyntheticParams(n=200), seed=2)
        self.assertNotEqual(a.closes, b.closes)

    def test_ohlc_coherente(self):
        for c in synthetic.generate(synthetic.SyntheticParams(n=500), seed=3):
            self.assertGreaterEqual(c.high, max(c.open, c.close))
            self.assertLessEqual(c.low, min(c.open, c.close))
            self.assertGreater(c.close, 0)

    def test_timestamps_regulares(self):
        s = synthetic.generate(synthetic.SyntheticParams(n=50, timeframe=300), seed=4)
        for a, b in zip(s, s[1:]):
            self.assertEqual(b.ts - a.ts, 300)

    def test_parametros_invalidos(self):
        for kwargs in ({"n": 0}, {"ticks_por_vela": 1}, {"vol_base": 0.0}):
            with self.assertRaises(ValueError):
                synthetic.SyntheticParams(**kwargs)


class TestTimestamps(unittest.TestCase):
    def test_epoch_segundos(self):
        self.assertEqual(loader.parse_timestamp("1700000000"), 1700000000)

    def test_epoch_milisegundos(self):
        self.assertEqual(loader.parse_timestamp("1700000000000"), 1700000000)

    def test_epoch_microsegundos(self):
        self.assertEqual(loader.parse_timestamp("1700000000000000"), 1700000000)

    def test_iso_con_z(self):
        self.assertEqual(loader.parse_timestamp("2023-11-14T22:13:20Z"), 1700000000)

    def test_iso_sin_zona_se_asume_utc(self):
        self.assertEqual(loader.parse_timestamp("2023-11-14 22:13:20"), 1700000000)

    def test_basura_falla(self):
        for malo in ("", "   ", "no-es-fecha"):
            with self.assertRaises(loader.LoaderError):
                loader.parse_timestamp(malo)


class TestLoader(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.ruta = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def _escribir(self, nombre: str, texto: str) -> Path:
        p = self.ruta / nombre
        p.write_text(texto, encoding="utf-8")
        return p

    def test_ida_y_vuelta_csv(self):
        original = synthetic.generate(synthetic.SyntheticParams(n=120), seed=5)
        destino = loader.save_csv(original, self.ruta / "v.csv")
        recargada = loader.load_csv(destino, symbol=original.symbol)
        self.assertEqual(len(recargada), len(original))
        self.assertEqual(recargada.closes, original.closes)
        self.assertEqual(recargada.timeframe, original.timeframe)

    def test_alias_de_columnas(self):
        p = self._escribir("alias.csv", "time,o,h,l,c,vol\n1700000000,1,2,0.5,1.5,10\n"
                                        "1700000060,1.5,2,1,1.8,12\n")
        s = loader.load_csv(p)
        self.assertEqual(len(s), 2)
        self.assertAlmostEqual(s[0].high, 2.0)

    def test_columnas_en_espanol(self):
        p = self._escribir("es.csv", "fecha,apertura,maximo,minimo,cierre\n")
        with self.assertRaises(loader.LoaderError):
            loader.load_csv(p)  # 'fecha' no esta en los alias de timestamp

    def test_falta_columna_obligatoria(self):
        p = self._escribir("malo.csv", "timestamp,open,high\n1700000000,1,2\n")
        with self.assertRaises(loader.LoaderError):
            loader.load_csv(p)

    def test_fichero_inexistente(self):
        with self.assertRaises(loader.LoaderError):
            loader.load_csv(self.ruta / "no-existe.csv")

    def test_fichero_vacio(self):
        with self.assertRaises(loader.LoaderError):
            loader.load_csv(self._escribir("vacio.csv", ""))

    def test_sin_filas(self):
        with self.assertRaises(loader.LoaderError):
            loader.load_csv(self._escribir("solo.csv", "timestamp,open,high,low,close\n"))

    def test_fila_corrupta_indica_la_linea(self):
        p = self._escribir("corr.csv", "timestamp,open,high,low,close\n"
                                       "1700000000,1,2,0.5,1.5\n"
                                       "1700000060,X,2,1,1.8\n")
        with self.assertRaises(loader.LoaderError) as ctx:
            loader.load_csv(p)
        self.assertIn(":3", str(ctx.exception))

    def test_ordena_y_deduplica(self):
        p = self._escribir("des.csv", "timestamp,open,high,low,close\n"
                                      "1700000060,1,2,0.5,1.5\n"
                                      "1700000000,1,2,0.5,1.2\n"
                                      "1700000060,1,2,0.5,1.9\n")
        s = loader.load_csv(p)
        self.assertEqual(len(s), 2)
        self.assertEqual(s.timestamps, [1700000000, 1700000060])
        self.assertAlmostEqual(s[1].close, 1.9)  # conserva la ultima repetida

    def test_filas_en_blanco_se_ignoran(self):
        p = self._escribir("blanco.csv", "timestamp,open,high,low,close\n"
                                         "1700000000,1,2,0.5,1.5\n\n"
                                         "1700000060,1.5,2,1,1.8\n")
        self.assertEqual(len(loader.load_csv(p)), 2)

    def test_json_lista_de_objetos(self):
        p = self.ruta / "v.json"
        p.write_text(json.dumps([
            {"timestamp": 1700000000, "open": 1, "high": 2, "low": 0.5, "close": 1.5},
            {"timestamp": 1700000060, "open": 1.5, "high": 2, "low": 1, "close": 1.8},
        ]), encoding="utf-8")
        self.assertEqual(len(loader.load_json(p)), 2)

    def test_json_lista_de_listas(self):
        p = self.ruta / "v2.json"
        p.write_text(json.dumps([
            [1700000000, 1, 2, 0.5, 1.5, 10],
            [1700000060, 1.5, 2, 1, 1.8, 12],
        ]), encoding="utf-8")
        s = loader.load_json(p)
        self.assertEqual(len(s), 2)
        self.assertAlmostEqual(s[1].volume, 12)

    def test_inferencia_de_timeframe(self):
        original = synthetic.generate(synthetic.SyntheticParams(n=50, timeframe=300), seed=6)
        recargada = loader.load_csv(loader.save_csv(original, self.ruta / "tf.csv"))
        self.assertEqual(recargada.timeframe, 300)

    def test_replay_ventana_rodante(self):
        s = synthetic.generate(synthetic.SyntheticParams(n=60), seed=7)
        ventanas = list(loader.iter_replay(s, 20))
        self.assertEqual(len(ventanas), 41)
        self.assertTrue(all(len(v) == 20 for v in ventanas))


HISTDATA_M1 = (
    "20230102 000000;1.06997;1.07012;1.06995;1.07004;0\n"
    "20230102 000100;1.07004;1.07020;1.07001;1.07018;0\n"
    "20230102 000200;1.07018;1.07025;1.07010;1.07011;0\n"
)


class TestHistData(unittest.TestCase):
    """Formato Generic ASCII M1 de HistData.com."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.ruta = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_timestamp_compacto(self):
        # 2023-01-02 00:00:00 UTC
        self.assertEqual(loader.parse_timestamp("20230102 000000"), 1672617600)

    def test_csv_suelto(self):
        p = self.ruta / "DAT_ASCII_EURUSD_M1_202301.csv"
        p.write_text(HISTDATA_M1, encoding="utf-8")
        s = loader.load_histdata(p)
        self.assertEqual(len(s), 3)
        self.assertAlmostEqual(s[0].open, 1.06997)
        self.assertAlmostEqual(s[2].close, 1.07011)
        self.assertEqual(s.timeframe, 60)

    def test_conversion_est_a_utc(self):
        """EST sin DST es UTC-5: la hora 00:00 del fichero son las 05:00 UTC."""
        p = self.ruta / "a.csv"
        p.write_text(HISTDATA_M1, encoding="utf-8")
        s = loader.load_histdata(p)
        self.assertEqual(s[0].dt.hour, 5)
        self.assertEqual(s[0].dt.day, 2)

    def test_sin_conversion_si_offset_cero(self):
        p = self.ruta / "a.csv"
        p.write_text(HISTDATA_M1, encoding="utf-8")
        self.assertEqual(loader.load_histdata(p, tz_offset_horas=0)[0].dt.hour, 0)

    def test_zip(self):
        import zipfile
        z = self.ruta / "DAT_ASCII_EURUSD_M1_202301.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("DAT_ASCII_EURUSD_M1_202301.csv", HISTDATA_M1)
        self.assertEqual(len(loader.load_histdata(z)), 3)

    def test_zip_real_ignora_el_informe_txt(self):
        """Los zips de HistData traen un .txt con un informe de huecos.

        No son datos y hay que ignorarlo: si se intenta parsear, sus lineas de
        texto rompen la carga entera.
        """
        import zipfile
        z = self.ruta / "HISTDATA_COM_ASCII_EURUSD_M12025.zip"
        informe = (
            "HistData.com (c) 2012\n"
            "File: DAT_ASCII_EURUSD_M1_2025.csv Status Report\n\n"
            "Gap of 128s found between 20250101170019 and 20250101170234.\n"
        )
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("DAT_ASCII_EURUSD_M1_2025.csv", HISTDATA_M1)
            zf.writestr("DAT_ASCII_EURUSD_M1_2025.txt", informe)
        s = loader.load_histdata(z)
        self.assertEqual(len(s), 3, "el .txt no debe aportar ni duplicar velas")

    def test_lineas_de_texto_sueltas_se_ignoran(self):
        p = self.ruta / "mixto.csv"
        p.write_text("HistData.com (c) 2012\nStatus Report\n" + HISTDATA_M1, encoding="utf-8")
        self.assertEqual(len(loader.load_histdata(p)), 3)

    def test_carpeta_fusiona_y_ordena(self):
        import zipfile
        (self.ruta / "b.csv").write_text(
            "20230102 000300;1.07011;1.07030;1.07005;1.07028;0\n", encoding="utf-8")
        z = self.ruta / "a.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("a.csv", HISTDATA_M1)
        s = loader.load_histdata(self.ruta)
        self.assertEqual(len(s), 4)
        self.assertEqual(s.timestamps, sorted(s.timestamps))

    def test_separador_por_comas(self):
        p = self.ruta / "c.csv"
        p.write_text(HISTDATA_M1.replace(";", ","), encoding="utf-8")
        self.assertEqual(len(loader.load_histdata(p)), 3)

    def test_cabecera_opcional_se_ignora(self):
        p = self.ruta / "d.csv"
        p.write_text("DateTime;Open;High;Low;Close;Volume\n" + HISTDATA_M1, encoding="utf-8")
        self.assertEqual(len(loader.load_histdata(p)), 3)

    def test_volumen_cero_es_normal_en_forex(self):
        p = self.ruta / "e.csv"
        p.write_text(HISTDATA_M1, encoding="utf-8")
        self.assertTrue(all(c.volume == 0.0 for c in loader.load_histdata(p)))

    def test_fila_corta_falla_con_linea(self):
        p = self.ruta / "f.csv"
        p.write_text("20230102 000000;1.07;1.07\n", encoding="utf-8")
        with self.assertRaises(loader.LoaderError) as ctx:
            loader.load_histdata(p)
        self.assertIn(":1", str(ctx.exception))

    def test_origen_inexistente(self):
        with self.assertRaises(loader.LoaderError):
            loader.load_histdata(self.ruta / "no-existe.zip")

    def test_carpeta_vacia(self):
        with self.assertRaises(loader.LoaderError):
            loader.load_histdata(self.ruta)

    def test_ida_y_vuelta_al_csv_canonico(self):
        p = self.ruta / "g.csv"
        p.write_text(HISTDATA_M1, encoding="utf-8")
        original = loader.load_histdata(p)
        recargada = loader.load_csv(loader.save_csv(original, self.ruta / "canon.csv"))
        self.assertEqual(recargada.timestamps, original.timestamps)
        self.assertEqual(recargada.closes, original.closes)


class TestPaperBroker(unittest.TestCase):
    """Mecanica de liquidacion. Spread a cero para aislarla del coste de entrada."""

    def setUp(self):
        self.b = PaperBroker(balance_inicial=1000.0, payout_por_defecto=0.80,
                             spread_pips=0.0)
        self.b.conectar()

    def test_cuenta_es_demo(self):
        self.assertIs(self.b.tipo_cuenta, TipoCuenta.DEMO)

    def test_comprar_sin_conectar_falla(self):
        b = PaperBroker()
        b.marcar_precio("X", 1.0, 0)
        with self.assertRaises(RuntimeError):
            b.comprar("X", Decision.CALL, 10.0, 60)

    def test_comprar_sin_precio_falla(self):
        with self.assertRaises(RuntimeError):
            self.b.comprar("SIN-PRECIO", Decision.CALL, 10.0, 60)

    def test_call_ganadora(self):
        self.b.marcar_precio("X", 1.0000, 0)
        o = self.b.comprar("X", Decision.CALL, 10.0, 60)
        self.assertAlmostEqual(self.b.balance(), 990.0)
        cerradas = self.b.marcar_precio("X", 1.0010, 60)
        self.assertEqual(len(cerradas), 1)
        self.assertIs(cerradas[0].estado, EstadoOrden.GANADA)
        self.assertAlmostEqual(cerradas[0].pnl, 8.0)
        self.assertAlmostEqual(self.b.balance(), 1008.0)

    def test_put_ganadora(self):
        self.b.marcar_precio("X", 1.0000, 0)
        self.b.comprar("X", Decision.PUT, 10.0, 60)
        cerradas = self.b.marcar_precio("X", 0.9990, 60)
        self.assertIs(cerradas[0].estado, EstadoOrden.GANADA)
        self.assertAlmostEqual(self.b.balance(), 1008.0)

    def test_perdedora(self):
        self.b.marcar_precio("X", 1.0000, 0)
        self.b.comprar("X", Decision.CALL, 10.0, 60)
        cerradas = self.b.marcar_precio("X", 0.9990, 60)
        self.assertIs(cerradas[0].estado, EstadoOrden.PERDIDA)
        self.assertAlmostEqual(cerradas[0].pnl, -10.0)
        self.assertAlmostEqual(self.b.balance(), 990.0)

    def test_empate_devuelve_stake(self):
        self.b.marcar_precio("X", 1.0000, 0)
        self.b.comprar("X", Decision.CALL, 10.0, 60)
        cerradas = self.b.marcar_precio("X", 1.0000, 60)
        self.assertIs(cerradas[0].estado, EstadoOrden.EMPATE)
        self.assertAlmostEqual(self.b.balance(), 1000.0)

    def test_no_vence_antes_de_tiempo(self):
        self.b.marcar_precio("X", 1.0, 0)
        self.b.comprar("X", Decision.CALL, 10.0, 300)
        self.assertEqual(self.b.marcar_precio("X", 1.1, 120), [])
        self.assertEqual(len(self.b.abiertas), 1)

    def test_stake_mayor_que_balance_se_rechaza(self):
        self.b.marcar_precio("X", 1.0, 0)
        o = self.b.comprar("X", Decision.CALL, 5000.0, 60)
        self.assertIs(o.estado, EstadoOrden.RECHAZADA)
        self.assertAlmostEqual(self.b.balance(), 1000.0)

    def test_esperar_no_es_comprable(self):
        self.b.marcar_precio("X", 1.0, 0)
        with self.assertRaises(ValueError):
            self.b.comprar("X", Decision.ESPERAR, 10.0, 60)

    def test_stake_no_positivo(self):
        self.b.marcar_precio("X", 1.0, 0)
        with self.assertRaises(ValueError):
            self.b.comprar("X", Decision.CALL, 0.0, 60)

    def test_payout_por_activo(self):
        b = PaperBroker(payouts={"RARO": 0.5})
        self.assertAlmostEqual(b.payout("RARO", 60), 0.5)
        self.assertAlmostEqual(b.payout("OTRO", 60), 0.80)

    def test_context_manager(self):
        with PaperBroker() as b:
            b.marcar_precio("X", 1.0, 0)
            self.assertIsNotNone(b.comprar("X", Decision.CALL, 10.0, 60).id)


class TestSpreadDelBroker(unittest.TestCase):
    """El spread es el coste que decide si una estrategia vive o muere.

    La exploracion sobre 744k velas reales mostro que en horizontes de 1-10
    minutos basta con 0.2 pips para borrar toda la ventaja medible. Un broker
    simulado sin spread miente a favor.
    """

    def _broker(self, spread: float) -> PaperBroker:
        b = PaperBroker(balance_inicial=1000.0, payout_por_defecto=0.80,
                        spread_pips=spread)
        b.conectar()
        return b

    def test_por_defecto_hay_spread(self):
        self.assertGreater(PaperBroker().spread_pips, 0.0)

    def test_call_entra_por_encima_del_precio(self):
        b = self._broker(1.0)
        b.marcar_precio("X", 1.10000, 0)
        o = b.comprar("X", Decision.CALL, 10.0, 60)
        self.assertAlmostEqual(o.precio_entrada, 1.10010, places=6)

    def test_put_entra_por_debajo_del_precio(self):
        b = self._broker(1.0)
        b.marcar_precio("X", 1.10000, 0)
        o = b.comprar("X", Decision.PUT, 10.0, 60)
        self.assertAlmostEqual(o.precio_entrada, 1.09990, places=6)

    def test_un_movimiento_menor_que_el_spread_pierde(self):
        """El caso que arruina las estrategias de horizonte corto."""
        b = self._broker(1.0)
        b.marcar_precio("X", 1.10000, 0)
        b.comprar("X", Decision.CALL, 10.0, 60)
        # El precio sube 0.5 pips: a favor, pero menos que el spread pagado.
        cerradas = b.marcar_precio("X", 1.10005, 60)
        self.assertIs(cerradas[0].estado, EstadoOrden.PERDIDA)

    def test_el_mismo_movimiento_gana_sin_spread(self):
        b = self._broker(0.0)
        b.marcar_precio("X", 1.10000, 0)
        b.comprar("X", Decision.CALL, 10.0, 60)
        cerradas = b.marcar_precio("X", 1.10005, 60)
        self.assertIs(cerradas[0].estado, EstadoOrden.GANADA)

    def test_mas_spread_nunca_mejora(self):
        resultados = []
        for spread in (0.0, 0.5, 1.0, 2.0):
            b = self._broker(spread)
            b.marcar_precio("X", 1.10000, 0)
            b.comprar("X", Decision.CALL, 10.0, 60)
            b.marcar_precio("X", 1.10012, 60)
            resultados.append(b.balance())
        for antes, despues in zip(resultados, resultados[1:]):
            self.assertLessEqual(despues, antes)

    def test_spread_negativo_falla(self):
        with self.assertRaises(ValueError):
            PaperBroker(spread_pips=-1.0)


class TestAdaptadorIQOption(unittest.TestCase):
    def test_cuenta_real_bloqueada_sin_variable_de_entorno(self):
        import os
        from kronos.broker.base import TipoCuenta
        from kronos.broker.iqoption import BrokerNoDisponible, IQOptionBroker

        previo = os.environ.pop("KRONOS_ALLOW_REAL", None)
        try:
            with self.assertRaises(BrokerNoDisponible):
                IQOptionBroker(tipo_cuenta=TipoCuenta.REAL)
        finally:
            if previo is not None:
                os.environ["KRONOS_ALLOW_REAL"] = previo

    def test_demo_se_construye_sin_conectar(self):
        from kronos.broker.iqoption import IQOptionBroker
        b = IQOptionBroker()
        self.assertIs(b.tipo_cuenta, TipoCuenta.DEMO)

    def test_operar_sin_conectar_falla(self):
        from kronos.broker.iqoption import BrokerNoDisponible, IQOptionBroker
        with self.assertRaises(BrokerNoDisponible):
            IQOptionBroker().balance()


if __name__ == "__main__":
    unittest.main()


class TestReagrupar(unittest.TestCase):
    """Alargar el horizonte es la palanca contra el spread: este es fijo en pips
    y el movimiento del precio crece con la raiz del tiempo."""

    # Alineado a hora en punto: reagrupar usa fronteras de reloj, no la primera
    # vela como origen. Una vela horaria debe empezar en :00.
    BASE_TS = 1_699_999_200  # multiplo exacto de 3600

    def _serie_minutos(self, n=240):
        velas = []
        for i in range(n):
            apertura = 1.0 + i * 0.001
            cierre = 1.0 + (i + 1) * 0.001
            velas.append(Candle(
                ts=self.BASE_TS + i * 60, open=apertura, close=cierre,
                # Los extremos tienen que envolver al cuerpo o la vela es invalida.
                high=max(apertura, cierre) + 0.0005,
                low=min(apertura, cierre) - 0.0005,
                volume=1.0,
            ))
        return Series(velas, symbol="X", timeframe=60)

    def test_agrupa_el_numero_correcto(self):
        r = loader.reagrupar(self._serie_minutos(240), 3600)
        self.assertEqual(len(r), 4)
        self.assertEqual(r.timeframe, 3600)

    def test_ohlc_del_grupo(self):
        s = self._serie_minutos(120)
        r = loader.reagrupar(s, 3600)
        primera = list(s)[:60]
        self.assertAlmostEqual(r[0].open, primera[0].open)
        self.assertAlmostEqual(r[0].close, primera[-1].close)
        self.assertAlmostEqual(r[0].high, max(c.high for c in primera))
        self.assertAlmostEqual(r[0].low, min(c.low for c in primera))

    def test_ohlc_sigue_siendo_coherente(self):
        for tf in (300, 900, 3600):
            for c in loader.reagrupar(self._serie_minutos(600), tf):
                self.assertGreaterEqual(c.high, max(c.open, c.close))
                self.assertLessEqual(c.low, min(c.open, c.close))

    def test_volumen_se_suma(self):
        r = loader.reagrupar(self._serie_minutos(120), 3600)
        self.assertAlmostEqual(r[0].volume, 60.0)

    def test_timeframe_menor_o_igual_falla(self):
        s = self._serie_minutos(120)
        for tf in (60, 30):
            with self.assertRaises(ValueError):
                loader.reagrupar(s, tf)

    def test_timeframe_no_multiplo_falla(self):
        with self.assertRaises(ValueError):
            loader.reagrupar(self._serie_minutos(120), 90)
