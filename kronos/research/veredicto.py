"""Evaluacion honesta de UNA estrategia, con todos los filtros aplicados.

`barrido.py` corrige por las hipotesis que se prueban a la vez. Este modulo
resuelve el problema que aparece cuando hay una interfaz de por medio y las
hipotesis se prueban de una en una, a lo largo de una sesion:

    El usuario define una estrategia, no le gusta el resultado, cambia un umbral
    y vuelve a probar. Cuarenta veces.

Estadisticamente eso son cuarenta hipotesis contra los mismos datos, y el mejor
de esos cuarenta intentos se ve estupendo por puro azar. Que se prueben de una
en una no lo cambia; solo lo esconde. Por eso `Veredicto` recibe `intentos` y
corrige por el numero acumulado de evaluaciones contra ese mismo conjunto de
datos, no por el numero de reglas de la estrategia actual.

Es la diferencia entre una herramienta que te ayuda a medir y una que te ayuda
a autoenganyarte con mas comodidad.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from kronos.backtest.stops import evaluar_con_stops
from kronos.core.candle import Series
from kronos.research import hipotesis as hip
from kronos.research.barrido import evaluar as evaluar_binarias
from kronos.research.reglas import EstrategiaDeclarativa, senales

MIN_OPERACIONES = 100       # por debajo de esto no se concluye nada
ALFA = 0.05


@dataclass(frozen=True, slots=True)
class Tramo:
    """Resultado de un tramo, normalizado para binarias y para stop/objetivo."""

    n: int
    winrate: float
    umbral: float
    esperanza: float
    p_valor: float
    ic95: tuple[float, float]

    @property
    def edge(self) -> float:
        return self.winrate - self.umbral

    @property
    def suficiente(self) -> bool:
        return self.n >= MIN_OPERACIONES


def _tramo_binarias(series: Series, sen: Sequence[int], *, expiry: int,
                    payout: float, spread_pips: float, valor_pip: float) -> Tramo:
    r = evaluar_binarias(series, sen, expiry, payout, "", spread_pips, valor_pip)
    return Tramo(r.decisivas, r.winrate, r.umbral, r.esperanza, r.p_valor, r.ic95)


def _tramo_stops(series: Series, sen: Sequence[int], *, rr: float, atr_mult: float,
                 max_velas: int, spread_pips: float, valor_pip: float) -> Tramo:
    r = evaluar_con_stops(series, sen, rr=rr, atr_mult=atr_mult, max_velas=max_velas,
                          spread_pips=spread_pips, valor_pip=valor_pip)
    # Con coste, el umbral efectivo sube: hace falta (1 + coste_R) / (1 + rr).
    umbral_efectivo = (1.0 + r.coste_r) / (1.0 + rr)
    return Tramo(r.n, r.winrate, umbral_efectivo, r.esperanza_r, r.p_valor, r.ic95)


@dataclass(slots=True)
class Veredicto:
    """Lo que se le ensenya al usuario. Nunca un winrate a secas."""

    nombre: str
    modo: str                       # "binarias" | "stops"
    dentro: Tramo
    fuera: Tramo
    controles: dict[str, Tramo] = field(default_factory=dict)
    intentos: int = 1
    velas: int = 0

    @property
    def p_corregido(self) -> float:
        """Bonferroni sobre los intentos acumulados contra estos datos."""
        return min(1.0, self.dentro.p_valor * max(1, self.intentos))

    @property
    def mejor_control(self) -> Optional[tuple[str, Tramo]]:
        if not self.controles:
            return None
        return max(self.controles.items(), key=lambda kv: kv[1].winrate)

    @property
    def bate_a_los_controles(self) -> bool:
        mc = self.mejor_control
        return mc is None or self.dentro.winrate > mc[1].winrate

    @property
    def superviviente(self) -> bool:
        """Los cinco filtros a la vez. Fallar uno solo basta para descartar."""
        return (
            self.dentro.suficiente and self.fuera.suficiente
            and self.dentro.edge > 0 and self.fuera.edge > 0
            and self.p_corregido < ALFA and self.fuera.p_valor < ALFA
            and self.bate_a_los_controles
        )

    def motivos(self) -> list[str]:
        """Por que NO sobrevive. Lista vacia si sobrevive."""
        m: list[str] = []
        if not self.dentro.suficiente:
            m.append(f"Solo {self.dentro.n} operaciones dentro de muestra; "
                     f"hacen falta {MIN_OPERACIONES} para concluir algo.")
        if not self.fuera.suficiente:
            m.append(f"Solo {self.fuera.n} operaciones fuera de muestra; "
                     f"hacen falta {MIN_OPERACIONES}.")
        if self.dentro.edge <= 0:
            m.append(f"Edge negativo dentro de muestra ({self.dentro.edge * 100:+.2f}%): "
                     "no llega al umbral de equilibrio.")
        elif self.fuera.edge <= 0:
            m.append(f"El edge desaparece fuera de muestra ({self.fuera.edge * 100:+.2f}%). "
                     "Eso es sobreajuste, no una estrategia.")
        if self.dentro.edge > 0 and self.p_corregido >= ALFA:
            m.append(f"p corregido = {self.p_corregido:.3f} tras {self.intentos} "
                     f"intento(s) sobre estos datos: no se distingue del azar.")
        elif self.fuera.edge > 0 and self.fuera.p_valor >= ALFA:
            m.append(f"Fuera de muestra p = {self.fuera.p_valor:.3f}: no significativo.")
        mc = self.mejor_control
        if mc and not self.bate_a_los_controles:
            m.append(f"El control '{mc[0]}' acierta mas ({mc[1].winrate * 100:.2f}% "
                     f"frente a {self.dentro.winrate * 100:.2f}%): la estrategia no "
                     "aporta capacidad predictiva.")
        return m

    def dictamen(self) -> str:
        return "SOBREVIVE" if self.superviviente else "NO SOBREVIVE"

    def informe(self) -> str:
        """Tabla ASCII: la consola de Windows no siempre resuelve UTF-8."""
        u = "esperanza (R)" if self.modo == "stops" else "esperanza"
        L = ["=" * 72,
             f"  {self.nombre}",
             f"  modo {self.modo} | {self.velas:,} velas | intento numero {self.intentos}",
             "=" * 72, ""]
        L.append(f"  {'tramo':<26}{'N':>7}{'winrate':>10}{'umbral':>9}"
                 f"{'edge':>9}{'p':>8}")
        L.append("  " + "-" * 69)
        for etiqueta, t in (("dentro de muestra", self.dentro), ("fuera de muestra", self.fuera)):
            L.append(f"  {etiqueta:<26}{t.n:>7,}{t.winrate * 100:>9.2f}%"
                     f"{t.umbral * 100:>8.2f}%{t.edge * 100:>+8.2f}%{t.p_valor:>8.3f}")
        if self.controles:
            L.append("")
            L.append("  " + "-" * 69)
            for nombre, t in self.controles.items():
                L.append(f"  {'[control] ' + nombre:<26}{t.n:>7,}{t.winrate * 100:>9.2f}%"
                         f"{t.umbral * 100:>8.2f}%{t.edge * 100:>+8.2f}%{t.p_valor:>8.3f}")
        L += ["",
              f"  p corregido por {self.intentos} intento(s): {self.p_corregido:.4f}",
              f"  {u} dentro de muestra: {self.dentro.esperanza:+.4f}",
              "", "=" * 72,
              f"  VEREDICTO: {self.dictamen()}",
              "=" * 72]
        for m in self.motivos():
            L.append(f"  - {m}")
        if self.superviviente:
            L.append("  Pasa los cinco filtros. Sigue siendo un resultado historico:")
            L.append("  validalo en cuenta demo antes de arriesgar nada.")
        L.append("=" * 72)
        return "\n".join(L)


# --------------------------------------------------------------------- #
# Evaluacion
# --------------------------------------------------------------------- #
_CONTROLES = {
    "siempre CALL": hip.siempre_call,
    "siempre PUT": hip.siempre_put,
    "moneda al aire": hip.aleatoria,
}


def evaluar_estrategia(series: Series, estrategia: EstrategiaDeclarativa, *,
                       modo: str = "binarias", split: float = 0.6,
                       intentos: int = 1,
                       expiry: int = 5, payout: float = 0.84,
                       rr: float = 2.0, atr_mult: float = 1.5, max_velas: int = 48,
                       spread_pips: float = 0.5, valor_pip: float = 0.0001,
                       con_controles: bool = True) -> Veredicto:
    """Evalua la estrategia dentro y fuera de muestra, contra controles.

    `intentos` es el numero acumulado de estrategias probadas contra ESTOS datos
    en la sesion, no el numero de reglas. Ver docstring del modulo.
    """
    if modo not in ("binarias", "stops"):
        raise ValueError(f"modo debe ser 'binarias' o 'stops', recibido {modo!r}")
    if not 0.1 <= split <= 0.9:
        raise ValueError(f"split debe estar entre 0.1 y 0.9, recibido {split}")
    if intentos < 1:
        raise ValueError("intentos debe ser >= 1")

    corte = int(len(series) * split)
    s_dentro, s_fuera = series[:corte], series[corte:]

    def medir(s: Series, sen: Sequence[int]) -> Tramo:
        if modo == "binarias":
            return _tramo_binarias(s, sen, expiry=expiry, payout=payout,
                                   spread_pips=spread_pips, valor_pip=valor_pip)
        return _tramo_stops(s, sen, rr=rr, atr_mult=atr_mult, max_velas=max_velas,
                            spread_pips=spread_pips, valor_pip=valor_pip)

    controles: dict[str, Tramo] = {}
    if con_controles:
        for nombre, gen in _CONTROLES.items():
            controles[nombre] = medir(s_dentro, gen(s_dentro))

    return Veredicto(
        nombre=estrategia.nombre,
        modo=modo,
        dentro=medir(s_dentro, senales(s_dentro, estrategia)),
        fuera=medir(s_fuera, senales(s_fuera, estrategia)),
        controles=controles,
        intentos=intentos,
        velas=len(series),
    )
