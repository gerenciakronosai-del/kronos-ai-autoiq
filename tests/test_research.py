"""Tests del barrido de hipotesis.

El riesgo de una herramienta de exploracion no es que falle, es que mienta:
que declare ganadora una hipotesis que solo funciona en el pasado. Los tests
que mas importan aqui son los que verifican las salvaguardas — correccion por
test multiple, exigencia de fuera de muestra y modelado del spread.
"""

from __future__ import annotations

import unittest

from kronos.core.candle import Candle, Series
from kronos.data import synthetic
from kronos.research.barrido import Hallazgo, Resultado, barrer, evaluar, informe, por_hora
from kronos.research.hipotesis import (
    CATALOGO, aleatoria, momentum_1v, reversion_1v, rsi_extremo, siempre_call, siempre_put,
)


def serie_creciente(n: int = 200, paso: float = 0.001) -> Series:
    velas = []
    for i in range(n):
        c = 1.0 + i * paso
        o = 1.0 + (i - 1) * paso if i else c
        velas.append(Candle(ts=1_700_000_000 + i * 60, open=o, high=max(o, c) + 1e-5,
                            low=min(o, c) - 1e-5, close=c))
    return Series(velas, symbol="SUBE", timeframe=60)


class TestEvaluar(unittest.TestCase):
    def test_call_en_serie_creciente_acierta_siempre(self):
        s = serie_creciente()
        r = evaluar(s, siempre_call(s), expiry=5)
        self.assertEqual(r.losses, 0)
        self.assertAlmostEqual(r.winrate, 1.0)

    def test_put_en_serie_creciente_falla_siempre(self):
        s = serie_creciente()
        r = evaluar(s, siempre_put(s), expiry=5)
        self.assertEqual(r.wins, 0)
        self.assertAlmostEqual(r.winrate, 0.0)

    def test_senal_cero_no_opera(self):
        s = serie_creciente()
        r = evaluar(s, [0] * len(s), expiry=5)
        self.assertEqual(r.decisivas, 0)

    def test_no_opera_sin_espacio_para_vencer(self):
        """Ninguna operacion puede vencer fuera de los datos."""
        s = serie_creciente(50)
        r = evaluar(s, siempre_call(s), expiry=10)
        self.assertEqual(r.decisivas, 40)

    def test_empate_no_cuenta_como_acierto(self):
        velas = [Candle(ts=1_700_000_000 + i * 60, open=1.0, high=1.0, low=1.0, close=1.0)
                 for i in range(30)]
        s = Series(velas, symbol="PLANA")
        r = evaluar(s, siempre_call(s), expiry=3)
        self.assertEqual(r.wins, 0)
        self.assertEqual(r.losses, 0)
        self.assertGreater(r.ties, 0)

    def test_el_spread_siempre_empeora(self):
        s = synthetic.generate(synthetic.SyntheticParams(n=3000), seed=5)
        sen = rsi_extremo(s)
        sin = evaluar(s, sen, 10, spread_pips=0.0)
        con = evaluar(s, sen, 10, spread_pips=1.0)
        self.assertLessEqual(con.winrate, sin.winrate,
                             "el spread nunca puede mejorar el resultado")

    def test_el_spread_juega_en_contra_en_ambas_direcciones(self):
        s = serie_creciente()
        # Con la serie subiendo, un PUT ya pierde; el spread no debe 'salvarlo'.
        r0 = evaluar(s, siempre_put(s), 5, spread_pips=0.0)
        r1 = evaluar(s, siempre_put(s), 5, spread_pips=2.0)
        self.assertLessEqual(r1.winrate, r0.winrate)

    def test_umbral_y_edge_coherentes(self):
        r = Resultado("x", 100, 60, 40, 0, payout=0.80)
        self.assertAlmostEqual(r.umbral, 1 / 1.8)
        self.assertAlmostEqual(r.winrate, 0.6)
        self.assertAlmostEqual(r.edge, 0.6 - 1 / 1.8)
        self.assertGreater(r.esperanza, 0)


