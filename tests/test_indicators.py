"""Tests de indicadores.

El test que mas valor tiene aqui es `test_sin_look_ahead`: comprueba que el
valor del indicador en el indice i es identico calculado con la serie completa
o solo con los datos hasta i. Si esa propiedad se rompe, cualquier backtest de
este repositorio queda invalidado.
"""

from __future__ import annotations

import math
import random
import unittest

from kronos.core import indicators as ind


def serie_ruido(n: int = 200, seed: int = 7) -> list[float]:
    rng = random.Random(seed)
    p = 100.0
    out = []
    for _ in range(n):
        p *= 1 + rng.gauss(0, 0.004)
        out.append(round(p, 6))
    return out


class TestBasicos(unittest.TestCase):
    def test_sma_valores_conocidos(self):
        v = [1, 2, 3, 4, 5, 6]
        r = ind.sma(v, 3)
        self.assertEqual(r[:2], [None, None])
        self.assertAlmostEqual(r[2], 2.0)
        self.assertAlmostEqual(r[5], 5.0)

    def test_sma_longitud_preservada(self):
        v = serie_ruido(50)
        self.assertEqual(len(ind.sma(v, 10)), 50)

    def test_sma_serie_corta(self):
        self.assertEqual(ind.sma([1, 2], 5), [None, None])

    def test_ema_sembrada_con_sma(self):
        v = [1, 2, 3, 4, 5, 6, 7, 8]
        r = ind.ema(v, 4)
        self.assertAlmostEqual(r[3], 2.5)  # SMA de los 4 primeros
        k = 2 / 5
        self.assertAlmostEqual(r[4], 5 * k + 2.5 * (1 - k))

    def test_ema_converge_a_constante(self):
        r = ind.ema([7.0] * 60, 10)
        self.assertAlmostEqual(r[-1], 7.0, places=9)

    def test_stdev_constante_es_cero(self):
        r = ind.stdev([3.0] * 20, 5)
        self.assertAlmostEqual(r[-1], 0.0)

    def test_stdev_poblacional(self):
        r = ind.stdev([2, 4, 4, 4, 5, 5, 7, 9], 8)
        self.assertAlmostEqual(r[7], 2.0)  # desviacion poblacional conocida

    def test_periodo_invalido(self):
        for fn in (ind.sma, ind.ema, ind.stdev, ind.rsi):
            with self.assertRaises(ValueError):
                fn([1, 2, 3], 0)


class TestRSI(unittest.TestCase):
    def test_rango_valido(self):
        for v in ind.rsi(serie_ruido(300), 14):
            if v is not None:
                self.assertGreaterEqual(v, 0.0)
                self.assertLessEqual(v, 100.0)

    def test_serie_estrictamente_creciente_da_100(self):
        r = ind.rsi([float(i) for i in range(1, 60)], 14)
        self.assertAlmostEqual(r[-1], 100.0, places=6)

    def test_serie_estrictamente_decreciente_da_0(self):
        r = ind.rsi([float(i) for i in range(60, 1, -1)], 14)
        self.assertAlmostEqual(r[-1], 0.0, places=6)

    def test_serie_constante_no_divide_por_cero(self):
        r = ind.rsi([5.0] * 60, 14)
        self.assertAlmostEqual(r[-1], 50.0)

    def test_calentamiento(self):
        r = ind.rsi(serie_ruido(60), 14)
        self.assertTrue(all(x is None for x in r[:14]))
        self.assertIsNotNone(r[14])


class TestBollinger(unittest.TestCase):
    def test_orden_de_bandas(self):
        bb = ind.bollinger(serie_ruido(200), 20, 2.0)
        for i in range(19, 200):
            self.assertLess(bb.lower[i], bb.middle[i])
            self.assertLess(bb.middle[i], bb.upper[i])

    def test_media_coincide_con_sma(self):
        v = serie_ruido(100)
        bb = ind.bollinger(v, 20, 2.0)
        s = ind.sma(v, 20)
        for i in range(19, 100):
            self.assertAlmostEqual(bb.middle[i], s[i], places=9)

    def test_percent_b_en_extremos(self):
        v = serie_ruido(200)
        bb = ind.bollinger(v, 20, 2.0)
        for i in range(19, 200):
            pb_recalc = (v[i] - bb.lower[i]) / (bb.upper[i] - bb.lower[i])
            self.assertAlmostEqual(bb.percent_b[i], pb_recalc, places=9)

    def test_mult_invalido(self):
        with self.assertRaises(ValueError):
            ind.bollinger([1.0] * 30, 20, 0.0)


class TestATRyADX(unittest.TestCase):
    def setUp(self):
        closes = serie_ruido(300)
        self.closes = closes
        self.highs = [c * 1.002 for c in closes]
        self.lows = [c * 0.998 for c in closes]

    def test_atr_no_negativo(self):
        for v in ind.atr(self.highs, self.lows, self.closes, 14):
            if v is not None:
                self.assertGreaterEqual(v, 0.0)

    def test_true_range_incluye_gap(self):
        tr = ind.true_range([10, 20], [9, 19], [9.5, 19.5])
        self.assertAlmostEqual(tr[1], 20 - 9.5)  # gap contra el cierre previo

    def test_adx_en_rango(self):
        a = ind.adx(self.highs, self.lows, self.closes, 14)
        for serie in (a.adx, a.plus_di, a.minus_di):
            for v in serie:
                if v is not None:
                    self.assertGreaterEqual(v, 0.0)
                    self.assertLessEqual(v, 100.0)

    def test_adx_tendencia_fuerte_supera_rango(self):
        n = 200
        subida = [100 + i * 0.5 for i in range(n)]
        highs = [c + 0.2 for c in subida]
        lows = [c - 0.2 for c in subida]
        a_tend = ind.adx(highs, lows, subida, 14)

        plano = [100 + (0.1 if i % 2 else -0.1) for i in range(n)]
        a_plano = ind.adx([c + 0.2 for c in plano], [c - 0.2 for c in plano], plano, 14)
        self.assertGreater(a_tend.adx[-1], a_plano.adx[-1])

    def test_adx_serie_corta(self):
        a = ind.adx([1, 2], [0, 1], [0.5, 1.5], 14)
        self.assertTrue(all(v is None for v in a.adx))


