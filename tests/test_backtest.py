"""Tests del motor de backtest y de las metricas.

Incluye el test central de honestidad del simulador: con una estrategia que
siempre dice CALL sobre una serie estrictamente creciente el winrate debe ser
exactamente 100%, y el balance debe crecer justo `stake * payout` por operacion.
Si la contabilidad estuviera mal, ese numero no cuadraria.
"""

from __future__ import annotations

import math
import unittest

from kronos.backtest.engine import Backtester, BacktestConfig
from kronos.backtest.metrics import (
    Resultado, binomial_p_value, breakeven_winrate, expectancy, max_drawdown, wilson_interval,
)
from kronos.backtest.report import render, veredicto
from kronos.core.candle import Candle, Series
from kronos.data import synthetic
from kronos.risk.manager import RiskParams
from kronos.strategy.base import Confidence, Decision, Signal, Strategy
from kronos.strategy.confluence import ConfluenceStrategy


class EstrategiaFija(Strategy):
    """Estrategia de laboratorio: siempre la misma decision."""

    name = "fija"

    def __init__(self, decision: Decision, confianza: Confidence = Confidence.ALTA):
        self.decision = decision
        self.confianza = confianza

    @property
    def min_bars(self) -> int:
        return 2

    def evaluate(self, series: Series) -> Signal:
        return Signal(decision=self.decision, confianza=self.confianza,
                      razon="fija", symbol=series.symbol, ts=series[-1].ts, score=4)


def serie_lineal(n: int, paso: float = 0.001, inicio: float = 1.0) -> Series:
    velas = []
    for i in range(n):
        c = inicio + i * paso
        o = inicio + (i - 1) * paso if i else c
        velas.append(Candle(ts=1_700_000_000 + i * 60, open=o, high=max(o, c) + 1e-4,
                            low=min(o, c) - 1e-4, close=c))
    return Series(velas, symbol="LINEAL", timeframe=60)


class TestMatematicas(unittest.TestCase):
    def test_breakeven_conocido(self):
        self.assertAlmostEqual(breakeven_winrate(0.80), 1 / 1.8)
        self.assertAlmostEqual(breakeven_winrate(1.00), 0.5)
        self.assertAlmostEqual(breakeven_winrate(0.70), 1 / 1.7)

    def test_breakeven_payout_invalido(self):
        with self.assertRaises(ValueError):
            breakeven_winrate(0.0)

    def test_esperanza_nula_en_el_umbral(self):
        for payout in (0.6, 0.75, 0.8, 0.92):
            self.assertAlmostEqual(expectancy(breakeven_winrate(payout), payout), 0.0, places=12)

    def test_esperanza_negativa_por_debajo_del_umbral(self):
        self.assertLess(expectancy(0.53, 0.80), 0.0)

    def test_p_valor_moneda_justa(self):
        self.assertAlmostEqual(binomial_p_value(50, 100, 0.5), 0.5, delta=0.05)

    def test_p_valor_extremos(self):
        self.assertLess(binomial_p_value(90, 100, 0.5), 1e-10)
        self.assertAlmostEqual(binomial_p_value(0, 100, 0.5), 1.0)
        self.assertEqual(binomial_p_value(10, 0, 0.5), 1.0)

    def test_p_valor_normal_para_n_grande(self):
        p = binomial_p_value(3000, 6000, 0.5)
        self.assertGreater(p, 0.4)
        self.assertLess(p, 0.6)

    def test_wilson_contiene_la_proporcion(self):
        lo, hi = wilson_interval(60, 100)
        self.assertLess(lo, 0.6)
        self.assertGreater(hi, 0.6)
        self.assertEqual(wilson_interval(0, 0), (0.0, 0.0))

    def test_drawdown(self):
        dd_abs, dd_rel = max_drawdown([100, 120, 90, 130])
        self.assertAlmostEqual(dd_abs, 30.0)
        self.assertAlmostEqual(dd_rel, 0.25)
        self.assertEqual(max_drawdown([]), (0.0, 0.0))

    def test_drawdown_curva_creciente(self):
        self.assertEqual(max_drawdown([10, 20, 30]), (0.0, 0.0))


