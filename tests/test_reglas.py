"""Tests de las estrategias declarativas.

El test que justifica el modulo entero es `test_sin_look_ahead`: permitir que el
usuario defina reglas desde fuera no puede abrir la puerta a que una estrategia
mire al futuro. Si eso se rompe, la plataforma miente y no sirve para nada.
"""

from __future__ import annotations

import math
import unittest

from kronos.core.candle import Candle, Series
from kronos.research.reglas import (
    CANALES,
    Condicion,
    EstrategiaDeclarativa,
    Regla,
    catalogo,
    senales,
)


def serie_realista(n: int = 400, semilla: int = 7) -> Series:
    """Paseo aleatorio determinista con OHLC coherente."""
    velas = []
    precio = 100.0
    estado = semilla
    for i in range(n):
        estado = (estado * 1103515245 + 12345) % (2 ** 31)
        paso = ((estado / (2 ** 31)) - 0.5) * 2.0
        apertura = precio
        cierre = precio + paso
        alto = max(apertura, cierre) + abs(paso) * 0.5
        bajo = min(apertura, cierre) - abs(paso) * 0.5
        velas.append(Candle(ts=1_700_000_000 + i * 3600, open=apertura, high=alto,
                            low=bajo, close=cierre, volume=100.0))
        precio = cierre
    return Series(velas, symbol="TEST", timeframe=3600)


def estrategia_ejemplo() -> EstrategiaDeclarativa:
    return EstrategiaDeclarativa(
        nombre="RSI extremo con filtro de tendencia",
        reglas=(
            Regla((Condicion("rsi", "<", 30.0), Condicion("adx", "<", 25.0)), 1),
            Regla((Condicion("rsi", ">", 70.0), Condicion("adx", "<", 25.0)), -1),
        ),
    )


class TestSinLookAhead(unittest.TestCase):
    """La garantia central: la senal en i no puede depender de velas > i."""

    def test_sin_look_ahead(self):
        s = serie_realista(400)
        est = estrategia_ejemplo()
        completa = senales(s, est)

        # Recortar la serie no puede cambiar las senales que ya estaban.
        for corte in (150, 220, 300, 399):
            parcial = senales(Series(list(s)[:corte], symbol="TEST", timeframe=3600), est)
            self.assertEqual(
                parcial, completa[:corte],
                f"las senales cambiaron al recortar la serie en {corte}: hay look-ahead",
            )

    def test_todos_los_canales_estan_libres_de_look_ahead(self):
        s = serie_realista(300)
        corte = 200
        recortada = Series(list(s)[:corte], symbol="TEST", timeframe=3600)
        for nombre, canal in CANALES.items():
            completo = canal.calcular(s, canal.periodo)[:corte]
            parcial = canal.calcular(recortada, canal.periodo)
            for i, (a, b) in enumerate(zip(completo, parcial)):
                if a is None or b is None:
                    self.assertIs(a, b, f"{nombre}[{i}]: calentamiento distinto")
                else:
                    self.assertAlmostEqual(
                        a, b, places=9,
                        msg=f"{nombre}[{i}] cambia al anyadir velas posteriores",
                    )

    def test_el_cruce_solo_mira_una_vela_atras(self):
        """En i=0 no hay vela anterior, asi que un cruce nunca puede dispararse."""
        s = serie_realista(100)
        est = EstrategiaDeclarativa(
            "cruce", (Regla((Condicion("rsi", "cruza_arriba", 50.0),), 1),))
        self.assertEqual(senales(s, est)[0], 0)


class TestLongitudYCalentamiento(unittest.TestCase):
    def test_misma_longitud_que_la_entrada(self):
        for n in (0, 1, 5, 120):
            s = serie_realista(n) if n else Series([], symbol="T", timeframe=3600)
            self.assertEqual(len(senales(s, estrategia_ejemplo())), n)

    def test_en_calentamiento_no_se_opera(self):
        s = serie_realista(200)
        est = EstrategiaDeclarativa(
            "adx", (Regla((Condicion("adx", ">", 0.0),), 1),))
        # El ADX necesita bastante calentamiento; las primeras velas deben ser 0.
        self.assertEqual(senales(s, est)[0], 0)

    def test_valor_none_nunca_cumple(self):
        """Un canal en calentamiento no satisface ninguna comparacion."""
        s = serie_realista(200)
        for op, ref in (("<", 1e9), (">", -1e9), ("<=", 1e9), (">=", -1e9)):
            est = EstrategiaDeclarativa(
                "x", (Regla((Condicion("adx", op, ref),), 1),))
            self.assertEqual(
                senales(s, est)[0], 0,
                f"con operador {op} una vela en calentamiento acabo operando",
            )


