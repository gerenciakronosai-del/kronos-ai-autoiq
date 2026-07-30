"""Tests de la PWA.

El motor corre en el navegador sobre Pyodide, y el codigo Python que lo pilota
vive incrustado en una plantilla de JavaScript dentro de `pwa/app.js`. Esa
convivencia tiene un modo de fallo desagradable que ya ocurrio una vez:

    un acento grave dentro del bloque Python cierra la plantilla, app.js deja de
    parsearse ENTERO, y la consola del navegador no ensenya ningun error.

La app se queda en blanco sin ninguna pista. `test_el_bloque_python_no_rompe_la_plantilla`
existe por eso.

Estos tests no necesitan navegador ni red: leen los ficheros y los validan como
texto.
"""

from __future__ import annotations

import ast
import json
import re
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PWA = RAIZ / "pwa"

# `kronos.zip` y `demo.csv` son artefactos: los genera `construir_pwa.py` y no se
# versionan. En un clon recien hecho no existen todavia, asi que los tests que
# dependen de ellos se saltan en vez de fallar. Quien clone el repositorio no
# deberia ver un test en rojo por no haber ejecutado un comando que aun no sabe
# que existe.
CONSTRUIDA = (PWA / "kronos.zip").exists() and (PWA / "demo.csv").exists()
NO_CONSTRUIDA = "la PWA no esta construida: ejecuta 'python construir_pwa.py'"

APERTURA = "await py.runPythonAsync(`"
CIERRE = "`);"


def leer(nombre: str) -> str:
    return (PWA / nombre).read_text(encoding="utf-8")


def bloque_python() -> str:
    """Extrae el Python incrustado en la plantilla de app.js."""
    js = leer("app.js")
    ini = js.index(APERTURA) + len(APERTURA)
    return js[ini:js.index(CIERRE, ini)]


@unittest.skipUnless(PWA.is_dir(), "la carpeta pwa/ no esta construida")
class TestPlantillaJavaScript(unittest.TestCase):
    """El fallo que dejo la app en blanco sin error en consola."""

    def test_el_bloque_python_no_rompe_la_plantilla(self):
        b = bloque_python()
        self.assertNotIn("`", b,
                         "un acento grave cierra la plantilla y app.js deja de parsearse")
        self.assertNotIn("${", b,
                         "'${' interpola en la plantilla y corrompe el Python")

    def test_el_bloque_python_es_python_valido(self):
        ast.parse(bloque_python())

    def test_el_bloque_expone_las_funciones_que_usa_el_puente(self):
        """Si app.js llama a algo que Python no define, la app falla en runtime."""
        arbol = ast.parse(bloque_python())
        definidas = {n.name for n in ast.walk(arbol)
                     if isinstance(n, ast.FunctionDef)}
        js = leer("app.js")
        llamadas = set(re.findall(r'py\.globals\.get\("(\w+)"\)', js))
        self.assertTrue(llamadas, "el test no encontro ninguna llamada al puente")
        self.assertLessEqual(llamadas, definidas,
                             f"app.js llama a funciones que Python no define: "
                             f"{sorted(llamadas - definidas)}")


@unittest.skipUnless(PWA.is_dir(), "la carpeta pwa/ no esta construida")
class TestManifiesto(unittest.TestCase):
    def test_es_json_valido(self):
        json.loads(leer("manifest.json"))

    def test_tiene_lo_que_exige_la_instalacion(self):
        m = json.loads(leer("manifest.json"))
        for campo in ("name", "short_name", "start_url", "display", "icons"):
            self.assertIn(campo, m, f"sin '{campo}' el navegador no ofrece instalar")
        self.assertIn(m["display"], ("standalone", "fullscreen", "minimal-ui"))
        self.assertTrue(m["icons"])

    def test_hay_icono_enmascarable(self):
        """Sin 'maskable', Android recorta el icono con un marco blanco feo."""
        m = json.loads(leer("manifest.json"))
        propositos = {p for i in m["icons"] for p in i.get("purpose", "any").split()}
        self.assertIn("maskable", propositos)

    def test_los_iconos_existen_y_son_svg_valido(self):
        m = json.loads(leer("manifest.json"))
        for icono in m["icons"]:
            ruta = PWA / icono["src"]
            self.assertTrue(ruta.exists(), f"falta {icono['src']}")
            ET.fromstring(ruta.read_text(encoding="utf-8"))


