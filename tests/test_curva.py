"""Tests de la curva de capital y las operaciones individuales.

El test que da sentido al modulo es `test_coincide_con_el_agregado_*`: si el
grafico ensenya un recuento distinto al del veredicto, el grafico miente, y un
grafico convincente que miente es peor que no tener grafico.
"""

from __future__ import annotations

import unittest

from kronos.backtest.stops import evaluar_con_stops
from kronos.core.candle import Candle, Series
from kronos.research.barrido import evaluar as evaluar_binarias
from kronos.research.curva import (
    Operacion,
    analizar_curva,
    curva_de_capital,
    operaciones_binarias,
    operaciones_stops,
    resumen,
)
from kronos.research.hipotesis import rsi_extremo


def serie(n: int = 800, semilla: int = 3) -> Series:
    velas = []
    precio = 100.0
    estado = semilla
    for i in range(n):
        estado = (estado * 1103515245 + 12345) % (2 ** 31)
        paso = ((estado / (2 ** 31)) - 0.5) * 2.0
        o, c = precio, precio + paso
        velas.append(Candle(ts=1_700_000_000 + i * 3600, open=o,
                            high=max(o, c) + abs(paso) * 0.5,
                            low=min(o, c) - abs(paso) * 0.5,
                            close=c, volume=100.0))
        precio = c
    return Series(velas, symbol="TEST", timeframe=3600)


class TestCoincidenciaConLosAgregados(unittest.TestCase):
    """La razon de ser de los tests de este modulo."""

    def test_coincide_con_el_agregado_binarias(self):
        s = serie()
        sen = rsi_extremo(s)
        for expiry in (1, 3, 5, 10):
            ops = operaciones_binarias(s, sen, expiry=expiry, payout=0.84,
                                       spread_pips=0.5, valor_pip=0.0001)
            agg = evaluar_binarias(s, sen, expiry, 0.84, "", 0.5, 0.0001)
            self.assertEqual(sum(1 for o in ops if o.ganada), agg.wins,
                             f"ganadas no cuadran a expiry {expiry}")
            self.assertEqual(sum(1 for o in ops if not o.ganada), agg.losses,
                             f"perdidas no cuadran a expiry {expiry}")
            self.assertEqual(len(ops), agg.decisivas)

    def test_coincide_con_el_agregado_stops(self):
        s = serie()
        sen = rsi_extremo(s)
        for rr in (1.0, 2.0, 3.0):
            ops = operaciones_stops(s, sen, rr=rr, atr_mult=1.5, max_velas=48,
                                    spread_pips=0.5, valor_pip=0.0001)
            agg = evaluar_con_stops(s, sen, rr=rr, atr_mult=1.5, max_velas=48,
                                    spread_pips=0.5, valor_pip=0.0001)
            self.assertEqual(sum(1 for o in ops if o.ganada), agg.ganadas,
                             f"ganadas no cuadran a rr {rr}")
            self.assertEqual(sum(1 for o in ops if not o.ganada), agg.perdidas,
                             f"perdidas no cuadran a rr {rr}")

    def test_las_no_resueltas_no_aparecen(self):
        """Igual que en el agregado: lo que no toca stop ni objetivo se descarta."""
        s = serie()
        sen = rsi_extremo(s)
        ops = operaciones_stops(s, sen, max_velas=2)
        agg = evaluar_con_stops(s, sen, max_velas=2)
        self.assertEqual(len(ops), agg.n)
        self.assertGreater(agg.sin_resolver, 0, "el fixture deberia dejar alguna abierta")


