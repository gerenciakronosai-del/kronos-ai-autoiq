"""Graficos en SVG, sin dependencias.

## Por que a mano y no con una libreria

La primera version usaba Altair. En la maquina de desarrollo fallaba asi:

    ImportError: DLL load failed while importing hashtable:
    Una directiva de Control de aplicaciones ha bloqueado este archivo

El Control de aplicaciones de Windows bloqueaba las DLL compiladas de pandas, y
Streamlit lo usa por dentro para serializar los datos de cualquier grafico. En
esa maquina, ningun grafico de Streamlit puede funcionar.

Se podria haber pedido al usuario que cambiara una politica de seguridad de su
sistema para ver una linea azul. Generar el SVG a mano cuesta doscientas lineas,
no arrastra nada compilado, y mantiene la promesa del repositorio: se clona y
funciona. Ademas es testeable sin navegador, que con un grafico de libreria no
lo es.

## Convenciones

* Coordenadas SVG: y crece hacia abajo, asi que los valores se invierten.
* Todo el texto es ASCII, como el resto de salidas del proyecto.
* Los valores se escapan antes de entrar en el SVG: los nombres de serie
  vienen de datos del usuario.
"""

from __future__ import annotations

from typing import Optional, Sequence

from kronos.research.curva import Operacion

# Paleta unica, para que los graficos se lean como un sistema.
AZUL = "#2E86DE"
VERDE = "#27AE60"
ROJO = "#E74C3C"
GRIS = "#8A8A8A"
GRIS_SUAVE = "#D8D8D8"


def _escapar(texto: str) -> str:
    """Nada de lo que venga de fuera entra crudo en el SVG."""
    return (str(texto).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _formato(v: float) -> str:
    """Numero legible sin notacion cientifica en los rangos habituales."""
    a = abs(v)
    if a >= 1000:
        return f"{v:,.0f}"
    if a >= 1:
        return f"{v:.2f}"
    return f"{v:.5g}"


class _Escala:
    """Convierte valores del dominio a pixeles."""

    __slots__ = ("lo", "hi", "desde_px", "hasta_px", "invertir")

    def __init__(self, lo: float, hi: float, desde_px: float, hasta_px: float,
                 invertir: bool = False):
        if hi - lo < 1e-12:      # serie plana: se abre un margen artificial
            centro = lo
            lo, hi = centro - 0.5, centro + 0.5
        self.lo, self.hi = lo, hi
        self.desde_px, self.hasta_px = desde_px, hasta_px
        self.invertir = invertir

    def __call__(self, v: float) -> float:
        t = (v - self.lo) / (self.hi - self.lo)
        if self.invertir:
            t = 1.0 - t
        return self.desde_px + t * (self.hasta_px - self.desde_px)


def _marco(ancho: int, alto: int, margen: dict[str, int],
           ejes_y: Sequence[tuple[float, float]], titulo_y: str) -> list[str]:
    """Rejilla horizontal con etiquetas. Devuelve fragmentos de SVG."""
    L = []
    for valor, y in ejes_y:
        L.append(f'<line x1="{margen["izq"]}" y1="{y:.1f}" '
                 f'x2="{ancho - margen["der"]}" y2="{y:.1f}" '
                 f'stroke="{GRIS_SUAVE}" stroke-width="1"/>')
        L.append(f'<text x="{margen["izq"] - 6}" y="{y + 4:.1f}" '
                 f'text-anchor="end" font-size="11" fill="{GRIS}" '
                 f'font-family="monospace">{_escapar(_formato(valor))}</text>')
    if titulo_y:
        L.append(f'<text x="4" y="14" font-size="11" fill="{GRIS}" '
                 f'font-family="sans-serif">{_escapar(titulo_y)}</text>')
    return L


def _ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if hi - lo < 1e-12:
        return [lo]
    paso = (hi - lo) / (n - 1)
    return [lo + i * paso for i in range(n)]


def curva_svg(curva: Sequence[float], *, ancho: int = 820, alto: int = 260) -> str:
    """Curva de capital: linea azul y referencia punteada en el capital inicial."""
    if len(curva) < 2:
        return _vacio(ancho, alto, "Sin operaciones que dibujar")

    m = {"izq": 70, "der": 16, "arriba": 24, "abajo": 28}
    lo, hi = min(curva), max(curva)
    holgura = (hi - lo) * 0.08 or 1.0
    ex = _Escala(0, len(curva) - 1, m["izq"], ancho - m["der"])
    ey = _Escala(lo - holgura, hi + holgura, m["arriba"], alto - m["abajo"],
                 invertir=True)

    L = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {ancho} {alto}" '
         f'width="100%" height="{alto}" role="img" '
         f'aria-label="Curva de capital">']
    L += _marco(ancho, alto, m,
                [(v, ey(v)) for v in _ticks(lo - holgura, hi + holgura)], "capital")

    inicial = curva[0]
    L.append(f'<line x1="{m["izq"]}" y1="{ey(inicial):.1f}" '
             f'x2="{ancho - m["der"]}" y2="{ey(inicial):.1f}" '
             f'stroke="{GRIS}" stroke-width="1" stroke-dasharray="4 4"/>')

    puntos = " ".join(f"{ex(i):.1f},{ey(v):.1f}" for i, v in enumerate(curva))
    L.append(f'<polyline points="{puntos}" fill="none" stroke="{AZUL}" '
             f'stroke-width="1.8" stroke-linejoin="round"/>')

    final = curva[-1]
    color = VERDE if final >= inicial else ROJO
    L.append(f'<circle cx="{ex(len(curva) - 1):.1f}" cy="{ey(final):.1f}" '
             f'r="3.5" fill="{color}"/>')
    L.append(f'<text x="{ancho - m["der"]}" y="{alto - 8}" text-anchor="end" '
             f'font-size="11" fill="{GRIS}" font-family="monospace">'
             f'{len(curva) - 1} operaciones</text>')
    L.append("</svg>")
    return "\n".join(L)


