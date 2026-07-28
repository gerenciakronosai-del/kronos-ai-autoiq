"""Tests del motor de decision."""

from __future__ import annotations

import json
import random
import unittest

from kronos.core.candle import Candle, Series
from kronos.data import synthetic
from kronos.strategy.base import Confidence, Decision, Regime, Signal, Vote
from kronos.strategy.confluence import ConfluenceParams, ConfluenceStrategy
from kronos.strategy.registry import available, build


def serie_desde_cierres(cierres, ruido: float = 0.0005, seed: int = 3,
                        symbol: str = "TEST/USD") -> Series:
    """Construye velas OHLC coherentes alrededor de una lista de cierres."""
    rng = random.Random(seed)
    velas = []
    prev = cierres[0]
    for k, c in enumerate(cierres):
        o = prev
        alto = max(o, c) * (1 + abs(rng.gauss(0, ruido)))
        bajo = min(o, c) * (1 - abs(rng.gauss(0, ruido)))
        velas.append(Candle(ts=1_700_000_000 + k * 60, open=o, high=alto, low=bajo, close=c))
        prev = c
    return Series(velas, symbol=symbol, timeframe=60)


class TestContrato(unittest.TestCase):
    def setUp(self):
        self.s = ConfluenceStrategy()

    def test_json_tiene_exactamente_las_tres_claves(self):
        serie = synthetic.generate(synthetic.SyntheticParams(n=400), seed=1)
        payload = json.loads(self.s.evaluate(serie).to_json())
        self.assertEqual(set(payload), {"decision", "confianza", "razon"})

    def test_valores_del_contrato_son_validos(self):
        serie = synthetic.generate(synthetic.SyntheticParams(n=1200), seed=5)
        for i in range(self.s.min_bars, len(serie), 17):
            p = json.loads(self.s.evaluate(serie[: i + 1]).to_json())
            self.assertIn(p["decision"], {"CALL", "PUT", "ESPERAR"})
            self.assertIn(p["confianza"], {"ALTA", "MEDIA", "BAJA"})
            self.assertIsInstance(p["razon"], str)
            self.assertTrue(p["razon"].strip(), "la razon no puede estar vacia")

    def test_razon_es_una_sola_linea(self):
        serie = synthetic.generate(synthetic.SyntheticParams(n=900), seed=9)
        for i in range(self.s.min_bars, len(serie), 23):
            self.assertNotIn("\n", self.s.evaluate(serie[: i + 1]).razon)

    def test_json_full_incluye_diagnostico(self):
        serie = synthetic.generate(synthetic.SyntheticParams(n=400), seed=2)
        p = json.loads(self.s.evaluate(serie).to_json(full=True))
        for clave in ("decision", "confianza", "razon", "regimen", "score", "votos", "contexto"):
            self.assertIn(clave, p)


class TestVetos(unittest.TestCase):
    def setUp(self):
        self.s = ConfluenceStrategy()

    def test_datos_insuficientes(self):
        serie = synthetic.generate(synthetic.SyntheticParams(n=10), seed=1)
        sig = self.s.evaluate(serie)
        self.assertIs(sig.decision, Decision.ESPERAR)
        self.assertIn("insuficientes", sig.razon.lower())

    def test_serie_vacia(self):
        sig = self.s.evaluate(Series([], symbol="VACIA"))
        self.assertIs(sig.decision, Decision.ESPERAR)

    def test_mercado_plano_no_opera(self):
        serie = serie_desde_cierres([1.1000] * 200, ruido=0.0)
        sig = self.s.evaluate(serie)
        self.assertIs(sig.decision, Decision.ESPERAR)
        self.assertIn("volatilidad", sig.razon.lower())

    def test_rango_lateral_estrecho_no_opera(self):
        cierres = [1.1000 + (0.00002 if i % 2 else -0.00002) for i in range(200)]
        sig = self.s.evaluate(serie_desde_cierres(cierres, ruido=0.0))
        self.assertIs(sig.decision, Decision.ESPERAR)

    def test_spike_de_noticia_no_opera(self):
        base = synthetic.generate(synthetic.SyntheticParams(n=200), seed=4)
        velas = list(base)
        ultima = velas[-1]
        velas[-1] = Candle(
            ts=ultima.ts, open=ultima.open, close=ultima.close,
            high=max(ultima.open, ultima.close) * 1.02,
            low=min(ultima.open, ultima.close) * 0.98,
        )
        sig = self.s.evaluate(Series(velas, symbol="SPIKE"))
        self.assertIs(sig.decision, Decision.ESPERAR)
        self.assertTrue(
            "anomala" in sig.razon.lower() or "volatilidad" in sig.razon.lower(),
            f"razon inesperada: {sig.razon}",
        )

    def test_confluencia_insuficiente_espera(self):
        """Con min_votos alto es imposible operar: siempre ESPERAR."""
        s = ConfluenceStrategy(ConfluenceParams(min_votos=9))
        serie = synthetic.generate(synthetic.SyntheticParams(n=1500), seed=6)
        for i in range(s.min_bars, len(serie), 29):
            self.assertIs(s.evaluate(serie[: i + 1]).decision, Decision.ESPERAR)