class TestContabilidad(unittest.TestCase):
    def test_call_en_serie_creciente_gana_siempre(self):
        bt = Backtester(
            EstrategiaFija(Decision.CALL),
            BacktestConfig(payout=0.80, expiry_velas=1, ventana=50),
            RiskParams(balance_inicial=1000.0, stake_fijo=10.0, max_operaciones_dia=9999,
                       max_ganancia_diaria=10.0, max_perdidas_seguidas=99),
        )
        r = bt.run(serie_lineal(60))
        self.assertGreater(r.n, 0)
        self.assertEqual(r.losses, 0)
        self.assertEqual(r.wins, r.n)
        self.assertAlmostEqual(r.winrate, 1.0)
        self.assertAlmostEqual(r.pnl, r.n * 10.0 * 0.80, places=6)

    def test_put_en_serie_creciente_pierde_siempre(self):
        bt = Backtester(
            EstrategiaFija(Decision.PUT),
            BacktestConfig(payout=0.80, expiry_velas=1, ventana=50),
            RiskParams(balance_inicial=1000.0, stake_fijo=10.0, max_operaciones_dia=9999,
                       max_perdida_diaria=10.0, max_perdidas_seguidas=999,
                       enfriamiento_velas=0, balance_minimo=1.0),
        )
        r = bt.run(serie_lineal(60))
        self.assertGreater(r.n, 0)
        self.assertEqual(r.wins, 0)
        self.assertAlmostEqual(r.pnl, -r.n * 10.0, places=6)

    def test_empate_devuelve_el_stake(self):
        velas = [Candle(ts=1_700_000_000 + i * 60, open=1.0, high=1.0, low=1.0, close=1.0)
                 for i in range(40)]
        bt = Backtester(
            EstrategiaFija(Decision.CALL),
            BacktestConfig(payout=0.80, expiry_velas=1, ventana=30),
            RiskParams(balance_inicial=1000.0, stake_fijo=10.0, max_operaciones_dia=9999),
        )
        r = bt.run(Series(velas, symbol="PLANA"))
        self.assertEqual(r.losses, 0)
        self.assertEqual(r.wins, 0)
        self.assertGreater(r.ties, 0)
        self.assertAlmostEqual(r.balance_final, 1000.0, places=6)

    def test_esperar_no_abre_operaciones(self):
        bt = Backtester(EstrategiaFija(Decision.ESPERAR), BacktestConfig(ventana=30))
        r = bt.run(serie_lineal(80))
        self.assertEqual(r.n, 0)
        self.assertEqual(r.senales_emitidas, 0)
        self.assertAlmostEqual(r.balance_final, r.balance_inicial)

    def test_curva_de_capital_coherente(self):
        bt = Backtester(
            EstrategiaFija(Decision.CALL),
            BacktestConfig(payout=0.80, expiry_velas=1, ventana=50),
            RiskParams(balance_inicial=1000.0, stake_fijo=10.0, max_operaciones_dia=9999,
                       max_ganancia_diaria=10.0),
        )
        r = bt.run(serie_lineal(60))
        self.assertAlmostEqual(r.equity[-1], r.balance_final, places=6)
        for t in r.trades:
            self.assertGreater(t.balance_despues, 0)


class TestHonestidadDelSimulador(unittest.TestCase):
    def test_ninguna_operacion_vence_fuera_de_los_datos(self):
        serie = serie_lineal(80)
        ultimo_ts = serie[-1].ts
        bt = Backtester(
            EstrategiaFija(Decision.CALL),
            BacktestConfig(payout=0.80, expiry_velas=5, ventana=40),
            RiskParams(balance_inicial=1000.0, stake_fijo=10.0, max_operaciones_dia=9999,
                       max_ganancia_diaria=10.0),
        )
        r = bt.run(serie)
        for t in r.trades:
            self.assertLessEqual(t.ts_salida, ultimo_ts)
            self.assertEqual(t.ts_salida - t.ts_entrada, 5 * 60)

    def test_la_estrategia_solo_ve_el_pasado(self):
        """Registra el ts de la ultima vela vista y lo compara con el de entrada."""
        vistos: list[tuple[int, int]] = []

        class Espia(EstrategiaFija):
            def evaluate(self, series):
                sig = super().evaluate(series)
                vistos.append((series[-1].ts, len(series)))
                return sig

        serie = serie_lineal(70)
        bt = Backtester(
            Espia(Decision.CALL),
            BacktestConfig(payout=0.8, expiry_velas=3, ventana=30),
            RiskParams(balance_inicial=1000.0, stake_fijo=10.0, max_operaciones_dia=9999,
                       max_ganancia_diaria=10.0),
        )
        r = bt.run(serie)
        ts_vistos = {ts for ts, _ in vistos}
        for t in r.trades:
            self.assertIn(t.ts_entrada, ts_vistos)
            self.assertGreater(t.ts_salida, t.ts_entrada)
        for _, largo in vistos:
            self.assertLessEqual(largo, 30)

    def test_slippage_empeora_el_resultado(self):
        serie = synthetic.generate(synthetic.SyntheticParams(n=800), seed=31)
        riesgo = RiskParams(balance_inicial=1000.0, stake_fijo=10.0, max_operaciones_dia=9999,
                            max_perdida_diaria=10.0, max_ganancia_diaria=10.0,
                            max_perdidas_seguidas=999, balance_minimo=1.0)
        sin = Backtester(EstrategiaFija(Decision.CALL),
                         BacktestConfig(expiry_velas=1, ventana=50, slippage_pct=0.0),
                         riesgo).run(serie)
        con = Backtester(EstrategiaFija(Decision.CALL),
                         BacktestConfig(expiry_velas=1, ventana=50, slippage_pct=0.0005),
                         riesgo).run(serie)
        self.assertLessEqual(con.wins, sin.wins)

    def test_determinista(self):
        serie = synthetic.generate(synthetic.SyntheticParams(n=900), seed=17)
        def correr():
            return Backtester(ConfluenceStrategy(), BacktestConfig(ventana=300)).run(serie)
        a, b = correr(), correr()
        self.assertEqual(a.to_dict(), b.to_dict())

    def test_serie_demasiado_corta(self):
        r = Backtester(ConfluenceStrategy()).run(synthetic.generate(
            synthetic.SyntheticParams(n=20), seed=1))
        self.assertEqual(r.n, 0)
        self.assertAlmostEqual(r.balance_final, r.balance_inicial)

    def test_config_invalida(self):
        for kwargs in ({"payout": 0.0}, {"payout": 9.0}, {"expiry_velas": 0},
                       {"slippage_pct": -0.1}):
            with self.assertRaises(ValueError, msg=f"deberia fallar con {kwargs}"):
                BacktestConfig(**kwargs)


