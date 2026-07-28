"""Tests de la CLI y de la configuracion.

Lo importante que se verifica aqui es el CONTRATO de `decide`: stdout contiene
un unico JSON con las tres claves acordadas y nada mas, para que un script
ejecutor pueda parsearlo sin limpiar la salida.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from kronos.cli import main
from kronos.config import AppConfig, default_config
from kronos.data import loader, synthetic


def correr(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        codigo = main(argv)
    return codigo, out.getvalue(), err.getvalue()


class TestDecide(unittest.TestCase):
    def test_stdout_es_json_puro(self):
        codigo, out, _ = correr(["decide", "--sintetico", "--n", "400"])
        self.assertEqual(codigo, 0)
        payload = json.loads(out)  # falla si hay cualquier texto extra
        self.assertEqual(set(payload), {"decision", "confianza", "razon"})

    def test_avisos_van_a_stderr(self):
        _, out, err = correr(["decide", "--sintetico", "--n", "400"])
        self.assertNotIn("[kronos]", out)
        self.assertIn("[kronos]", err)

    def test_valores_validos(self):
        _, out, _ = correr(["decide", "--sintetico", "--n", "600", "--seed", "77"])
        p = json.loads(out)
        self.assertIn(p["decision"], {"CALL", "PUT", "ESPERAR"})
        self.assertIn(p["confianza"], {"ALTA", "MEDIA", "BAJA"})

    def test_full_anade_diagnostico(self):
        _, out, _ = correr(["decide", "--sintetico", "--n", "400", "--full"])
        p = json.loads(out)
        self.assertIn("votos", p)
        self.assertIn("contexto", p)

    def test_pretty_sigue_siendo_json_valido(self):
        _, out, _ = correr(["decide", "--sintetico", "--n", "400", "--pretty"])
        self.assertIn("\n", out.strip())
        json.loads(out)

    def test_desde_csv(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = loader.save_csv(
                synthetic.generate(synthetic.SyntheticParams(n=400), seed=3),
                Path(d) / "v.csv",
            )
            codigo, out, _ = correr(["decide", "--data", str(ruta)])
            self.assertEqual(codigo, 0)
            json.loads(out)

    def test_datos_insuficientes_devuelve_esperar(self):
        _, out, _ = correr(["decide", "--sintetico", "--n", "15"])
        self.assertEqual(json.loads(out)["decision"], "ESPERAR")

    def test_fichero_inexistente_da_codigo_2(self):
        codigo, _, err = correr(["decide", "--data", "no-existe-jamas.csv"])
        self.assertEqual(codigo, 2)
        self.assertIn("error", err.lower())


class TestBacktestCLI(unittest.TestCase):
    def test_informe_incluye_veredicto(self):
        codigo, out, _ = correr(["backtest", "--sintetico", "--n", "800"])
        self.assertIn(codigo, (0, 1))  # 1 = sin edge significativo, es normal
        self.assertIn("VEREDICTO", out)
        self.assertIn("EDGE", out)

    def test_salida_json(self):
        _, out, _ = correr(["backtest", "--sintetico", "--n", "800", "--json"])
        d = json.loads(out)
        for clave in ("winrate", "breakeven", "edge", "p_value", "significativo"):
            self.assertIn(clave, d)

    def test_codigo_de_salida_1_sin_edge(self):
        codigo, _, _ = correr(["backtest", "--sintetico", "--n", "500"])
        self.assertEqual(codigo, 1, "sin edge significativo el codigo debe ser 1")

    def test_payout_afecta_al_umbral(self):
        _, out, _ = correr(["backtest", "--sintetico", "--n", "800", "--json", "--payout", "0.5"])
        self.assertAlmostEqual(json.loads(out)["breakeven"], 1 / 1.5, places=4)

    def test_exportar(self):
        with tempfile.TemporaryDirectory() as d:
            destino = Path(d) / "res.json"
            correr(["backtest", "--sintetico", "--n", "800", "--exportar", str(destino)])
            datos = json.loads(destino.read_text(encoding="utf-8"))
            self.assertIn("resumen", datos)
            self.assertIn("trades", datos)


class TestImportar(unittest.TestCase):
    def test_convierte_y_reporta_cobertura(self):
        with tempfile.TemporaryDirectory() as d:
            origen = Path(d) / "DAT_ASCII_EURUSD_M1_202301.csv"
            origen.write_text(
                "".join(f"20230102 00{m:02d}00;1.070{m:02d};1.0710;1.0690;1.070{m:02d};0\n"
                        for m in range(60)),
                encoding="utf-8",
            )
            destino = Path(d) / "canon.csv"
            codigo, out, _ = correr(["importar", str(origen), "--out", str(destino)])
            self.assertEqual(codigo, 0)
            self.assertTrue(destino.exists())
            self.assertIn("Cobertura", out)
            self.assertIn("Operaciones estimadas", out)
            self.assertEqual(len(loader.load_csv(destino)), 60)

    def test_origen_inexistente_da_codigo_2(self):
        codigo, _, err = correr(["importar", "no-existe-jamas.zip"])
        self.assertEqual(codigo, 2)
        self.assertIn("error", err.lower())


class TestValidar(unittest.TestCase):
    def test_compara_los_dos_tramos(self):
        codigo, out, _ = correr(["validar", "--sintetico", "--n", "2000"])
        self.assertIn(codigo, (0, 1))
        self.assertIn("DENTRO DE MUESTRA", out)
        self.assertIn("FUERA DE MUESTRA", out)
        self.assertIn("COMPARATIVA", out)

    def test_serie_corta_no_se_puede_dividir(self):
        codigo, _, err = correr(["validar", "--sintetico", "--n", "150"])
        self.assertEqual(codigo, 2)
        self.assertIn("demasiado corta", err)


class TestOtrosComandos(unittest.TestCase):
    def test_demo(self):
        codigo, out, _ = correr(["demo", "--n", "3000"])
        self.assertEqual(codigo, 0)
        self.assertIn("VEREDICTO", out)
        self.assertIn("paseo aleatorio", out)

    def test_indicadores(self):
        codigo, out, _ = correr(["indicadores", "--sintetico", "--n", "300", "--ultimas", "5"])
        self.assertEqual(codigo, 0)
        self.assertIn("RSI", out)
        self.assertIn("ADX", out)

    def test_paper(self):
        codigo, out, _ = correr(["paper", "--sintetico", "--n", "800"])
        self.assertEqual(codigo, 0)
        self.assertIn("Umbral de equilibrio", out)

    def test_datos(self):
        with tempfile.TemporaryDirectory() as d:
            destino = Path(d) / "gen.csv"
            codigo, out, _ = correr(["datos", "--out", str(destino), "--n", "150"])
            self.assertEqual(codigo, 0)
            self.assertTrue(destino.exists())
            self.assertEqual(len(loader.load_csv(destino)), 150)

    def test_config_init_y_no_sobrescribe(self):
        with tempfile.TemporaryDirectory() as d:
            destino = Path(d) / "cfg.json"
            self.assertEqual(correr(["config-init", "--out", str(destino)])[0], 0)
            self.assertEqual(correr(["config-init", "--out", str(destino)])[0], 1)
            self.assertEqual(correr(["config-init", "--out", str(destino), "--forzar"])[0], 0)

    def test_sin_comando_falla(self):
        with self.assertRaises(SystemExit):
            correr([])

    def test_version(self):
        with self.assertRaises(SystemExit) as ctx:
            correr(["--version"])
        self.assertEqual(ctx.exception.code, 0)


class TestConfig(unittest.TestCase):
    def test_por_defecto_construye_objetos(self):
        cfg = default_config()
        self.assertEqual(cfg.strategy().name, "confluence")
        self.assertAlmostEqual(cfg.backtest_config().payout, 0.80)
        self.assertAlmostEqual(cfg.risk_params().balance_inicial, 1000.0)

    def test_confianza_minima_desde_texto(self):
        cfg = default_config()
        cfg.riesgo["confianza_minima"] = "alta"
        self.assertEqual(str(cfg.risk_params().confianza_minima), "ALTA")

    def test_ida_y_vuelta(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "c.json"
            default_config().save(ruta)
            cargada = AppConfig.load(ruta)
            self.assertEqual(cargada.estrategia, "confluence")

    def test_clave_desconocida_falla(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "c.json"
            ruta.write_text(json.dumps({"inventado": 1}), encoding="utf-8")
            with self.assertRaises(KeyError):
                AppConfig.load(ruta)

    def test_parametro_de_riesgo_desconocido_falla(self):
        cfg = default_config()
        cfg.riesgo["no_existe"] = 1
        with self.assertRaises(KeyError):
            cfg.risk_params()

    def test_parametro_de_backtest_desconocido_falla(self):
        cfg = default_config()
        cfg.backtest["no_existe"] = 1
        with self.assertRaises(KeyError):
            cfg.backtest_config()

    def test_fichero_inexistente(self):
        with self.assertRaises(FileNotFoundError):
            AppConfig.load("no-existe-jamas.json")


if __name__ == "__main__":
    unittest.main()
