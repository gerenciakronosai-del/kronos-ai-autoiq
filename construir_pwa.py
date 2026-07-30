"""Empaqueta el nucleo de Kronos para que corra dentro del navegador.

Kronos es Python puro sin dependencias, y esa restriccion —que costo trabajo
mantener— es justo lo que permite esto: Pyodide compila CPython a WebAssembly y
puede cargar cualquier paquete sin extensiones compiladas. El motor entero corre
en el movil del usuario.

Consecuencias, que son la razon de elegir este camino:

* **Sin servidor.** Nada que desplegar, nada que pagar, nada que mantener.
* **Sin envio de datos.** El CSV del usuario no sale de su dispositivo, asi que
  no hay nada que custodiar ni ninguna politica de privacidad que prometer de
  mas.
* **Funciona sin conexion** una vez instalada.

Uso:

    python construir_pwa.py

Deja `pwa/kronos.zip` y `pwa/demo.csv` listos para servir como ficheros
estaticos.
"""

from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
PWA = RAIZ / "pwa"

# El navegador no necesita el bot en vivo, ni el bróker, ni el cerebro IA: todos
# usan red o hilos, que en WebAssembly no funcionan igual. Se excluyen para que
# el zip sea pequenyo y para que nadie cargue por error codigo que ahi no corre.
PAQUETES_EXCLUIDOS = {"live", "broker", "ia"}

# La CLI y el punto de entrada importan esos mismos paquetes, asi que fallarian
# al cargarse. En el navegador no hay linea de comandos que valga.
MODULOS_EXCLUIDOS = {"cli.py", "__main__.py"}


def _se_excluye(rel: Path) -> bool:
    if "__pycache__" in rel.parts:
        return True
    if rel.name in MODULOS_EXCLUIDOS and len(rel.parts) == 2:
        return True
    return len(rel.parts) > 2 and rel.parts[1] in PAQUETES_EXCLUIDOS


def construir_zip(destino: Path) -> tuple[int, int]:
    """Comprime el paquete `kronos`. Devuelve (ficheros, bytes)."""
    origen = RAIZ / "kronos"
    ficheros = 0
    destino.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for ruta in sorted(origen.rglob("*.py")):
            rel = ruta.relative_to(RAIZ)
            if _se_excluye(rel):
                continue
            z.write(ruta, rel.as_posix())
            ficheros += 1
        # py.typed no es codigo pero mantiene el paquete bien formado.
        marca = origen / "py.typed"
        if marca.exists():
            z.write(marca, marca.relative_to(RAIZ).as_posix())
            ficheros += 1
    return ficheros, destino.stat().st_size


def copiar_demo(destino: Path) -> int:
    """Datos de ejemplo, para que la app sirva de algo en el primer arranque."""
    for candidato in ("eth_d1.csv", "btc_d1.csv"):
        origen = RAIZ / "data" / candidato
        if origen.exists():
            shutil.copyfile(origen, destino)
            return destino.stat().st_size

    # Sin datos reales a mano —el caso de la integracion continua, donde data/
    # no se versiona— se genera una serie sintetica reproducible. Es un paseo
    # aleatorio, asi que la app arranca ensenyando lo que debe ensenyar: que ahi
    # no hay ventaja ninguna.
    sys.path.insert(0, str(RAIZ))
    from kronos.data import loader, synthetic
    serie = synthetic.generate(
        synthetic.SyntheticParams(n=3000, timeframe=86400), seed=42, symbol="DEMO")
    loader.save_csv(serie, destino)
    return destino.stat().st_size


def main() -> int:
    PWA.mkdir(exist_ok=True)
    ficheros, peso = construir_zip(PWA / "kronos.zip")
    peso_demo = copiar_demo(PWA / "demo.csv")

    print(f"  kronos.zip   {ficheros:>3} modulos   {peso / 1024:>7.1f} KB")
    print(f"  demo.csv                    {peso_demo / 1024:>7.1f} KB")
    print()
    print("  Sirve la carpeta pwa/ como estatica. En local:")
    print("      python -m http.server 8503 --directory pwa")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