def precio_svg(closes: Sequence[float], operaciones: Sequence[Operacion],
               desde: int, hasta: int, *, ancho: int = 820, alto: int = 300) -> str:
    """Precio con las entradas marcadas: verde ganada, rojo perdida.

    El sentido se distingue por la forma: triangulo arriba para CALL, abajo para
    PUT. Con color y forma a la vez, el grafico sigue siendo legible para quien
    no distingue rojo y verde.
    """
    desde = max(0, desde)
    hasta = min(len(closes), hasta)
    if hasta - desde < 2:
        return _vacio(ancho, alto, "Rango demasiado corto")

    tramo = list(closes[desde:hasta])
    m = {"izq": 70, "der": 16, "arriba": 24, "abajo": 28}
    lo, hi = min(tramo), max(tramo)
    holgura = (hi - lo) * 0.08 or 1.0
    ex = _Escala(desde, hasta - 1, m["izq"], ancho - m["der"])
    ey = _Escala(lo - holgura, hi + holgura, m["arriba"], alto - m["abajo"],
                 invertir=True)

    L = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {ancho} {alto}" '
         f'width="100%" height="{alto}" role="img" '
         f'aria-label="Precio con las entradas marcadas">']
    L += _marco(ancho, alto, m,
                [(v, ey(v)) for v in _ticks(lo - holgura, hi + holgura)], "precio")

    puntos = " ".join(f"{ex(desde + i):.1f},{ey(v):.1f}" for i, v in enumerate(tramo))
    L.append(f'<polyline points="{puntos}" fill="none" stroke="{GRIS}" '
             f'stroke-width="1.2"/>')

    dibujadas = 0
    for op in operaciones:
        if not (desde <= op.i_entrada < hasta):
            continue
        x, y = ex(op.i_entrada), ey(op.precio_entrada)
        color = VERDE if op.ganada else ROJO
        # Triangulo: hacia arriba si CALL, hacia abajo si PUT.
        s = 5.0
        if op.direccion > 0:
            pts = f"{x:.1f},{y - s:.1f} {x - s:.1f},{y + s:.1f} {x + s:.1f},{y + s:.1f}"
        else:
            pts = f"{x:.1f},{y + s:.1f} {x - s:.1f},{y - s:.1f} {x + s:.1f},{y - s:.1f}"
        sentido = "CALL" if op.direccion > 0 else "PUT"
        estado = "ganada" if op.ganada else "perdida"
        L.append(f'<polygon points="{pts}" fill="{color}" fill-opacity="0.85" '
                 f'stroke="white" stroke-width="0.6">'
                 f'<title>vela {op.i_entrada} | {sentido} | {estado} | '
                 f'{op.resultado_r:+.2f}R | {op.velas} velas</title></polygon>')
        dibujadas += 1

    L.append(f'<text x="{ancho - m["der"]}" y="{alto - 8}" text-anchor="end" '
             f'font-size="11" fill="{GRIS}" font-family="monospace">'
             f'velas {desde}-{hasta} | {dibujadas} entradas</text>')
    L.append("</svg>")
    return "\n".join(L)


def _vacio(ancho: int, alto: int, mensaje: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {ancho} {alto}" '
            f'width="100%" height="{alto}">'
            f'<text x="{ancho // 2}" y="{alto // 2}" text-anchor="middle" '
            f'font-size="13" fill="{GRIS}" font-family="sans-serif">'
            f'{_escapar(mensaje)}</text></svg>')


def leyenda_svg(*, ancho: int = 820, alto: int = 30) -> str:
    """Leyenda compartida por los dos graficos."""
    items = [(VERDE, "ganada"), (ROJO, "perdida")]
    L = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {ancho} {alto}" '
         f'width="100%" height="{alto}">']
    x = 70
    for color, texto in items:
        L.append(f'<circle cx="{x}" cy="{alto // 2}" r="5" fill="{color}"/>')
        L.append(f'<text x="{x + 12}" y="{alto // 2 + 4}" font-size="11" '
                 f'fill="{GRIS}" font-family="sans-serif">{texto}</text>')
        x += 90
    L.append(f'<polygon points="{x},{alto // 2 - 5} {x - 5},{alto // 2 + 5} '
             f'{x + 5},{alto // 2 + 5}" fill="{GRIS}"/>')
    L.append(f'<text x="{x + 12}" y="{alto // 2 + 4}" font-size="11" fill="{GRIS}" '
             f'font-family="sans-serif">CALL</text>')
    x += 70
    L.append(f'<polygon points="{x},{alto // 2 + 5} {x - 5},{alto // 2 - 5} '
             f'{x + 5},{alto // 2 - 5}" fill="{GRIS}"/>')
    L.append(f'<text x="{x + 12}" y="{alto // 2 + 4}" font-size="11" fill="{GRIS}" '
             f'font-family="sans-serif">PUT</text>')
    L.append("</svg>")
    return "\n".join(L)