class TestMACDyEstocastico(unittest.TestCase):
    def test_macd_histograma_es_diferencia(self):
        v = serie_ruido(200)
        m = ind.macd(v, 12, 26, 9)
        for i in range(len(v)):
            if m.macd[i] is not None and m.signal[i] is not None:
                self.assertAlmostEqual(m.histogram[i], m.macd[i] - m.signal[i], places=9)

    def test_macd_fast_mayor_que_slow_falla(self):
        with self.assertRaises(ValueError):
            ind.macd(serie_ruido(100), 26, 12)

    def test_estocastico_en_rango(self):
        closes = serie_ruido(200)
        highs = [c * 1.003 for c in closes]
        lows = [c * 0.997 for c in closes]
        st = ind.stochastic(highs, lows, closes)
        for serie in (st.k, st.d):
            for v in serie:
                if v is not None:
                    self.assertGreaterEqual(v, 0.0)
                    self.assertLessEqual(v, 100.0)

    def test_estocastico_rango_plano(self):
        st = ind.stochastic([1.0] * 50, [1.0] * 50, [1.0] * 50)
        self.assertAlmostEqual(st.k[-1], 50.0)


class TestSinLookAhead(unittest.TestCase):
    """Ningun indicador puede cambiar su valor pasado al llegar datos nuevos."""

    def setUp(self):
        self.closes = serie_ruido(160, seed=11)
        self.highs = [c * 1.0025 for c in self.closes]
        self.lows = [c * 0.9975 for c in self.closes]

    def _comparar(self, nombre, completo, parcial, i):
        a, b = completo[i], parcial[i]
        if a is None and b is None:
            return
        self.assertIsNotNone(a, f"{nombre}: completo None en {i}")
        self.assertIsNotNone(b, f"{nombre}: parcial None en {i}")
        self.assertAlmostEqual(a, b, places=9, msg=f"{nombre} difiere en el indice {i}")

    def test_sin_look_ahead(self):
        n = len(self.closes)
        completos = {
            "sma": ind.sma(self.closes, 20),
            "ema": ind.ema(self.closes, 21),
            "rsi": ind.rsi(self.closes, 14),
            "stdev": ind.stdev(self.closes, 20),
            "atr": ind.atr(self.highs, self.lows, self.closes, 14),
        }
        bb_full = ind.bollinger(self.closes, 20, 2.0)
        adx_full = ind.adx(self.highs, self.lows, self.closes, 14)
        macd_full = ind.macd(self.closes)
        st_full = ind.stochastic(self.highs, self.lows, self.closes)

        for corte in (80, 110, 140, n):
            c, h, l = self.closes[:corte], self.highs[:corte], self.lows[:corte]
            i = corte - 1
            self._comparar("sma", completos["sma"], ind.sma(c, 20), i)
            self._comparar("ema", completos["ema"], ind.ema(c, 21), i)
            self._comparar("rsi", completos["rsi"], ind.rsi(c, 14), i)
            self._comparar("stdev", completos["stdev"], ind.stdev(c, 20), i)
            self._comparar("atr", completos["atr"], ind.atr(h, l, c, 14), i)
            self._comparar("bb.upper", bb_full.upper, ind.bollinger(c, 20, 2.0).upper, i)
            self._comparar("bb.percent_b", bb_full.percent_b, ind.bollinger(c, 20, 2.0).percent_b, i)
            self._comparar("adx", adx_full.adx, ind.adx(h, l, c, 14).adx, i)
            self._comparar("macd.hist", macd_full.histogram, ind.macd(c).histogram, i)
            self._comparar("stoch.k", st_full.k, ind.stochastic(h, l, c).k, i)
            self._comparar("stoch.d", st_full.d, ind.stochastic(h, l, c).d, i)


class TestDegenerados(unittest.TestCase):
    def test_series_vacias(self):
        self.assertEqual(ind.sma([], 5), [])
        self.assertEqual(ind.rsi([], 14), [])
        self.assertEqual(ind.true_range([], [], []), [])
        self.assertEqual(len(ind.bollinger([], 20)), 0)

    def test_un_solo_valor(self):
        self.assertEqual(ind.sma([1.0], 5), [None])
        self.assertEqual(ind.true_range([2.0], [1.0], [1.5]), [1.0])

    def test_slope(self):
        s = ind.slope([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], 5)
        self.assertAlmostEqual(s[5], 5.0)
        self.assertTrue(all(v is None for v in s[:5]))

    def test_todo_finito(self):
        v = serie_ruido(200)
        for serie in (ind.sma(v, 20), ind.ema(v, 20), ind.rsi(v, 14), ind.stdev(v, 20)):
            for x in serie:
                if x is not None:
                    self.assertTrue(math.isfinite(x))


if __name__ == "__main__":
    unittest.main()