class TestComportamiento(unittest.TestCase):
    def test_determinista(self):
        serie = synthetic.generate(synthetic.SyntheticParams(n=600), seed=8)
        s = ConfluenceStrategy()
        a, b = s.evaluate(serie), s.evaluate(serie)
        self.assertEqual(a.to_contract(), b.to_contract())
        self.assertEqual(a.score, b.score)

    def test_no_depende_de_datos_futuros(self):
        """La misma ventana produce la misma senal, haya o no velas posteriores."""
        largo = synthetic.generate(synthetic.SyntheticParams(n=800), seed=12)
        s = ConfluenceStrategy()
        for corte in (300, 450, 600):
            ventana = largo[corte - 250 : corte]
            aislada = Series(list(ventana), symbol=largo.symbol, timeframe=largo.timeframe)
            self.assertEqual(
                s.evaluate(ventana).to_contract(),
                s.evaluate(aislada).to_contract(),
                f"la senal cambia segun lo que venga despues (corte {corte})",
            )

    def test_tendencia_alcista_no_genera_puts(self):
        rng = random.Random(21)
        precio = 1.1000
        cierres = []
        for _ in range(500):
            precio *= 1 + 0.00035 + rng.gauss(0, 0.0002)
            cierres.append(precio)
        serie = serie_desde_cierres(cierres, ruido=0.0002, seed=21)
        s = ConfluenceStrategy()
        puts = [
            i for i in range(s.min_bars, len(serie))
            if s.evaluate(serie[: i + 1]).decision is Decision.PUT
        ]
        self.assertEqual(puts, [], f"emitio PUT en tendencia alcista clara (indices {puts[:5]})")

    def test_tendencia_bajista_no_genera_calls(self):
        rng = random.Random(22)
        precio = 1.1000
        cierres = []
        for _ in range(500):
            precio *= 1 - 0.00035 + rng.gauss(0, 0.0002)
            cierres.append(precio)
        serie = serie_desde_cierres(cierres, ruido=0.0002, seed=22)
        s = ConfluenceStrategy()
        calls = [
            i for i in range(s.min_bars, len(serie))
            if s.evaluate(serie[: i + 1]).decision is Decision.CALL
        ]
        self.assertEqual(calls, [], f"emitio CALL en tendencia bajista clara (indices {calls[:5]})")

    def test_confianza_coherente_con_score(self):
        serie = synthetic.generate(synthetic.SyntheticParams(n=2500), seed=13)
        s = ConfluenceStrategy()
        vistos = 0
        for i in range(s.min_bars, len(serie), 7):
            sig = s.evaluate(serie[: i + 1])
            if not sig.decision.is_trade:
                continue
            vistos += 1
            self.assertGreaterEqual(sig.score, s.p.min_votos)
            if sig.confianza is Confidence.ALTA:
                self.assertGreaterEqual(sig.score, s.p.votos_alta)
        self.assertGreater(vistos, 0, "la muestra no produjo ninguna operacion")

    def test_votos_de_un_solo_lado(self):
        serie = synthetic.generate(synthetic.SyntheticParams(n=2000), seed=14)
        s = ConfluenceStrategy()
        for i in range(s.min_bars, len(serie), 11):
            sig = s.evaluate(serie[: i + 1])
            if sig.decision.is_trade:
                lados = {v.direccion for v in sig.votos}
                self.assertEqual(lados, {sig.decision}, "hay votos contrarios en una senal emitida")


class TestParametros(unittest.TestCase):
    def test_min_votos_menor_que_dos_falla(self):
        with self.assertRaises(ValueError):
            ConfluenceParams(min_votos=1)

    def test_emas_invertidas_fallan(self):
        with self.assertRaises(ValueError):
            ConfluenceParams(ema_fast=21, ema_slow=9)

    def test_umbrales_percent_b_invalidos(self):
        with self.assertRaises(ValueError):
            ConfluenceParams(bb_lower_pb=0.9, bb_upper_pb=0.1)

    def test_atr_min_mayor_que_max_falla(self):
        with self.assertRaises(ValueError):
            ConfluenceParams(atr_min_pct=0.5, atr_max_pct=0.1)

    def test_adx_incoherente_falla(self):
        with self.assertRaises(ValueError):
            ConfluenceParams(adx_range_max=40.0, adx_trend_min=20.0)

    def test_min_bars_cubre_el_calentamiento(self):
        s = ConfluenceStrategy()
        self.assertGreater(s.min_bars, s.p.macd_slow + s.p.macd_signal)
        self.assertGreater(s.min_bars, s.p.adx_period * 2)


class TestRegistro(unittest.TestCase):
    def test_confluence_registrada(self):
        self.assertIn("confluence", available())

    def test_build_con_parametros(self):
        s = build("confluence", {"rsi_period": 21})
        self.assertEqual(s.p.rsi_period, 21)

    def test_estrategia_desconocida(self):
        with self.assertRaises(KeyError):
            build("no-existe")

    def test_parametro_desconocido(self):
        with self.assertRaises(KeyError):
            build("confluence", {"parametro_inventado": 1})


class TestSignal(unittest.TestCase):
    def test_esperar_por_defecto_es_baja(self):
        sig = Signal.esperar("sin datos")
        self.assertIs(sig.decision, Decision.ESPERAR)
        self.assertIs(sig.confianza, Confidence.BAJA)

    def test_orden_de_confianza(self):
        self.assertLess(Confidence.BAJA.rank, Confidence.MEDIA.rank)
        self.assertLess(Confidence.MEDIA.rank, Confidence.ALTA.rank)

    def test_is_trade(self):
        self.assertTrue(Decision.CALL.is_trade)
        self.assertTrue(Decision.PUT.is_trade)
        self.assertFalse(Decision.ESPERAR.is_trade)

    def test_serializacion_de_votos(self):
        sig = Signal(
            decision=Decision.CALL, confianza=Confidence.ALTA, razon="prueba",
            regimen=Regime.TENDENCIA, score=3,
            votos=[Vote("RSI", Decision.CALL, "detalle")],
            contexto={"rsi": 28.123456789},
        )
        p = json.loads(sig.to_json(full=True))
        self.assertEqual(p["votos"][0]["indicador"], "RSI")
        self.assertEqual(p["contexto"]["rsi"], 28.123457)


if __name__ == "__main__":
    unittest.main()
