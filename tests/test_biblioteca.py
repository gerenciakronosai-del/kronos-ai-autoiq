"""Tests de la biblioteca de estrategias.

El nombre del fichero lo escribe el usuario, asi que la mitad de estos tests van
sobre eso: barras, `..`, rutas absolutas y nombres reservados de Windows no
pueden acabar escribiendo fuera del directorio de la biblioteca.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kronos.research.biblioteca import (
    BibliotecaError,
    borrar,
    cargar,
    guardar,
    listar,
)
from kronos.research.reglas import Condicion, EstrategiaDeclarativa, Regla


def estrategia(nombre: str = "mi estrategia") -> EstrategiaDeclarativa:
    return EstrategiaDeclarativa(
        nombre, (Regla((Condicion("rsi", "<", 30.0), Condicion("adx", ">", 20.0)), 1),))


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class TestIdaYVuelta(Base):
    def test_guardar_y_cargar(self):
        guardar(estrategia(), self.dir)
        e = cargar("mi estrategia", self.dir)
        self.assertEqual(e.estrategia, estrategia())

    def test_las_reglas_sobreviven(self):
        guardar(estrategia(), self.dir)
        cond = cargar("mi estrategia", self.dir).estrategia.reglas[0].condiciones
        self.assertEqual(len(cond), 2)
        self.assertEqual(cond[0].canal, "rsi")

    def test_sobrescribe_si_existe(self):
        guardar(estrategia(), self.dir)
        otra = EstrategiaDeclarativa(
            "mi estrategia", (Regla((Condicion("adx", ">", 50.0),), -1),))
        guardar(otra, self.dir)
        self.assertEqual(len(listar(self.dir)), 1)
        self.assertEqual(cargar("mi estrategia", self.dir).estrategia, otra)

    def test_guarda_el_veredicto_si_se_da(self):
        guardar(estrategia(), self.dir, veredicto={"superviviente": False, "edge": -0.1})
        e = cargar("mi estrategia", self.dir)
        self.assertIs(e.sobrevivio, False)

    def test_sin_veredicto_no_se_inventa(self):
        guardar(estrategia(), self.dir)
        self.assertIsNone(cargar("mi estrategia", self.dir).sobrevivio)


class TestNombresPeligrosos(Base):
    """El nombre viene de un campo de texto: es entrada no confiable."""

    def test_no_escapa_con_puntos(self):
        guardar(estrategia("../../fuera"), self.dir)
        escritos = list(self.dir.glob("*.json"))
        self.assertEqual(len(escritos), 1)
        self.assertFalse((self.dir.parent / "fuera.json").exists())

    def test_no_escapa_con_barras(self):
        for nombre in ("a/b", "a\\b", "/etc/passwd", "C:\\Windows\\algo"):
            guardar(estrategia(nombre), self.dir)
        for ruta in self.dir.glob("*.json"):
            self.assertEqual(ruta.parent.resolve(), self.dir.resolve())

    def test_nombre_reservado_de_windows(self):
        for nombre in ("CON", "nul", "COM1", "lpt9"):
            destino = guardar(estrategia(nombre), self.dir)
            self.assertTrue(destino.stem.startswith("_"),
                            f"{nombre} no fue neutralizado: {destino.stem}")

    def test_nombre_vacio_o_solo_simbolos(self):
        for nombre in ("", "   ", "///", "..."):
            with self.assertRaises(BibliotecaError):
                guardar(estrategia(nombre), self.dir)

    def test_nombre_larguisimo_se_recorta(self):
        destino = guardar(estrategia("x" * 500), self.dir)
        self.assertLessEqual(len(destino.stem), 64)

    def test_los_acentos_no_rompen_el_fichero(self):
        destino = guardar(estrategia("reversión rápida"), self.dir)
        self.assertTrue(destino.exists())
        destino.read_text(encoding="utf-8").encode("ascii")


class TestListado(Base):
    def test_lista_vacia_si_no_hay_directorio(self):
        self.assertEqual(listar(self.dir / "no_existe"), [])

    def test_ordena_por_fecha_descendente(self):
        import time
        guardar(estrategia("primera"), self.dir)
        time.sleep(0.01)
        guardar(estrategia("segunda"), self.dir)
        nombres = [e.estrategia.nombre for e in listar(self.dir)]
        self.assertEqual(nombres[0], "segunda")

    def test_un_fichero_corrupto_no_tumba_el_listado(self):
        guardar(estrategia("buena"), self.dir)
        (self.dir / "rota.json").write_text("{ esto no es json", encoding="utf-8")
        (self.dir / "incompleta.json").write_text('{"otra_cosa": 1}', encoding="utf-8")
        entradas = listar(self.dir)
        self.assertEqual(len(entradas), 1)
        self.assertEqual(entradas[0].estrategia.nombre, "buena")

    def test_la_entrada_trae_fecha_legible(self):
        guardar(estrategia(), self.dir)
        self.assertRegex(listar(self.dir)[0].fecha, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")


class TestBorrado(Base):
    def test_borra_lo_que_existe(self):
        guardar(estrategia(), self.dir)
        self.assertTrue(borrar("mi estrategia", self.dir))
        self.assertEqual(listar(self.dir), [])

    def test_borrar_lo_que_no_existe_devuelve_false(self):
        self.assertFalse(borrar("fantasma", self.dir))

    def test_borrar_no_escapa_del_directorio(self):
        externo = self.dir.parent / "no_tocar.json"
        externo.write_text("{}", encoding="utf-8")
        try:
            borrar("../no_tocar", self.dir)
            self.assertTrue(externo.exists(), "borro un fichero de fuera")
        finally:
            externo.unlink(missing_ok=True)


class TestErrores(Base):
    def test_cargar_lo_que_no_existe(self):
        with self.assertRaises(BibliotecaError):
            cargar("fantasma", self.dir)

    def test_json_valido_pero_sin_estrategia(self):
        (self.dir / "x.json").write_text(json.dumps({"hola": 1}), encoding="utf-8")
        with self.assertRaises(BibliotecaError):
            cargar("x", self.dir)


if __name__ == "__main__":
    unittest.main()
