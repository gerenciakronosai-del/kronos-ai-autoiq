"""Tests de gestion de riesgo."""

from __future__ import annotations

import unittest

from kronos.risk.manager import RiskManager, RiskParams, Veto
from kronos.strategy.base import Confidence, Decision, Signal

DIA = 86400


def senal(decision=Decision.CALL, confianza=Confidence.ALTA) -> Signal:
    return Signal(decision=decision, confianza=confianza, razon="test", score=4)


class TestSizing(unittest.TestCase):
    def test_fraccion_fija_del_balance(self):
        rm = RiskManager(RiskParams(balance_inicial=1000.0, riesgo_por_operacion=0.02))
        rm.on_new_bar(0)
        self.assertAlmostEqual(rm.evaluate(senal()).stake, 20.0)

    def test_stake_fijo_tiene_prioridad(self):
        rm = RiskManager(RiskParams(balance_inicial=1000.0, riesgo_por_operacion=0.02,
                                    stake_fijo=5.0))
        rm.on_new_bar(0)
        self.assertAlmostEqual(rm.evaluate(senal()).stake, 5.0)

    def test_stake_minimo_se_respeta(self):
        rm = RiskManager(RiskParams(balance_inicial=120.0, riesgo_por_operacion=0.001,
                                    stake_minimo=1.0, balance_minimo=10.0))
        rm.on_new_bar(0)
        self.assertAlmostEqual(rm.evaluate(senal()).stake, 1.0)

    def test_sin_martingala_tras_perder(self):
        """El stake NUNCA crece tras una perdida: no hay progresion."""
        rm = RiskManager(RiskParams(balance_inicial=1000.0, riesgo_por_operacion=0.01,
                                    max_perdidas_seguidas=99, confianza_minima=Confidence.BAJA))
        stakes = []
        for k in range(5):
            rm.on_new_bar(k * 60)
            d = rm.evaluate(senal())
            self.assertTrue(d.permitido)
            stakes.append(d.stake)
            rm.on_open(d.stake)
            rm.on_close(-d.stake, 0.0)
        for anterior, actual in zip(stakes, stakes[1:]):
            self.assertLessEqual(actual, anterior, "el stake crecio tras una perdida")

    def test_parametros_invalidos(self):
        for kwargs in ({"riesgo_por_operacion": 0.0}, {"riesgo_por_operacion": 0.9},
                       {"stake_fijo": -1.0}, {"max_perdidas_seguidas": 0},
                       {"balance_inicial": 0.0}):
            with self.assertRaises(ValueError, msg=f"deberia fallar con {kwargs}"):
                RiskParams(**kwargs)