class TestOperaciones(unittest.TestCase):
    def test_la_salida_va_despues_de_la_entrada(self):
        s = serie()
        for ops in (operaciones_binarias(s, rsi_extremo(s), expiry=5),
                    operaciones_stops(s, rsi_extremo(s))):
            for o in ops:
                self.assertGreater(o.i_salida, o.i_entrada)
                self.assertGreater(o.velas, 0)

    def test_el_spread_empeora_la_entrada(self):
        s = serie()
        sen = rsi_extremo(s)
        sin = operaciones_binarias(s, sen, expiry=5, spread_pips=0.0)
        con = operaciones_binarias(s, sen, expiry=5, spread_pips=10.0)
        for a, b in zip(sin, con):
            if a.direccion > 0:
                self.assertGreater(b.precio_entrada, a.precio_entrada)
            else:
                self.assertLess(b.precio_entrada, a.precio_entrada)

    def test_en_stops_el_coste_resta_a_las_dos(self):
        """Tanto ganadas como perdidas pagan el spread."""
        s = serie()
        ops = operaciones_stops(s, rsi_extremo(s), rr=2.0, spread_pips=10.0)
        self.assertTrue(ops)
        for o in ops:
            if o.ganada:
                self.assertLess(o.resultado_r, 2.0)
            else:
                self.assertLess(o.resultado_r, -1.0)


class TestCurvaDeCapital(unittest.TestCase):
    def test_longitud_es_operaciones_mas_una(self):
        ops = operaciones_binarias(serie(), rsi_extremo(serie()), expiry=5)
        self.assertEqual(len(curva_de_capital(ops)), len(ops) + 1)

    def test_empieza_en_el_capital_inicial(self):
        self.assertEqual(curva_de_capital([], capital_inicial=500.0), [500.0])

    def test_el_riesgo_es_fijo_no_compuesto(self):
        """Dos ganadas iguales suman lo mismo, no cada vez mas."""
        op = Operacion(0, 1, 1, 100.0, 101.0, 2.0, True)
        c = curva_de_capital([op, op], capital_inicial=1000.0,
                             riesgo_por_operacion=0.01)
        self.assertAlmostEqual(c[1] - c[0], c[2] - c[1], places=9)

    def test_una_perdida_baja_el_capital(self):
        op = Operacion(0, 1, 1, 100.0, 99.0, -1.0, False)
        c = curva_de_capital([op], capital_inicial=1000.0, riesgo_por_operacion=0.02)
        self.assertAlmostEqual(c[-1], 980.0, places=9)

    def test_capital_invalido(self):
        with self.assertRaises(ValueError):
            curva_de_capital([], capital_inicial=0.0)

    def test_riesgo_invalido(self):
        for r in (0.0, 1.5, -0.1):
            with self.assertRaises(ValueError):
                curva_de_capital([], riesgo_por_operacion=r)


class TestAnalisisDeCurva(unittest.TestCase):
    def test_curva_creciente_no_tiene_drawdown(self):
        r = analizar_curva([100.0, 110.0, 120.0, 130.0])
        self.assertAlmostEqual(r.max_drawdown, 0.0, places=9)
        self.assertEqual(r.perdidas_seguidas, 0)

    def test_drawdown_conocido(self):
        # Sube a 200 y cae a 150: 25% desde el pico.
        r = analizar_curva([100.0, 200.0, 150.0, 180.0])
        self.assertAlmostEqual(r.max_drawdown, 0.25, places=9)
        self.assertAlmostEqual(r.pico, 200.0, places=9)
        self.assertAlmostEqual(r.valle, 150.0, places=9)

    def test_cuenta_perdidas_seguidas(self):
        r = analizar_curva([100.0, 99.0, 98.0, 97.0, 99.0, 98.0])
        self.assertEqual(r.perdidas_seguidas, 3)

    def test_curva_vacia_o_de_un_punto(self):
        self.assertEqual(analizar_curva([]).perdidas_seguidas, 0)
        self.assertEqual(analizar_curva([100.0]).max_drawdown, 0.0)


class TestResumen(unittest.TestCase):
    def test_resumen_es_ascii(self):
        s = serie()
        ops = operaciones_binarias(s, rsi_extremo(s), expiry=5)
        resumen(ops, curva_de_capital(ops)).encode("ascii")

    def test_resumen_sin_operaciones(self):
        self.assertIn("Sin operaciones", resumen([], [1000.0]))

    def test_el_resumen_incluye_el_drawdown(self):
        s = serie()
        ops = operaciones_binarias(s, rsi_extremo(s), expiry=5)
        self.assertIn("Peor caida", resumen(ops, curva_de_capital(ops)))


if __name__ == "__main__":
    unittest.main()