class TestHipotesis(unittest.TestCase):
    def test_todas_devuelven_la_longitud_correcta(self):
        s = synthetic.generate(synthetic.SyntheticParams(n=400), seed=3)
        for nombre, gen in CATALOGO.items():
            senales = gen(s)
            self.assertEqual(len(senales), len(s), f"{nombre} devolvio otra longitud")
            self.assertTrue(all(v in (-1, 0, 1) for v in senales),
                            f"{nombre} devolvio un valor fuera de (-1, 0, 1)")

    def test_reversion_es_el_inverso_del_momentum(self):
        s = synthetic.generate(synthetic.SyntheticParams(n=300), seed=4)
        self.assertEqual(reversion_1v(s), [-x for x in momentum_1v(s)])

    def test_aleatoria_es_reproducible(self):
        s = synthetic.generate(synthetic.SyntheticParams(n=200), seed=1)
        self.assertEqual(aleatoria(s), aleatoria(s))

    def test_controles_presentes(self):
        """Sin controles no se puede interpretar el barrido."""
        for ctrl in ("CTRL siempre CALL", "CTRL siempre PUT", "CTRL aleatoria"):
            self.assertIn(ctrl, CATALOGO)

    def test_rsi_extremo_solo_opera_en_extremos(self):
        """En zona neutra (30-70) no debe emitir señal, sea cual sea la serie."""
        from kronos.core import indicators as ind
        s = synthetic.generate(synthetic.SyntheticParams(n=1000), seed=6)
        rsi = ind.rsi(s.closes, 14)
        senales = rsi_extremo(s)
        for i, (v, sg) in enumerate(zip(rsi, senales)):
            if v is None:
                self.assertEqual(sg, 0, f"señal en calentamiento, indice {i}")
            elif 30 <= v <= 70:
                self.assertEqual(sg, 0, f"señal en zona neutra (RSI {v:.1f}), indice {i}")
            elif v < 30:
                self.assertEqual(sg, 1)
            else:
                self.assertEqual(sg, -1)


class TestBarrido(unittest.TestCase):
    def setUp(self):
        self.serie = synthetic.generate(synthetic.SyntheticParams(n=4000), seed=9)

    def test_produce_una_entrada_por_combinacion(self):
        h = barrer(self.serie, {"a": siempre_call, "b": siempre_put}, expiries=(1, 5))
        self.assertEqual(len(h), 4)

    def test_corrige_por_test_multiple(self):
        """Bonferroni: el p corregido debe ser p_crudo x numero de pruebas."""
        h = barrer(self.serie, {"a": siempre_call, "b": siempre_put}, expiries=(1, 3, 5))
        k = len(h)
        for x in h:
            self.assertAlmostEqual(x.p_corregido, min(1.0, x.dentro.p_valor * k), places=9)

    def test_separa_dentro_y_fuera(self):
        h = barrer(self.serie, {"a": siempre_call}, expiries=(5,), split=0.5)
        self.assertIsNotNone(h[0].fuera)
        self.assertGreater(h[0].dentro.decisivas, 0)
        self.assertGreater(h[0].fuera.decisivas, 0)

    def test_superviviente_exige_ambos_tramos(self):
        bueno = Resultado("x", 1000, 700, 300, 0, 0.80)   # 70%, muy por encima
        malo = Resultado("x", 1000, 400, 600, 0, 0.80)    # 40%, por debajo
        self.assertFalse(Hallazgo("solo dentro", bueno, malo, 0.001).superviviente)
        self.assertFalse(Hallazgo("solo fuera", malo, bueno, 1.0).superviviente)
        self.assertTrue(Hallazgo("ambos", bueno, bueno, 0.001).superviviente)

    def test_superviviente_exige_p_corregido(self):
        bueno = Resultado("x", 1000, 700, 300, 0, 0.80)
        self.assertFalse(Hallazgo("sin correccion", bueno, bueno, 0.9).superviviente)

    def test_superviviente_exige_muestra_minima(self):
        pequeno = Resultado("x", 50, 40, 10, 0, 0.80)  # 80% pero solo 50 operaciones
        self.assertFalse(Hallazgo("poca muestra", pequeno, pequeno, 0.001).superviviente)

    def test_informe_es_ascii_y_lleva_veredicto(self):
        h = barrer(self.serie, {"a": siempre_call, "b": aleatoria}, expiries=(1, 5))
        texto = informe(h)
        texto.encode("ascii")
        self.assertIn("VEREDICTO", texto)
        self.assertIn("combinaciones probadas", texto)

    def test_informe_avisa_de_falsos_positivos(self):
        h = barrer(self.serie, {"a": aleatoria}, expiries=(1,))
        self.assertIn("falsos", informe(h))

    def test_por_hora_cubre_las_sesiones(self):
        s = synthetic.generate(synthetic.SyntheticParams(n=3000), seed=12)
        filas = por_hora(s, siempre_call(s), expiry=5)
        self.assertGreater(len(filas), 0)
        self.assertTrue(all(f.decisivas > 0 for f in filas))
        self.assertLessEqual(len(filas), 24)


if __name__ == "__main__":
    unittest.main()
