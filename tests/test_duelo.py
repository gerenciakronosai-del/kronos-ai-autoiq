"""Tests del duelo IA contra reglas.

Lo que se verifica es la contabilidad, no la calidad de ningun cerebro: que un
acierto cuente como acierto, que el spread se aplique en contra a los dos por
igual, y que el veredicto no declare ganador a nadie con cuatro operaciones.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kronos.core.candle import Candle, Series
from kronos.research import duelo


def serie_creciente(n: int = 60, paso: float = 0.001) -> Series:
    velas = []
    for i in range(n):
        c = 1.0 + i * paso
        o = 1.0 + (i - 1) * paso if i else c
        velas.append(Candle(ts=1_700_000_000 + i * 60, open=o, high=max(o, c) + 1e-5,
                            low=min(o, c) - 1e-5, close=c))
    return Series(velas, symbol="SUBE", timeframe=60)


def registro(ts: int, ia: str, local: str, precio: float = 1.0, coste: float = 0.0) -> dict:
    return {"n": 1, "ts_vela": ts, "hora": "", "precio": precio,
            "ia_decision": ia, "ia_confianza": "MEDIA", "ia_razon": "",
            "ia_coste_usd": coste,
            "local_decision": local, "local_confianza": "MEDIA", "local_razon": ""}


class TestCargarRegistro(unittest.TestCase):
    def test_lee_jsonl(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.jsonl"
            p.write_text('{"a":1}\n{"a":2}\n', encoding="utf-8")
            self.assertEqual(len(duelo.cargar_registro(p)), 2)

    def test_salta_lineas_corruptas_sin_fallar(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.jsonl"
            p.write_text('{"a":1}\nesto no es json\n\n{"a":2}\n', encoding="utf-8")
            self.assertEqual(len(duelo.cargar_registro(p)), 2)

    def test_fichero_inexistente(self):
        with self.assertRaises(FileNotFoundError):
            duelo.cargar_registro("no-existe-jamas.jsonl")


class TestContabilidad(unittest.TestCase):
    def setUp(self):
        self.serie = serie_creciente()

    def test_call_acierta_en_serie_creciente(self):
        regs = [registro(1_700_000_000 + i * 60, "CALL", "CALL") for i in range(20)]
        r = duelo.evaluar(regs, self.serie, expiry_velas=5, spread_pips=0.0)
        self.assertEqual(r.ia.losses, 0)
        self.assertEqual(r.local.losses, 0)
        self.assertAlmostEqual(r.ia.winrate, 1.0)

    def test_put_falla_en_serie_creciente(self):
        regs = [registro(1_700_000_000 + i * 60, "PUT", "PUT") for i in range(20)]
        r = duelo.evaluar(regs, self.serie, expiry_velas=5, spread_pips=0.0)
        self.assertEqual(r.ia.wins, 0)

    def test_esperar_no_cuenta_como_operacion(self):
        regs = [registro(1_700_000_000 + i * 60, "ESPERAR", "ESPERAR") for i in range(20)]
        r = duelo.evaluar(regs, self.serie, expiry_velas=5)
        self.assertEqual(r.ia.decisivas, 0)
        self.assertEqual(r.ia.esperas, 20)

    def test_registros_sin_vela_correspondiente_se_ignoran(self):
        regs = [registro(999_999_999, "CALL", "CALL")]
        r = duelo.evaluar(regs, self.serie, expiry_velas=5)
        self.assertEqual(r.total_registros, 1)
        self.assertEqual(r.emparejados, 0)

    def test_no_evalua_sin_espacio_para_vencer(self):
        """Las ultimas velas no tienen futuro contra el que comparar."""
        ultimo = self.serie[-1].ts
        r = duelo.evaluar([registro(ultimo, "CALL", "CALL")], self.serie, expiry_velas=5)
        self.assertEqual(r.emparejados, 0)

    def test_el_spread_penaliza_a_los_dos_cerebros(self):
        regs = [registro(1_700_000_000 + i * 60, "CALL", "CALL") for i in range(20)]
        sin = duelo.evaluar(regs, self.serie, expiry_velas=5, spread_pips=0.0)
        con = duelo.evaluar(regs, self.serie, expiry_velas=5, spread_pips=50.0)
        self.assertLessEqual(con.ia.winrate, sin.ia.winrate)
        self.assertLessEqual(con.local.winrate, sin.local.winrate)

    def test_acumula_el_coste_de_la_ia(self):
        regs = [registro(1_700_000_000 + i * 60, "CALL", "CALL", coste=0.005)
                for i in range(10)]
        r = duelo.evaluar(regs, self.serie, expiry_velas=5)
        self.assertAlmostEqual(r.ia.coste_usd, 0.05, places=6)


class TestDiscrepancia(unittest.TestCase):
    """El corte que decide si la IA aporta: quien gana cuando difieren."""

    def setUp(self):
        self.serie = serie_creciente()

    def test_cuenta_acuerdos_y_desacuerdos(self):
        regs = ([registro(1_700_000_000 + i * 60, "CALL", "CALL") for i in range(5)]
                + [registro(1_700_000_000 + (i + 10) * 60, "CALL", "PUT") for i in range(5)])
        r = duelo.evaluar(regs, self.serie, expiry_velas=5)
        self.assertEqual(r.acuerdos, 5)
        self.assertEqual(r.desacuerdos, 5)

    def test_en_discrepancia_gana_quien_acierta(self):
        """Serie subiendo: la IA dice CALL y acierta, local dice PUT y falla."""
        regs = [registro(1_700_000_000 + i * 60, "CALL", "PUT") for i in range(20)]
        r = duelo.evaluar(regs, self.serie, expiry_velas=5, spread_pips=0.0)
        self.assertAlmostEqual(r.ia_en_discrepancia.winrate, 1.0)
        self.assertAlmostEqual(r.local_en_discrepancia.winrate, 0.0)

    def test_esperar_no_es_discrepancia(self):
        """Si uno no opera, no hay nada que comparar en ese ciclo."""
        regs = [registro(1_700_000_000 + i * 60, "CALL", "ESPERAR") for i in range(10)]
        r = duelo.evaluar(regs, self.serie, expiry_velas=5)
        self.assertEqual(r.acuerdos, 0)
        self.assertEqual(r.desacuerdos, 0)


class TestInforme(unittest.TestCase):
    def setUp(self):
        self.serie = serie_creciente(300)

    def test_es_ascii(self):
        regs = [registro(1_700_000_000 + i * 60, "CALL", "PUT") for i in range(50)]
        duelo.informe(duelo.evaluar(regs, self.serie)).encode("ascii")

    def test_muestra_pequena_no_declara_ganador(self):
        regs = [registro(1_700_000_000 + i * 60, "CALL", "PUT") for i in range(4)]
        texto = duelo.informe(duelo.evaluar(regs, self.serie))
        self.assertIn("MUESTRA INSUFICIENTE", texto)

    def test_muestra_grande_da_veredicto(self):
        regs = [registro(1_700_000_000 + i * 60, "CALL", "PUT") for i in range(200)]
        texto = duelo.informe(duelo.evaluar(regs, self.serie, spread_pips=0.0))
        self.assertNotIn("MUESTRA INSUFICIENTE", texto)
        self.assertIn("Winrate global", texto)

    def test_menciona_el_umbral_de_equilibrio(self):
        regs = [registro(1_700_000_000 + i * 60, "CALL", "CALL") for i in range(50)]
        self.assertIn("Umbral de equilibrio",
                      duelo.informe(duelo.evaluar(regs, self.serie)))


if __name__ == "__main__":
    unittest.main()
