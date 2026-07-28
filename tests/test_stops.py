"""Tests del backtest con stop y objetivo.

Lo que aqui se protege es la convencion pesimista: si en una misma vela se tocan
stop y objetivo, cuenta STOP. Es la causa numero uno de backtests de stops que
lucen bien y no se reproducen en real, porque con OHLC no se sabe cual llego
primero.
"""

from __future__ import annotations

import unittest

from kronos.backtest.stops import ResultadoStops, evaluar_con_stops, informe
from kronos.core.candle import Candle, Series


def serie(velas: list[tuple[float, float, float, float]], tf: int = 3600) -> Series:
    """Construye una serie desde tuplas (open, high, low, close)."""
    return Series(
        [Candle(ts=1_700_000_000 + i * tf, open=o, high=h, low=l, close=c)
         for i, (o, h, l, c) in enumerate(velas)],
        symbol="TEST", timeframe=tf,
    )


def serie_estable(n: int, precio: float = 100.0, rango: float = 1.0) -> list[tuple]:
    """Velas planas que fijan un ATR conocido y predecible."""
    return [(precio, precio + rango / 2, precio - rango / 2, precio)] * n


class TestUmbralYEsperanza(unittest.TestCase):
    """La razon de ser del modo stops: el umbral lo eliges tu, no el broker."""

    def test_umbral_depende_del_ratio(self):
        for rr, esperado in ((0.84, 1 / 1.84), (1.0, 0.5), (2.0, 1 / 3), (3.0, 0.25)):
            self.assertAlmostEqual(ResultadoStops("x", rr=rr).umbral, esperado, places=9)

    def test_binarias_son_el_peor_ratio(self):
        """Con 0.84:1 hace falta 54.35%; con 2:1 basta 33.3%."""
        self.assertGreater(ResultadoStops("x", rr=0.84).umbral,
                           ResultadoStops("x", rr=2.0).umbral)

    def test_esperanza_nula_en_el_umbral(self):
        for rr in (1.0, 2.0, 3.0):
            r = ResultadoStops("x", rr=rr)
            n = 1000
            r.ganadas = round(n * r.umbral)
            r.perdidas = n - r.ganadas
            self.assertAlmostEqual(r.esperanza_r, 0.0, places=2)

    def test_el_coste_resta_esperanza(self):
        base = ResultadoStops("x", ganadas=40, perdidas=60, rr=2.0)
        conc = ResultadoStops("x", ganadas=40, perdidas=60, rr=2.0, coste_r=0.18)
        self.assertAlmostEqual(conc.esperanza_r, base.esperanza_r - 0.18, places=9)


class TestResolucion(unittest.TestCase):
    def test_objetivo_alcanzado_es_ganada(self):
        velas = serie_estable(30) + [(100, 130, 99.5, 129)]
        r = evaluar_con_stops(serie(velas), [0] * 30 + [1, 0], rr=2.0, atr_mult=1.5,
                              spread_pips=0.0)
        self.assertEqual((r.ganadas, r.perdidas), (0, 0))  # la señal va en la ultima

    def test_stop_alcanzado_es_perdida(self):
        velas = serie_estable(30) + [(100, 100.5, 99.5, 100), (100, 100.5, 90, 91)]
        r = evaluar_con_stops(serie(velas), [0] * 30 + [1, 0], rr=2.0, atr_mult=1.5,
                              spread_pips=0.0)
        self.assertEqual(r.perdidas, 1)
        self.assertEqual(r.ganadas, 0)

    def test_objetivo_en_vela_posterior(self):
        velas = serie_estable(30) + [(100, 100.5, 99.5, 100), (100, 110, 99.8, 109)]
        r = evaluar_con_stops(serie(velas), [0] * 30 + [1, 0], rr=2.0, atr_mult=1.5,
                              spread_pips=0.0)
        self.assertEqual(r.ganadas, 1)

    def test_sin_resolver_si_no_toca_ninguno(self):
        velas = serie_estable(40)
        r = evaluar_con_stops(serie(velas), [0] * 30 + [1] + [0] * 9, rr=2.0,
                              atr_mult=1.5, max_velas=5, spread_pips=0.0)
        self.assertEqual(r.sin_resolver, 1)
        self.assertEqual(r.n, 0)

    def test_senal_cero_no_opera(self):
        r = evaluar_con_stops(serie(serie_estable(50)), [0] * 50, rr=2.0)
        self.assertEqual(r.n, 0)


class TestConvencionPesimista(unittest.TestCase):
    """El test que mas importa de este modulo."""

    def test_si_caben_los_dos_gana_el_stop(self):
        # Vela que abarca tanto el stop como el objetivo de un CALL.
        velas = serie_estable(30) + [(100, 100.5, 99.5, 100), (100, 130, 80, 100)]
        r = evaluar_con_stops(serie(velas), [0] * 30 + [1, 0], rr=2.0, atr_mult=1.5,
                              spread_pips=0.0)
        self.assertEqual(r.perdidas, 1, "con ambos tocados debe contar PERDIDA")
        self.assertEqual(r.ganadas, 0)

    def test_lo_mismo_para_un_put(self):
        velas = serie_estable(30) + [(100, 100.5, 99.5, 100), (100, 130, 80, 100)]
        r = evaluar_con_stops(serie(velas), [0] * 30 + [-1, 0], rr=2.0, atr_mult=1.5,
                              spread_pips=0.0)
        self.assertEqual(r.perdidas, 1)


class TestCostes(unittest.TestCase):
    def test_el_coste_nunca_mejora(self):
        velas = serie_estable(30) + [(100, 100.5, 99.5, 100), (100, 110, 99.9, 109)]
        s = serie(velas)
        sen = [0] * 30 + [1, 0]
        sin = evaluar_con_stops(s, sen, rr=2.0, atr_mult=1.5, spread_pips=0.0)
        con = evaluar_con_stops(s, sen, rr=2.0, atr_mult=1.5, spread_pips=50.0)
        self.assertLessEqual(con.esperanza_r, sin.esperanza_r)

    def test_coste_en_R_se_registra(self):
        velas = serie_estable(30) + [(100, 100.5, 99.5, 100), (100, 110, 99.9, 109)]
        r = evaluar_con_stops(serie(velas), [0] * 30 + [1, 0], rr=2.0, atr_mult=1.5,
                              spread_pips=10.0, valor_pip=0.01)
        self.assertGreater(r.coste_r, 0.0)


class TestParametros(unittest.TestCase):
    def test_rr_invalido(self):
        with self.assertRaises(ValueError):
            evaluar_con_stops(serie(serie_estable(40)), [0] * 40, rr=0.0)

    def test_atr_mult_invalido(self):
        with self.assertRaises(ValueError):
            evaluar_con_stops(serie(serie_estable(40)), [0] * 40, atr_mult=0.0)

    def test_informe_es_ascii(self):
        r = ResultadoStops("prueba", ganadas=40, perdidas=60, rr=2.0)
        informe([r]).encode("ascii")


if __name__ == "__main__":
    unittest.main()
