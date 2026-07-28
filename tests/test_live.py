"""Tests del cerebro IA, los feeds y el motor en vivo.

No se llama a la API real en ningun test: el cerebro se sustituye por un doble
que devuelve respuestas fijas. Lo que se verifica es el CONTRATO (que el motor
trate correctamente cualquier cosa que devuelva la IA, incluidos los fallos) y
la contabilidad de coste, no la calidad de las respuestas del modelo.
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from kronos.data import loader, synthetic
from kronos.ia.cerebro import RespuestaIA
from kronos.ia.coste import (
    MULT_LECTURA_CACHE, Contador, Uso, coste_usd, estimar_coste_diario,
)
from kronos.ia.prompt import ESQUEMA_DECISION, SISTEMA, construir_snapshot
from kronos.live.feed import FeedReplay, FeedSintetico
from kronos.live.motor import ConfigMotor, MotorEnVivo
from kronos.risk.manager import RiskParams
from kronos.strategy.base import Confidence, Decision


class CerebroFalso:
    """Doble del cerebro IA: devuelve lo que se le diga, sin red."""

    def __init__(self, respuestas=None):
        self.respuestas = list(respuestas or [])
        self.llamadas = 0
        self.contador = Contador(modelo="falso")

    def analizar(self, series, **kw) -> RespuestaIA:
        self.llamadas += 1
        ts = series[-1].ts if len(series) else 0
        if self.respuestas:
            r = self.respuestas.pop(0)
        else:
            r = RespuestaIA(decision=Decision.CALL, confianza=Confidence.ALTA,
                            razon="doble de prueba")
        r.ts = ts
        self.contador.registrar(Uso(entrada=100, salida=50), 12.5)
        return r


class TestPrompt(unittest.TestCase):
    def test_esquema_cierra_el_contrato(self):
        self.assertEqual(set(ESQUEMA_DECISION["properties"]), {"decision", "confianza", "razon"})
        self.assertEqual(set(ESQUEMA_DECISION["required"]), {"decision", "confianza", "razon"})
        self.assertFalse(ESQUEMA_DECISION["additionalProperties"])

    def test_enums_coinciden_con_los_tipos_internos(self):
        self.assertEqual(set(ESQUEMA_DECISION["properties"]["decision"]["enum"]),
                         {str(d) for d in Decision})
        self.assertEqual(set(ESQUEMA_DECISION["properties"]["confianza"]["enum"]),
                         {str(c) for c in Confidence})

    def test_sistema_supera_el_minimo_cacheable(self):
        """Por debajo de ~512 tokens la API no cachea y se paga entero cada vez."""
        self.assertGreater(len(SISTEMA) / 4, 512)

    def test_sistema_es_estable(self):
        """Si el prompt variara entre llamadas, la cache no serviria de nada."""
        self.assertEqual(SISTEMA, SISTEMA)
        self.assertNotIn("{", SISTEMA.replace("{{", "").replace("}}", ""))

    def test_snapshot_con_datos_suficientes(self):
        serie = synthetic.generate(synthetic.SyntheticParams(n=200), seed=1)
        snap = construir_snapshot(serie)
        self.assertIsNotNone(snap)
        for clave in ("ACTIVO:", "PRECIO ACTUAL:", "ATR(14)", "ADX(14)", "RSI(14)", "%B"):
            self.assertIn(clave, snap)

    def test_snapshot_sin_datos_suficientes(self):
        serie = synthetic.generate(synthetic.SyntheticParams(n=20), seed=1)
        self.assertIsNone(construir_snapshot(serie))

    def test_snapshot_es_compacto(self):
        """Se envian indicadores, no velas en crudo: debe caber en pocos tokens."""
        serie = synthetic.generate(synthetic.SyntheticParams(n=300), seed=2)
        self.assertLess(len(construir_snapshot(serie)) / 4, 500)


class TestCoste(unittest.TestCase):
    def test_coste_basico(self):
        c = coste_usd(Uso(entrada=1_000_000, salida=0), "claude-opus-5")
        self.assertAlmostEqual(c, 5.00, places=6)
        c = coste_usd(Uso(entrada=0, salida=1_000_000), "claude-opus-5")
        self.assertAlmostEqual(c, 25.00, places=6)

    def test_la_cache_abarata_la_entrada(self):
        plena = coste_usd(Uso(entrada=1_000_000), "claude-opus-5")
        cacheada = coste_usd(Uso(cache_lectura=1_000_000), "claude-opus-5")
        self.assertAlmostEqual(cacheada, plena * MULT_LECTURA_CACHE, places=6)

    def test_modelo_desconocido_no_revienta(self):
        self.assertGreater(coste_usd(Uso(entrada=1000), "modelo-inventado"), 0)

    def test_contador_acumula(self):
        c = Contador(modelo="claude-opus-5")
        c.registrar(Uso(entrada=1000, salida=100), 500.0)
        c.registrar(Uso(entrada=1000, salida=100), 1500.0)
        self.assertEqual(c.llamadas, 2)
        self.assertEqual(c.uso.entrada, 2000)
        self.assertAlmostEqual(c.latencia_media, 1000.0)
        self.assertGreater(c.coste_total, 0)
        self.assertAlmostEqual(c.coste_medio, c.coste_total / 2)

    def test_tasa_de_cache(self):
        c = Contador()
        c.registrar(Uso(entrada=200, cache_lectura=800, salida=50), 10.0)
        self.assertAlmostEqual(c.tasa_cache, 0.8)

    def test_intervalo_corto_multiplica_el_coste(self):
        """El dato que justifica la compuerta de cierre de vela."""
        cada_5s = estimar_coste_diario(5)
        por_vela = estimar_coste_diario(60)
        self.assertAlmostEqual(cada_5s / por_vela, 12.0, places=3)

    def test_estimacion_degenerada(self):
        self.assertEqual(estimar_coste_diario(0), 0.0)


class TestFeeds(unittest.TestCase):
    def test_sintetico_precarga_y_avanza(self):
        f = FeedSintetico(velocidad=100000.0, precargar=120)
        self.assertTrue(f.listo)
        n = len(f.serie)
        time.sleep(0.05)
        self.assertTrue(f.avanzar())
        self.assertGreaterEqual(len(f.serie), n)
        self.assertIsNotNone(f.precio)

    def test_sintetico_no_avanza_sin_tiempo(self):
        f = FeedSintetico(velocidad=0.01)
        self.assertFalse(f.avanzar())

    def test_ventana_rodante_acotada(self):
        f = FeedSintetico(velocidad=1e9, ventana=80, precargar=80)
        for _ in range(5):
            f.avanzar()
        self.assertLessEqual(len(f.serie), 80)

    def test_replay_sobre_csv(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = loader.save_csv(
                synthetic.generate(synthetic.SyntheticParams(n=400), seed=3),
                Path(d) / "v.csv",
            )
            f = FeedReplay(ruta, velocidad=100000.0, ventana=100, desde=150)
            self.assertTrue(f.listo)
            self.assertTrue(f.avanzar())
            self.assertFalse(f.agotado)
            self.assertGreater(f.progreso, 0)
            self.assertIn("Replay", f.descripcion())

    def test_replay_limita_la_carga(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = loader.save_csv(
                synthetic.generate(synthetic.SyntheticParams(n=1000), seed=4),
                Path(d) / "v.csv",
            )
            f = FeedReplay(ruta, limite=300, desde=250)
            self.assertEqual(len(f._velas), 300)


class TestMotor(unittest.TestCase):
    def _motor(self, cerebro=None, **cfg):
        base = dict(intervalo_seg=0.5, expiry_velas=3, registro=None,
                    usar_ia=cerebro is not None, usar_local=True)
        base.update(cfg)
        return MotorEnVivo(
            feed=FeedSintetico(velocidad=5000.0, seed=11),
            config=ConfigMotor(**base),
            riesgo=RiskParams(balance_inicial=1000.0, confianza_minima=Confidence.BAJA,
                              max_operaciones_dia=500),
            cerebro=cerebro,
        )

    def test_arranca_y_para(self):
        m = self._motor(cerebro=None, cerebro_ejecutor="local")
        m.iniciar()
        self.assertTrue(m.corriendo)
        time.sleep(1.2)
        m.detener()
        self.assertFalse(m.corriendo)
        self.assertGreater(m.snapshot()["stats"].ticks, 0)

    def test_consulta_a_la_ia_y_ejecuta(self):
        cerebro = CerebroFalso()
        m = self._motor(cerebro=cerebro, cerebro_ejecutor="ia")
        m.iniciar()
        time.sleep(2.0)
        m.detener()
        s = m.snapshot()
        self.assertGreater(cerebro.llamadas, 0)
        self.assertGreater(s["stats"].consultas, 0)
        self.assertGreater(s["stats"].ordenes, 0)

    def test_un_fallo_de_la_ia_no_opera_ni_rompe(self):
        """Fallo cerrado: sin decision valida no se abre ninguna posicion."""
        fallos = [RespuestaIA.fallo("timeout simulado") for _ in range(20)]
        cerebro = CerebroFalso(fallos)
        m = self._motor(cerebro=cerebro, cerebro_ejecutor="ia")
        m.iniciar()
        time.sleep(1.6)
        m.detener()
        s = m.snapshot()
        self.assertEqual(s["stats"].ordenes, 0)
        self.assertIsNone(s["ultimo_error"])
        self.assertAlmostEqual(s["balance"], 1000.0)

    def test_registra_acuerdo_y_desacuerdo(self):
        cerebro = CerebroFalso()
        m = self._motor(cerebro=cerebro, cerebro_ejecutor="local")
        m.iniciar()
        time.sleep(2.0)
        m.detener()
        st = m.snapshot()["stats"]
        self.assertGreater(st.acuerdos + st.desacuerdos, 0)
        self.assertGreaterEqual(st.tasa_acuerdo, 0.0)
        self.assertLessEqual(st.tasa_acuerdo, 1.0)

    def test_compuerta_de_cierre_de_vela_reduce_consultas(self):
        """Es la razon de ser de la compuerta: menos llamadas, misma informacion."""
        def contar(solo_cierre: bool) -> tuple[int, int]:
            cerebro = CerebroFalso()
            m = MotorEnVivo(
                feed=FeedSintetico(velocidad=30.0, seed=5),  # ~1 vela cada 2 s
                config=ConfigMotor(intervalo_seg=0.5, solo_en_cierre_de_vela=solo_cierre,
                                   registro=None, cerebro_ejecutor="local"),
                riesgo=RiskParams(balance_inicial=1000.0),
                cerebro=cerebro,
            )
            m.iniciar()
            time.sleep(3.0)
            m.detener()
            s = m.snapshot()
            return s["stats"].ticks, cerebro.llamadas

        ticks_con, llamadas_con = contar(True)
        _, llamadas_sin = contar(False)
        self.assertGreater(llamadas_sin, llamadas_con,
                           "la compuerta deberia recortar las llamadas a la API")

    def test_snapshot_es_una_copia(self):
        m = self._motor(cerebro=None, cerebro_ejecutor="local")
        m.iniciar()
        time.sleep(0.8)
        a = m.snapshot()
        a["stats"].ordenes = 9999
        a["historial"].clear()
        b = m.snapshot()
        m.detener()
        self.assertNotEqual(b["stats"].ordenes, 9999)

    def test_snapshot_expone_el_umbral_de_equilibrio(self):
        m = self._motor(cerebro=None, cerebro_ejecutor="local", payout=0.80)
        self.assertAlmostEqual(m.snapshot()["umbral_equilibrio"], 1 / 1.8, places=6)

    def test_persiste_el_registro(self):
        with tempfile.TemporaryDirectory() as d:
            destino = Path(d) / "dec.jsonl"
            m = MotorEnVivo(
                feed=FeedSintetico(velocidad=5000.0, seed=13),
                config=ConfigMotor(intervalo_seg=0.5, registro=str(destino),
                                   cerebro_ejecutor="local", usar_ia=False),
                riesgo=RiskParams(balance_inicial=1000.0, confianza_minima=Confidence.BAJA),
            )
            m.iniciar()
            time.sleep(1.6)
            m.detener()
            self.assertTrue(destino.exists())
            lineas = destino.read_text(encoding="utf-8").strip().splitlines()
            self.assertGreater(len(lineas), 0)
            fila = json.loads(lineas[0])
            for clave in ("n", "ts_vela", "precio", "ia_decision", "local_decision"):
                self.assertIn(clave, fila)

    def test_config_invalida(self):
        for kwargs in ({"intervalo_seg": 0.1}, {"expiry_velas": 0},
                       {"payout": 0.0}, {"cerebro_ejecutor": "otro"}):
            with self.assertRaises(ValueError, msg=f"deberia fallar con {kwargs}"):
                ConfigMotor(**kwargs)


class TestRespuestaIA(unittest.TestCase):
    def test_fallo_devuelve_esperar(self):
        r = RespuestaIA.fallo("sin red")
        self.assertIs(r.decision, Decision.ESPERAR)
        self.assertIs(r.confianza, Confidence.BAJA)
        self.assertFalse(r.ok)
        self.assertIn("sin red", r.razon)

    def test_contrato_de_salida(self):
        r = RespuestaIA(decision=Decision.PUT, confianza=Confidence.ALTA, razon="x")
        self.assertEqual(set(r.to_contract()), {"decision", "confianza", "razon"})
        self.assertTrue(r.ok)


if __name__ == "__main__":
    unittest.main()


class TestFeedEstancado(unittest.TestCase):
    """Un bot desatendido cuyo feed muere sigue 'corriendo' sin hacer nada.

    Paso de verdad: los instrumentos OTC cierran cuando abre el forex real, de
    madrugada. Sin deteccion, el bot pasa la noche mudo y nadie se entera.
    """

    class FeedMuerto(FeedSintetico):
        def avanzar(self) -> bool:
            return False  # nunca llegan velas nuevas

    def _motor(self, umbral: float):
        return MotorEnVivo(
            feed=self.FeedMuerto(velocidad=1000.0, seed=3),
            config=ConfigMotor(intervalo_seg=0.5, registro=None, usar_ia=False,
                               cerebro_ejecutor="local", alerta_sin_velas_seg=umbral),
            riesgo=RiskParams(balance_inicial=1000.0, confianza_minima=Confidence.BAJA),
        )

    def test_detecta_que_no_llegan_velas(self):
        m = self._motor(umbral=1.0)
        m.iniciar()
        time.sleep(2.5)
        m.detener()
        s = m.snapshot()
        self.assertTrue(s["feed_estancado"], "no detecto el feed muerto")
        self.assertIn("FEED ESTANCADO", s["ultimo_error"])

    def test_no_alerta_antes_de_tiempo(self):
        m = self._motor(umbral=600.0)
        m.iniciar()
        time.sleep(1.5)
        m.detener()
        self.assertFalse(m.snapshot()["feed_estancado"])

    def test_un_feed_sano_no_se_marca(self):
        m = MotorEnVivo(
            feed=FeedSintetico(velocidad=5000.0, seed=4),
            config=ConfigMotor(intervalo_seg=0.5, registro=None, usar_ia=False,
                               cerebro_ejecutor="local", alerta_sin_velas_seg=1.0),
            riesgo=RiskParams(balance_inicial=1000.0, confianza_minima=Confidence.BAJA),
        )
        m.iniciar()
        time.sleep(2.5)
        m.detener()
        self.assertFalse(m.snapshot()["feed_estancado"])


class TestCombinacionesContradictorias(unittest.TestCase):
    """Dos banderas que se contradicen vetaban TODAS las ordenes en silencio.

    Paso de verdad: 30 minutos contra el broker real, 7 senales emitidas y 0
    ordenes, porque el ejecutor esperaba a un cerebro que estaba apagado.
    """

    def test_ejecutor_ia_sin_ia_falla_al_construir(self):
        with self.assertRaises(ValueError) as ctx:
            ConfigMotor(cerebro_ejecutor="ia", usar_ia=False)
        self.assertIn("vetaria todo", str(ctx.exception))

    def test_ejecutor_local_sin_local_falla(self):
        with self.assertRaises(ValueError):
            ConfigMotor(cerebro_ejecutor="local", usar_local=False)

    def test_combinaciones_validas_pasan(self):
        ConfigMotor(cerebro_ejecutor="local", usar_ia=False, usar_local=True)
        ConfigMotor(cerebro_ejecutor="ia", usar_ia=True, usar_local=False)


class TestBalanceInicialDelBroker(unittest.TestCase):
    """Con broker real el balance de partida lo manda la cuenta, no la config.

    Si no, el PnL compara la config (1.000) con la cuenta demo (10.000) e
    inventa un beneficio de +9.000 que nunca existio.
    """

    class BrokerFalso:
        tipo_cuenta = "DEMO"

        def balance(self): return 10_000.0
        def liquidar(self, symbol, precio, ts): return []
        def comprar(self, *a, **k): raise AssertionError("no deberia comprar")
        @property
        def abiertas(self): return []

    def test_toma_el_balance_de_la_cuenta(self):
        m = MotorEnVivo(
            feed=FeedSintetico(velocidad=1000.0, seed=21),
            config=ConfigMotor(intervalo_seg=0.5, registro=None, usar_ia=False,
                               cerebro_ejecutor="local"),
            riesgo=RiskParams(balance_inicial=1000.0),
            broker=self.BrokerFalso(),
        )
        s = m.snapshot()
        self.assertAlmostEqual(s["balance_inicial"], 10_000.0)
        self.assertAlmostEqual(s["pnl"], 0.0, msg="el PnL de partida debe ser cero")


class TestPnlDelBotAislado(unittest.TestCase):
    """El resultado del bot no puede medirse por el cambio del balance.

    Paso de verdad: 8 ganadas de 10 (+476) y el balance bajo 520, porque el
    usuario hizo una operacion manual de 1.000 en paralelo. Atribuirsela al bot
    invierte por completo la lectura de su rendimiento.
    """

    def test_solo_suma_las_ordenes_propias(self):
        m = MotorEnVivo(
            feed=FeedSintetico(velocidad=5000.0, seed=31),
            config=ConfigMotor(intervalo_seg=0.5, expiry_velas=1, registro=None,
                               usar_ia=False, cerebro_ejecutor="local",
                               spread_pips=0.0),
            riesgo=RiskParams(balance_inicial=1000.0, confianza_minima=Confidence.BAJA,
                              max_operaciones_dia=999, max_perdidas_seguidas=99,
                              max_perdida_diaria=0.99, max_ganancia_diaria=0.99),
        )
        m.iniciar()
        time.sleep(6)
        m.detener()
        s = m.snapshot()
        st = s["stats"]
        if st.decisivas == 0:
            self.skipTest("la estrategia no genero operaciones en esta corrida")
        # Contra el broker simulado, sin interferencias, ambos deben coincidir.
        self.assertAlmostEqual(st.pnl_bot, s["pnl"], places=2)

    def test_pnl_bot_empieza_a_cero(self):
        m = MotorEnVivo(
            feed=FeedSintetico(velocidad=1000.0, seed=32),
            config=ConfigMotor(intervalo_seg=0.5, registro=None, usar_ia=False,
                               cerebro_ejecutor="local"),
            riesgo=RiskParams(balance_inicial=1000.0),
        )
        self.assertAlmostEqual(m.snapshot()["stats"].pnl_bot, 0.0)
