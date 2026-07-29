"""Biblioteca de estrategias guardadas en disco.

Una estrategia que se pierde al cerrar la pestanya no sirve para iterar. Este
modulo la persiste como JSON en un directorio, junto al veredicto que obtuvo la
ultima vez, para que al reabrirla se vea tanto la regla como lo que dio.

## El nombre lo escribe el usuario

Y eso lo convierte en entrada no confiable. `_nombre_seguro` reduce el nombre a
letras, digitos, guiones y guiones bajos antes de tocar el sistema de ficheros:
sin barras, sin `..`, sin rutas absolutas, sin nombres reservados de Windows.
Todas las operaciones verifican ademas que la ruta resultante quede dentro del
directorio de la biblioteca, para que ningun caso raro se escape.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from kronos.research.reglas import EstrategiaDeclarativa

DIRECTORIO_POR_DEFECTO = Path("estrategias")

# Nombres que Windows no permite como fichero, con o sin extension.
_RESERVADOS = frozenset(
    ["con", "prn", "aux", "nul"]
    + [f"com{i}" for i in range(1, 10)]
    + [f"lpt{i}" for i in range(1, 10)]
)
_LIMPIO = re.compile(r"[^A-Za-z0-9_-]+")
_MAX_NOMBRE = 64


class BibliotecaError(RuntimeError):
    """Fallo al guardar o cargar una estrategia."""


def _nombre_seguro(nombre: str) -> str:
    """Convierte un nombre libre en un nombre de fichero sin sorpresas."""
    base = _LIMPIO.sub("_", (nombre or "").strip()).strip("_")[:_MAX_NOMBRE]
    if not base:
        raise BibliotecaError("el nombre queda vacio despues de limpiarlo")
    if base.lower() in _RESERVADOS:
        base = f"_{base}"
    return base


def _ruta(nombre: str, directorio: Path) -> Path:
    """Ruta del fichero, garantizando que cae dentro del directorio."""
    directorio = directorio.resolve()
    destino = (directorio / f"{_nombre_seguro(nombre)}.json").resolve()
    if destino.parent != directorio:
        raise BibliotecaError(f"ruta fuera de la biblioteca: {destino}")
    return destino


@dataclass(frozen=True, slots=True)
class Entrada:
    """Una estrategia guardada, con lo que se sepa de su ultimo veredicto."""

    nombre: str
    estrategia: EstrategiaDeclarativa
    guardada_en: float
    veredicto: Optional[dict[str, Any]] = None

    @property
    def fecha(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.guardada_en))

    @property
    def sobrevivio(self) -> Optional[bool]:
        if not self.veredicto:
            return None
        return bool(self.veredicto.get("superviviente"))


def guardar(estrategia: EstrategiaDeclarativa,
            directorio: Path | str = DIRECTORIO_POR_DEFECTO, *,
            veredicto: Optional[dict[str, Any]] = None) -> Path:
    """Escribe la estrategia como JSON. Sobrescribe si ya existia."""
    directorio = Path(directorio)
    directorio.mkdir(parents=True, exist_ok=True)
    destino = _ruta(estrategia.nombre, directorio)
    payload = {
        "estrategia": estrategia.a_dict(),
        "guardada_en": time.time(),
        "veredicto": veredicto,
    }
    try:
        destino.write_text(json.dumps(payload, indent=2, ensure_ascii=True),
                           encoding="utf-8")
    except OSError as e:
        raise BibliotecaError(f"no se pudo escribir {destino.name}: {e}") from e
    return destino


def cargar(nombre: str, directorio: Path | str = DIRECTORIO_POR_DEFECTO) -> Entrada:
    """Lee una estrategia guardada."""
    ruta = _ruta(nombre, Path(directorio))
    if not ruta.exists():
        raise BibliotecaError(f"no existe la estrategia {nombre!r}")
    try:
        d = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise BibliotecaError(f"{ruta.name} no se puede leer: {e}") from e
    if not isinstance(d, dict) or "estrategia" not in d:
        raise BibliotecaError(f"{ruta.name} no tiene formato de estrategia")
    return Entrada(
        nombre=ruta.stem,
        estrategia=EstrategiaDeclarativa.desde_dict(d["estrategia"]),
        guardada_en=float(d.get("guardada_en", 0.0)),
        veredicto=d.get("veredicto"),
    )


def listar(directorio: Path | str = DIRECTORIO_POR_DEFECTO) -> list[Entrada]:
    """Todas las estrategias guardadas, de la mas reciente a la mas antigua.

    Un fichero corrupto se ignora en vez de tumbar el listado: que una
    estrategia mal escrita impida ver las demas seria un mal intercambio.
    """
    directorio = Path(directorio)
    if not directorio.is_dir():
        return []
    entradas: list[Entrada] = []
    for ruta in directorio.glob("*.json"):
        try:
            entradas.append(cargar(ruta.stem, directorio))
        except (BibliotecaError, ValueError):
            continue
    return sorted(entradas, key=lambda e: -e.guardada_en)


def borrar(nombre: str, directorio: Path | str = DIRECTORIO_POR_DEFECTO) -> bool:
    """Elimina una estrategia. Devuelve False si no existia."""
    ruta = _ruta(nombre, Path(directorio))
    if not ruta.exists():
        return False
    try:
        ruta.unlink()
    except OSError as e:
        raise BibliotecaError(f"no se pudo borrar {ruta.name}: {e}") from e
    return True