class TestVetos(unittest.TestCase):
    def test_esperar_no_es_operacion(self):
        rm = RiskManager()
        rm.on_new_bar(0)
        d = rm.evaluate(senal(Decision.ESPERAR))
        self.assertFalse(d.permitido)
        self.assertIs(d.veto, Veto.OK)

    def test_confianza_por_debajo_del_minimo(self):
        rm = RiskManager(RiskParams(confianza_minima=Confidence.ALTA))
        rm.on_new_bar(0)
        d = rm.evaluate(senal(confianza=Confidence.MEDIA))
        self.assertFalse(d.permitido)
        self.assertIs(d.veto, Veto.CONFIANZA_BAJA)

    def test_una_posicion_a_la_vez(self):
        rm = RiskManager(RiskParams(una_posicion_a_la_vez=True))
        rm.on_new_bar(0)
        d = rm.evaluate(senal())
        rm.on_open(d.stake)
        self.assertIs(rm.evaluate(senal()).veto, Veto.POSICION_ABIERTA)

    def test_limite_de_operaciones_diarias(self):
        rm = RiskManager(RiskParams(max_operaciones_dia=3, max_perdidas_seguidas=99,
                                    una_posicion_a_la_vez=False))
        rm.on_new_bar(0)
        for _ in range(3):
            d = rm.evaluate(senal())
            self.assertTrue(d.permitido)
            rm.on_open(d.stake)
            rm.on_close(d.stake * 0.8, d.stake * 1.8)
        self.assertIs(rm.evaluate(senal()).veto, Veto.MAX_OPERACIONES)

    def test_racha_perdedora_y_enfriamiento(self):
        rm = RiskManager(RiskParams(max_perdidas_seguidas=2, enfriamiento_velas=5,
                                    max_operaciones_dia=99))
        rm.on_new_bar(0)
        for _ in range(2):
            d = rm.evaluate(senal())
            rm.on_open(d.stake)
            rm.on_close(-d.stake, 0.0)
        self.assertEqual(rm.state.enfriamiento_restante, 5)
        self.assertIn(rm.evaluate(senal()).veto, (Veto.ENFRIAMIENTO, Veto.RACHA_PERDEDORA))

    def test_ganancia_rompe_la_racha_perdedora(self):
        rm = RiskManager(RiskParams(max_perdidas_seguidas=3, max_operaciones_dia=99))
        rm.on_new_bar(0)
        for _ in range(2):
            d = rm.evaluate(senal())
            rm.on_open(d.stake)
            rm.on_close(-d.stake, 0.0)
        self.assertEqual(rm.state.perdidas_seguidas, 2)
        d = rm.evaluate(senal())
        rm.on_open(d.stake)
        rm.on_close(d.stake * 0.8, d.stake * 1.8)
        self.assertEqual(rm.state.perdidas_seguidas, 0)

    def test_perdida_diaria_maxima(self):
        rm = RiskManager(RiskParams(balance_inicial=1000.0, stake_fijo=100.0,
                                    max_perdida_diaria=0.15, max_perdidas_seguidas=99,
                                    max_operaciones_dia=99))
        rm.on_new_bar(0)
        for _ in range(2):
            d = rm.evaluate(senal())
            self.assertTrue(d.permitido)
            rm.on_open(d.stake)
            rm.on_close(-d.stake, 0.0)
        self.assertIs(rm.evaluate(senal()).veto, Veto.PERDIDA_DIARIA)

    def test_objetivo_diario_de_ganancia(self):
        rm = RiskManager(RiskParams(balance_inicial=1000.0, stake_fijo=100.0,
                                    max_ganancia_diaria=0.05, max_operaciones_dia=99))
        rm.on_new_bar(0)
        d = rm.evaluate(senal())
        rm.on_open(d.stake)
        rm.on_close(80.0, 180.0)
        self.assertIs(rm.evaluate(senal()).veto, Veto.GANANCIA_DIARIA)

    def test_kill_switch_por_balance_minimo(self):
        rm = RiskManager(RiskParams(balance_inicial=200.0, stake_fijo=150.0,
                                    balance_minimo=100.0, max_perdidas_seguidas=99))
        rm.on_new_bar(0)
        d = rm.evaluate(senal())
        rm.on_open(d.stake)
        rm.on_close(-150.0, 0.0)
        self.assertTrue(rm.state.kill_switch)
        self.assertIs(rm.evaluate(senal()).veto, Veto.KILL_SWITCH)

    def test_kill_switch_es_permanente(self):
        rm = RiskManager(RiskParams(balance_inicial=200.0, balance_minimo=100.0))
        rm._activar_kill("prueba")
        rm.on_new_bar(DIA * 5)  # ni siquiera un dia nuevo lo levanta
        self.assertIs(rm.evaluate(senal()).veto, Veto.KILL_SWITCH)


class TestCicloDiario(unittest.TestCase):
    def test_cambio_de_dia_reinicia_contadores(self):
        rm = RiskManager(RiskParams(max_operaciones_dia=2, max_perdidas_seguidas=99))
        rm.on_new_bar(0)
        for _ in range(2):
            d = rm.evaluate(senal())
            rm.on_open(d.stake)
            rm.on_close(d.stake * 0.8, d.stake * 1.8)
        self.assertIs(rm.evaluate(senal()).veto, Veto.MAX_OPERACIONES)
        rm.on_new_bar(DIA + 60)
        self.assertEqual(rm.state.operaciones_hoy, 0)
        self.assertTrue(rm.evaluate(senal()).permitido)

    def test_enfriamiento_se_consume_por_vela(self):
        rm = RiskManager(RiskParams(enfriamiento_velas=3, max_perdidas_seguidas=1))
        rm.on_new_bar(0)
        d = rm.evaluate(senal())
        rm.on_open(d.stake)
        rm.on_close(-d.stake, 0.0)
        self.assertEqual(rm.state.enfriamiento_restante, 3)
        for k in range(1, 4):
            rm.on_new_bar(k * 60)
        self.assertEqual(rm.state.enfriamiento_restante, 0)

    def test_balance_refleja_apertura_y_cierre(self):
        rm = RiskManager(RiskParams(balance_inicial=1000.0, stake_fijo=50.0))
        rm.on_new_bar(0)
        rm.on_open(50.0)
        self.assertAlmostEqual(rm.state.balance, 950.0)
        rm.on_close(40.0, 90.0)
        self.assertAlmostEqual(rm.state.balance, 1040.0)

    def test_empate_devuelve_el_stake(self):
        rm = RiskManager(RiskParams(balance_inicial=1000.0, stake_fijo=50.0))
        rm.on_new_bar(0)
        rm.on_open(50.0)
        rm.on_close(0.0, 50.0)
        self.assertAlmostEqual(rm.state.balance, 1000.0)
        self.assertEqual(rm.state.perdidas_seguidas, 0)


if __name__ == "__main__":
    unittest.main()