class TestConflictos(unittest.TestCase):
    def test_reglas_opuestas_no_operan(self):
        s = serie_realista(200)
        # Dos reglas que se cumplen siempre, en sentidos opuestos.
        est = EstrategiaDeclarativa("choque", (
            Regla((Condicion("rsi", ">", -1.0),), 1),
            Regla((Condicion("rsi", ">", -1.0),), -1),
        ))
        self.assertEqual(set(senales(s, est)), {0})

    def test_reglas_del_mismo_signo_no_se_anulan(self):
        s = serie_realista(200)
        est = EstrategiaDeclarativa("acuerdo", (
            Regla((Condicion("rsi", ">", -1.0),), 1),
            Regla((Condicion("rsi", "<", 1e9),), 1),
        ))
        emitidas = [x for x in senales(s, est) if x != 0]
        self.assertTrue(emitidas)
        self.assertEqual(set(emitidas), {1})


class TestCondicionesCombinadas(unittest.TestCase):
    def test_las_condiciones_se_suman_con_Y(self):
        s = serie_realista(300)
        laxa = EstrategiaDeclarativa(
            "laxa", (Regla((Condicion("rsi", "<", 45.0),), 1),))
        estricta = EstrategiaDeclarativa("estricta", (
            Regla((Condicion("rsi", "<", 45.0), Condicion("adx", ">", 20.0)), 1),))
        n_laxa = sum(1 for x in senales(s, laxa) if x)
        n_estricta = sum(1 for x in senales(s, estricta) if x)
        self.assertLessEqual(n_estricta, n_laxa,
                             "anyadir una condicion no puede generar mas senales")


class TestValidacion(unittest.TestCase):
    def test_canal_desconocido(self):
        with self.assertRaises(ValueError):
            Condicion("no_existe", "<", 1.0)

    def test_operador_desconocido(self):
        with self.assertRaises(ValueError):
            Condicion("rsi", "aproximadamente", 1.0)

    def test_periodo_invalido(self):
        with self.assertRaises(ValueError):
            Condicion("rsi", "<", 30.0, periodo=0)

    def test_direccion_invalida(self):
        with self.assertRaises(ValueError):
            Regla((Condicion("rsi", "<", 30.0),), 0)

    def test_regla_sin_condiciones(self):
        with self.assertRaises(ValueError):
            Regla((), 1)

    def test_estrategia_sin_reglas(self):
        with self.assertRaises(ValueError):
            EstrategiaDeclarativa("vacia", ())


class TestSerializacion(unittest.TestCase):
    def test_ida_y_vuelta_por_json(self):
        original = estrategia_ejemplo()
        copia = EstrategiaDeclarativa.desde_json(original.a_json())
        self.assertEqual(copia, original)

    def test_las_senales_sobreviven_al_viaje(self):
        s = serie_realista(250)
        original = estrategia_ejemplo()
        copia = EstrategiaDeclarativa.desde_json(original.a_json())
        self.assertEqual(senales(s, copia), senales(s, original))

    def test_periodo_personalizado_se_conserva(self):
        est = EstrategiaDeclarativa(
            "p", (Regla((Condicion("rsi", "<", 30.0, periodo=7),), 1),))
        copia = EstrategiaDeclarativa.desde_json(est.a_json())
        self.assertEqual(copia.reglas[0].condiciones[0].periodo, 7)

    def test_json_invalido(self):
        with self.assertRaises(ValueError):
            EstrategiaDeclarativa.desde_json("{no es json")

    def test_estructura_invalida(self):
        with self.assertRaises(ValueError):
            EstrategiaDeclarativa.desde_json('{"nombre": "x"}')

    def test_json_es_ascii(self):
        estrategia_ejemplo().a_json().encode("ascii")


class TestSalidaLegible(unittest.TestCase):
    def test_describir_es_ascii(self):
        estrategia_ejemplo().describir().encode("ascii")

    def test_catalogo_es_ascii(self):
        catalogo().encode("ascii")

    def test_describir_menciona_la_direccion(self):
        d = estrategia_ejemplo().describir()
        self.assertIn("CALL", d)
        self.assertIn("PUT", d)


class TestIntegracionConElBarrido(unittest.TestCase):
    """Las senales declarativas tienen que servir al backtest tal cual."""

    def test_el_barrido_acepta_las_senales(self):
        from kronos.research.barrido import evaluar

        s = serie_realista(400)
        est = EstrategiaDeclarativa(
            "prueba", (Regla((Condicion("rsi", "<", 40.0),), 1),))
        r = evaluar(s, senales(s, est), expiry=3, payout=0.84,
                    nombre=est.nombre, spread_pips=0.5)
        self.assertGreaterEqual(r.n, 0)
        self.assertTrue(math.isfinite(r.winrate))


if __name__ == "__main__":
    unittest.main()