class TestVeredicto(unittest.TestCase):
    def _resultado(self, wins: int, losses: int, payout: float = 0.80):
        from kronos.backtest.metrics import BacktestResult, Trade
        r = BacktestResult(symbol="X", estrategia="fija", payout=payout,
                           balance_inicial=1000.0, balance_final=1000.0)
        for k in range(wins + losses):
            gana = k < wins
            r.trades.append(Trade(
                ts_entrada=k, ts_salida=k + 60, symbol="X", decision="CALL",
                confianza="ALTA", regimen="TENDENCIA", precio_entrada=1.0,
                precio_salida=1.1 if gana else 0.9, stake=10.0, payout=payout,
                resultado=Resultado.WIN if gana else Resultado.LOSS,
                pnl=8.0 if gana else -10.0, balance_despues=1000.0,
            ))
        r.velas_evaluadas = max(1, wins + losses)
        r.equity = [1000.0]
        return r

    def test_muestra_pequena(self):
        self.assertIn("INSUFICIENTE", veredicto(self._resultado(8, 2))[0])

    def test_esperanza_negativa(self):
        r = self._resultado(53, 47)  # 53% con payout 80% pierde dinero
        self.assertLess(r.edge, 0)
        self.assertIn("NO DESPLEGAR", veredicto(r)[0])

    def test_edge_positivo_pero_no_concluyente(self):
        r = self._resultado(29, 21)  # 58%, por encima del umbral pero pocos datos
        self.assertGreater(r.edge, 0)
        self.assertIn("NO CONCLUYENTE", veredicto(r)[0])
        self.assertIsNotNone(r.trades_minimos_necesarios())

    def test_edge_pequeno_no_es_significativo_aunque_haya_muchos_trades(self):
        """56.25% con 800 operaciones parece bueno y NO lo es.

        El edge sobre el umbral es de solo 0.69 puntos: dentro del ruido
        (p ~ 0.36). Este caso es exactamente el que hace perder dinero a quien
        mira unicamente el winrate.
        """
        r = self._resultado(450, 350)
        self.assertGreater(r.edge, 0)
        self.assertFalse(r.significativo)
        self.assertIn("NO CONCLUYENTE", veredicto(r)[0])

    def test_edge_significativo(self):
        r = self._resultado(600, 400)  # 60% con 1000 operaciones
        self.assertTrue(r.significativo, f"p={r.p_value}")
        self.assertLess(r.p_value, 0.05)
        self.assertIn("SIGNIFICATIVO", veredicto(r)[0])

    def test_trades_minimos_none_si_no_hay_edge(self):
        self.assertIsNone(self._resultado(50, 50).trades_minimos_necesarios())

    def test_informe_es_ascii(self):
        r = self._resultado(60, 40)
        texto = render(r, max_trades=5)
        texto.encode("ascii")  # falla si se cuela un caracter no ASCII
        self.assertIn("VEREDICTO", texto)
        self.assertIn("EDGE", texto)

    def test_desglose_por_confianza(self):
        r = self._resultado(30, 20)
        grupos = r.desglose("confianza")
        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0].n, 50)
        self.assertAlmostEqual(grupos[0].winrate, 0.6)

    def test_profit_factor(self):
        r = self._resultado(50, 50)
        self.assertAlmostEqual(r.profit_factor, (50 * 8.0) / (50 * 10.0))
        self.assertTrue(math.isinf(self._resultado(10, 0).profit_factor))

    def test_to_dict_serializable(self):
        import json
        json.dumps(self._resultado(40, 30).to_dict())


if __name__ == "__main__":
    unittest.main()