@unittest.skipUnless(PWA.is_dir(), "la carpeta pwa/ no esta construida")
class TestServiceWorker(unittest.TestCase):
    def test_precachea_todo_lo_necesario_para_funcionar_sin_conexion(self):
        sw = leer("sw.js")
        for fichero in ("index.html", "estilo.css", "app.js", "kronos.zip", "demo.csv"):
            self.assertIn(fichero, sw,
                          f"{fichero} no esta en la precarga: sin conexion faltaria")

    @unittest.skipUnless(CONSTRUIDA, NO_CONSTRUIDA)
    def test_los_ficheros_precacheados_existen(self):
        sw = leer("sw.js")
        for ruta in re.findall(r'"\./([\w.-]+)"', sw):
            self.assertTrue((PWA / ruta).exists(), f"sw.js precachea {ruta}, que no existe")

    def test_el_runtime_de_python_se_cachea_aparte(self):
        """Diez megas de CDN no se bajan dos veces."""
        self.assertIn("cdn.jsdelivr.net", leer("sw.js"))


@unittest.skipUnless(CONSTRUIDA, NO_CONSTRUIDA)
class TestPaquete(unittest.TestCase):
    def setUp(self):
        self.nombres = zipfile.ZipFile(PWA / "kronos.zip").namelist()

    def test_incluye_lo_que_la_app_importa(self):
        for modulo in ("kronos/research/reglas.py", "kronos/research/veredicto.py",
                       "kronos/research/curva.py", "kronos/research/grafico.py",
                       "kronos/data/loader.py", "kronos/core/indicators.py"):
            self.assertIn(modulo, self.nombres)

    def test_excluye_lo_que_no_corre_en_webassembly(self):
        """Red e hilos: si alguien los importa, la app revienta en el navegador."""
        prohibidos = [n for n in self.nombres
                      if any(p in n for p in ("/live/", "/broker/", "/ia/"))]
        self.assertEqual(prohibidos, [])

    def test_excluye_la_cli(self):
        """Importa broker y live, asi que fallaria al cargarse."""
        self.assertNotIn("kronos/cli.py", self.nombres)
        self.assertNotIn("kronos/__main__.py", self.nombres)

    def test_el_paquete_sigue_siendo_pequenyo(self):
        """Se descarga en el movil del usuario; que no crezca sin que nos enteremos."""
        peso = (PWA / "kronos.zip").stat().st_size
        self.assertLess(peso, 250 * 1024, f"kronos.zip pesa {peso / 1024:.0f} KB")


@unittest.skipUnless(PWA.is_dir(), "la carpeta pwa/ no esta construida")
class TestHonestidadDeLaInterfaz(unittest.TestCase):
    """Los invariantes del proyecto tienen que sobrevivir al cambio de interfaz."""

    def test_el_winrate_nunca_aparece_solo(self):
        js = leer("app.js")
        for obligatorio in ("Winrate", "Umbral", "p corregido", "Operaciones"):
            self.assertIn(obligatorio, js,
                          f"la interfaz no ensenya '{obligatorio}' junto al winrate")

    def test_avisa_de_que_no_ejecuta_operaciones(self):
        html = leer("index.html")
        self.assertIn("No ejecuta operaciones", html)

    def test_avisa_de_que_no_es_asesoramiento(self):
        self.assertIn("asesoramiento financiero", leer("index.html"))

    def test_el_coste_por_defecto_no_es_cero(self):
        html = leer("index.html")
        m = re.search(r'id="spread"[^>]*value="([\d.]+)"', html)
        self.assertIsNotNone(m, "no se encontro el campo de coste")
        self.assertGreater(float(m.group(1)), 0.0,
                           "evaluar sin coste fabrica ganadores que no existen")

    def test_el_contador_de_intentos_esta_en_la_interfaz(self):
        self.assertIn('id="intentos"', leer("index.html"))


if __name__ == "__main__":
    unittest.main()
