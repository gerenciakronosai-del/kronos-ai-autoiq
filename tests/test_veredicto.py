"""Tests del veredicto honesto.

Lo que aqui se protege es que ningun camino lleve a un "SOBREVIVE" facil. Los
cinco filtros tienen que ser conjuntivos: fallar uno basta para descartar, y en
particular la correccion por intentos acumulados no puede saltarse, porque es lo
unico que impide que probar cuarenta variantes fabrique una ganadora.
"""

from __future__ import annotations

import unittest

from kronos.core.candle import Candle, Series
from kronos.research.reglas import Condicion, EstrategiaDeclarativa, Regla
from kronos.research.veredicto import (
    ALFA,
    MIN_OPERACIONES,
    Tramo,
    Veredicto,
    evaluar_estrategia,
)


def serie(n: int = 600, semilla: int = 11) -> Series:
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


def estrategia() -> EstrategiaDeclarativa:
    return EstrategiaDeclarativa(
        "prueba", (Regla((Condicion("rsi", "<", 45.0),), 1),))


def tramo(n=500, winrate=0.60, umbral=0.5, p=0.001) -> Tramo:
    return Tramo(n=n, winrate=winrate, umbral=umbral, esperanza=0.1,
                 p_valor=p, ic95=(winrate - 0.05, winrate + 0.05))


def veredicto_ganador(**kw) -> Veredicto:
    base = dict(nombre="x", modo="binarias", dentro=tramo(), fuera=tramo(),
                controles={"siempre CALL": tramo(winrate=0.50)}, intentos=1, velas=1000)
    base.update(kw)
    return Veredicto(**base)


class TestLosCincoFiltros(unittest.TestCase):
    """Cada filtro, por separado, debe poder tumbar el veredicto."""

    def test_el_caso_base_sobrevive(self):
        self.assertTrue(veredicto_ganador().superviviente)

    def test_muestra_insuficiente_dentro(self):
        v = veredicto_ganador(dentro=tramo(n=MIN_OPERACIONES - 1))
        self.assertFalse(v.superviviente)

    def test_muestra_insuficiente_fuera(self):
        v = veredicto_ganador(fuera=tramo(n=MIN_OPERACIONES - 1))
        self.assertFalse(v.superviviente)

    def test_edge_negativo_dentro(self):
        self.assertFalse(veredicto_ganador(dentro=tramo(winrate=0.40)).superviviente)

    def test_edge_que_no_sobrevive_fuera(self):
        self.assertFalse(veredicto_ganador(fuera=tramo(winrate=0.40)).superviviente)

    def test_p_no_significativo_fuera(self):
        self.assertFalse(veredicto_ganador(fuera=tramo(p=0.20)).superviviente)

    def test_un_control_que_gana_lo_tumba(self):
        v = veredicto_ganador(controles={"siempre CALL": tramo(winrate=0.75)})
        self.assertFalse(v.superviviente)
        self.assertIn("control", " ".join(v.motivos()).lower())


class TestCorreccionPorIntentos(unittest.TestCase):
    """El filtro que solo existe porque hay una interfaz de por medio."""

    def test_un_intento_no_penaliza(self):
        self.assertAlmostEqual(veredicto_ganador(intentos=1).p_corregido, 0.001, places=9)

    def test_cuarenta_intentos_multiplican_el_p(self):
        v = veredicto_ganador(intentos=40)
        self.assertAlmostEqual(v.p_corregido, 0.04, places=9)

    def test_probar_muchas_veces_acaba_tumbando_el_hallazgo(self):
        """Lo mismo que sobrevive al intento 1 no sobrevive al intento 100."""
        self.assertTrue(veredicto_ganador(intentos=1).superviviente)
        self.assertFalse(veredicto_ganador(intentos=100).superviviente)

    def test_el_p_corregido_nunca_pasa_de_uno(self):
        self.assertLessEqual(veredicto_ganador(intentos=100000).p_corregido, 1.0)

    def test_el_motivo_menciona_los_intentos(self):
        v = veredicto_ganador(intentos=100)
        self.assertIn("100", " ".join(v.motivos()))


class TestMotivos(unittest.TestCase):
    def test_si_sobrevive_no_hay_motivos(self):
        self.assertEqual(veredicto_ganador().motivos(), [])

    def test_si_no_sobrevive_siempre_explica_por_que(self):
        for kw in (dict(dentro=tramo(n=5)), dict(dentro=tramo(winrate=0.40)),
                   dict(fuera=tramo(winrate=0.40)), dict(intentos=100)):
            v = veredicto_ganador(**kw)
            self.assertFalse(v.superviviente)
            self.assertTrue(v.motivos(), f"sin explicacion para {kw}")


class TestEvaluacionCompleta(unittest.TestCase):
    def test_binarias_de_extremo_a_extremo(self):
        v = evaluar_estrategia(serie(), estrategia(), modo="binarias", expiry=3)
        self.assertEqual(v.modo, "binarias")
        self.assertEqual(len(v.controles), 3)
        self.assertGreater(v.dentro.n, 0)

    def test_stops_de_extremo_a_extremo(self):
        v = evaluar_estrategia(serie(), estrategia(), modo="stops", rr=2.0)
        self.assertEqual(v.modo, "stops")
        # Con coste, el umbral efectivo tiene que superar el 1/(1+rr) teorico.
        self.assertGreaterEqual(v.dentro.umbral, 1.0 / 3.0)

    def test_una_serie_aleatoria_no_produce_supervivientes(self):
        """La prueba de humo del proyecto entero: ruido no da ventaja."""
        v = evaluar_estrategia(serie(1200), estrategia(), modo="binarias", expiry=3)
        self.assertFalse(v.superviviente,
                         "un paseo aleatorio produjo una estrategia ganadora")

    def test_sin_controles_si_se_pide(self):
        v = evaluar_estrategia(serie(), estrategia(), con_controles=False)
        self.assertEqual(v.controles, {})
        self.assertTrue(v.bate_a_los_controles)

    def test_el_spread_nunca_mejora_la_esperanza(self):
        s = serie()
        sin = evaluar_estrategia(s, estrategia(), expiry=3, spread_pips=0.0)
        con = evaluar_estrategia(s, estrategia(), expiry=3, spread_pips=5.0)
        self.assertLessEqual(con.dentro.esperanza, sin.dentro.esperanza)

    def test_modo_invalido(self):
        with self.assertRaises(ValueError):
            evaluar_estrategia(serie(), estrategia(), modo="ruleta")

    def test_split_invalido(self):
        with self.assertRaises(ValueError):
            evaluar_estrategia(serie(), estrategia(), split=0.99)

    def test_intentos_invalidos(self):
        with self.assertRaises(ValueError):
            evaluar_estrategia(serie(), estrategia(), intentos=0)


class TestInforme(unittest.TestCase):
    def test_informe_es_ascii(self):
        evaluar_estrategia(serie(), estrategia(), expiry=3).informe().encode("ascii")

    def test_el_informe_nunca_ensenya_el_winrate_solo(self):
        """Invariante del proyecto: winrate siempre junto a umbral, edge y p."""
        texto = evaluar_estrategia(serie(), estrategia(), expiry=3).informe()
        for obligatorio in ("winrate", "umbral", "edge", "p"):
            self.assertIn(obligatorio, texto)

    def test_el_informe_dice_el_numero_de_intentos(self):
        v = evaluar_estrategia(serie(), estrategia(), expiry=3, intentos=17)
        self.assertIn("17", v.informe())


if __name__ == "__main__":
    unittest.main()
