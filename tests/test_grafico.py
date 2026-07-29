"""Tests de los graficos SVG.

Se generan a mano porque el Control de aplicaciones de Windows bloquea las DLL
de pandas en algunas maquinas, y Streamlit lo necesita para cualquier grafico.
Ver el docstring de `kronos/research/grafico.py`.

Un grafico se puede testear de verdad si es texto: aqui se comprueba que el SVG
esta bien formado, que dibuja tantas marcas como operaciones hay en el rango, y
que los casos degenerados (serie plana, vacia, un solo punto) no revientan.
"""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from kronos.research.curva import Operacion
from kronos.research.grafico import curva_svg, leyenda_svg, precio_svg


def op(i_entrada: int, direccion: int = 1, ganada: bool = True) -> Operacion:
    return Operacion(i_entrada=i_entrada, i_salida=i_entrada + 3,
                     direccion=direccion, precio_entrada=100.0 + i_entrada,
                     precio_salida=101.0, resultado_r=2.0 if ganada else -1.0,
                     ganada=ganada)


def parsear(svg: str) -> ET.Element:
    """Un SVG mal formado revienta aqui, que es justo lo que se quiere."""
    return ET.fromstring(svg)


class TestSVGBienFormado(unittest.TestCase):
    def test_la_curva_es_xml_valido(self):
        parsear(curva_svg([1000.0, 1010.0, 990.0, 1020.0]))

    def test_el_precio_es_xml_valido(self):
        closes = [100.0 + i * 0.1 for i in range(50)]
        parsear(precio_svg(closes, [op(10), op(20, -1, False)], 0, 50))

    def test_la_leyenda_es_xml_valida(self):
        parsear(leyenda_svg())

    def test_todo_es_ascii(self):
        closes = [100.0 + i for i in range(30)]
        curva_svg([1000.0, 1100.0]).encode("ascii")
        precio_svg(closes, [op(5)], 0, 30).encode("ascii")
        leyenda_svg().encode("ascii")


class TestMarcas(unittest.TestCase):
    def test_dibuja_una_marca_por_operacion_en_rango(self):
        closes = [100.0 + i * 0.1 for i in range(100)]
        ops = [op(10), op(20), op(30, -1, False)]
        raiz = parsear(precio_svg(closes, ops, 0, 100))
        poligonos = raiz.findall(".//{http://www.w3.org/2000/svg}polygon")
        self.assertEqual(len(poligonos), 3)

    def test_las_operaciones_fuera_de_rango_no_se_dibujan(self):
        closes = [100.0 + i * 0.1 for i in range(100)]
        ops = [op(5), op(50), op(95)]
        raiz = parsear(precio_svg(closes, ops, 40, 60))
        poligonos = raiz.findall(".//{http://www.w3.org/2000/svg}polygon")
        self.assertEqual(len(poligonos), 1, "solo la de la vela 50 esta en rango")

    def test_ganadas_y_perdidas_usan_colores_distintos(self):
        closes = [100.0] * 50
        svg = precio_svg(closes, [op(10, 1, True), op(20, 1, False)], 0, 50)
        self.assertIn("#27AE60", svg)      # verde
        self.assertIn("#E74C3C", svg)      # rojo

    def test_call_y_put_usan_formas_distintas(self):
        """Color y forma a la vez: legible sin distinguir rojo de verde."""
        closes = [100.0] * 50
        raiz = parsear(precio_svg(closes, [op(10, 1), op(20, -1)], 0, 50))
        pts = [p.get("points") for p in raiz.findall(
            ".//{http://www.w3.org/2000/svg}polygon")]
        self.assertNotEqual(pts[0], pts[1])

    def test_cada_marca_lleva_su_detalle(self):
        closes = [100.0] * 50
        raiz = parsear(precio_svg(closes, [op(10)], 0, 50))
        titulos = raiz.findall(".//{http://www.w3.org/2000/svg}title")
        self.assertEqual(len(titulos), 1)
        self.assertIn("CALL", titulos[0].text)


class TestCasosDegenerados(unittest.TestCase):
    def test_curva_vacia(self):
        parsear(curva_svg([]))

    def test_curva_de_un_punto(self):
        parsear(curva_svg([1000.0]))

    def test_curva_totalmente_plana(self):
        """Sin margen artificial, la escala dividiria por cero."""
        parsear(curva_svg([1000.0] * 20))

    def test_precio_plano(self):
        parsear(precio_svg([100.0] * 40, [op(5)], 0, 40))

    def test_rango_invertido_o_vacio(self):
        closes = [100.0 + i for i in range(30)]
        parsear(precio_svg(closes, [], 20, 20))
        parsear(precio_svg(closes, [], 25, 10))

    def test_rango_que_se_sale_de_los_datos(self):
        closes = [100.0 + i for i in range(30)]
        parsear(precio_svg(closes, [], -50, 500))

    def test_sin_operaciones(self):
        raiz = parsear(precio_svg([100.0 + i for i in range(30)], [], 0, 30))
        self.assertEqual(raiz.findall(".//{http://www.w3.org/2000/svg}polygon"), [])


class TestEscapado(unittest.TestCase):
    def test_el_mensaje_de_vacio_se_escapa(self):
        """El SVG no puede romperse por un caracter especial en un texto."""
        from kronos.research.grafico import _escapar
        self.assertEqual(_escapar('<script>&"'), "&lt;script&gt;&amp;&quot;")

    def test_un_svg_vacio_sigue_siendo_valido(self):
        parsear(curva_svg([1000.0]))


class TestContenido(unittest.TestCase):
    def test_la_curva_marca_el_capital_inicial(self):
        raiz = parsear(curva_svg([1000.0, 1100.0, 900.0]))
        lineas = raiz.findall(".//{http://www.w3.org/2000/svg}line")
        punteadas = [l for l in lineas if l.get("stroke-dasharray")]
        self.assertEqual(len(punteadas), 1, "falta la referencia del capital inicial")

    def test_la_curva_traza_todos_los_puntos(self):
        curva = [1000.0 + i for i in range(25)]
        raiz = parsear(curva_svg(curva))
        poli = raiz.find(".//{http://www.w3.org/2000/svg}polyline")
        self.assertEqual(len(poli.get("points").split()), 25)

    def test_indica_cuantas_operaciones_hay(self):
        self.assertIn("24 operaciones", curva_svg([1000.0 + i for i in range(25)]))


if __name__ == "__main__":
    unittest.main()
