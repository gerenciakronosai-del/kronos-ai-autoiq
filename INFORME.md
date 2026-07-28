# Informe de resultados

Qué se midió, con qué datos, y qué salió. Este documento existe porque la
conclusión del proyecto es negativa y una conclusión negativa sin metodología no
vale nada: cualquiera puede decir "no funciona".

**Resumen en una línea:** ninguna de las ~300 hipótesis probadas, sobre cinco
mercados y cuatro horizontes temporales, produjo una ventaja que sobreviviera a
los costes de operar.

---

## Cómo se decide si algo "funciona"

Cuatro filtros, todos obligatorios. Una hipótesis solo se llama *superviviente*
si pasa los cuatro.

**1. Supera el umbral de equilibrio.** No basta con acertar más de la mitad.

| Instrumento | Umbral | Fórmula |
|---|---|---|
| Binaria con payout 84% | 54,35% | `1 / (1 + payout)` |
| Stop y objetivo 2:1 | 33,33% | `1 / (1 + rr)` |
| Stop y objetivo 2:1 con comisión | `(1 + coste_R) / 3` | |

Esa tercera fila es la que más veces se olvida. Con una comisión que valga el 7%
del riesgo, el umbral real de un 2:1 no es 33,3% sino 35,7%.

**2. Es distinguible del azar.** p-valor binomial de una cola contra el umbral,
**corregido por Bonferroni** sobre el número real de hipótesis probadas. Con 72
hipótesis al 5% esperas ~3,6 falsos positivos aunque ninguna sirva.

**3. Sobrevive fuera de muestra.** El edge tiene que aguantar en un tramo que no
se miró al construir la regla.

**4. Bate a un control.** El catálogo incluye *siempre CALL*, *siempre PUT* y
*moneda al aire*. Sin ellos el resultado no es interpretable: si "siempre CALL"
saca edge, estás midiendo la deriva del periodo, no capacidad predictiva.

Además, **todos los backtests modelan el coste de operar** (spread por defecto
0,5 pips en forex, comisión taker 0,2% en cripto). Nunca cero. Es la salvaguarda
que más resultados tumba.

---

## Parte 1 — Opciones binarias sobre EURUSD

Payout real de la cuenta: **84%**, así que el umbral es 54,35%.

| Mercado | Muestra | Hipótesis | Supervivientes | Mejor resultado |
|---|---:|---:|---:|---|
| EURUSD 1 min | 744.403 velas (2024-2025) | 72 | **0** | — |
| EURUSD 1 hora | 149.617 velas (2000-2025, 25,6 años) | 90 | **0** | RSI extremo a 8 h: 52,81% |
| EURUSD-OTC (IQ Option) | 20.000 velas | 72 | **0** | 50,58% (control moneda: 48,00%) |

La estrategia `confluence` sobre EURUSD de 1 minuto: **48,31% en 3.508
operaciones**. Simulada con gestión de riesgo completa, la cuenta cayó de $1.000
a $99,84 y el *kill switch* la detuvo a los once meses.

### El hallazgo metodológico más importante

El mismo edge, sobre el mismo activo y la misma regla, según cuántos datos mires:

| Muestra | Winrate | Edge |
|---|---:|---:|
| 12.468 velas (2 años) | 56,36% | **+2,01%** |
| 36.565 velas (6 años) | 53,48% | −0,87% |
| 149.617 velas (25 años) | 52,81% | −1,54% |

**El edge positivo se evaporó al multiplicar la muestra por doce.** No cambió la
estrategia ni el mercado: cambió cuánto se miraba. Lo mismo ocurrió en ejecución
en vivo, donde el bot dio 80% de aciertos en 10 operaciones y 40% en las 5
siguientes.

Corolario práctico: **desconfía de cualquier resultado con menos de 1.000
operaciones**, incluido el tuyo.

### Por qué alargar el horizonte no bastó

Alargar el vencimiento sí mejora la relación entre movimiento predecible y
spread — de 1x a 1 minuto a 9,6x a 1 hora — y el edge sube de −6,81% a −1,54%.
Pero no llega a cruzar el umbral. El bróker calibra el payout justo por encima
de lo que el mercado ofrece; ahí no hay hueco.

---

## Parte 2 — Cripto con stop y objetivo

Cambio de terreno deliberado, atacando las dos limitaciones anteriores:

* **De binarias a stop/objetivo.** El umbral deja de fijarlo el bróker. Con 2:1
  basta acertar el 33,3% en vez del 54,35%.
* **De forex a cripto.** Mayor volatilidad respecto al coste, mercado 24/7 y una
  API pública y documentada (Binance) en vez de ingeniería inversa.

Estrategia: ruptura de bandas de Bollinger. Stop a 1,5× ATR, objetivo a 2R,
comisión taker 0,2%, horizonte máximo 30-48 velas.

| Velas | Activos | Comisión / R | Esperanza media | Positivas | vs control |
|---|---:|---:|---:|---:|---:|
| 1 hora | 1 | 18% | muy negativa | — | — |
| 4 horas | 7 | 5-10% | −0,134R | 1 de 7 | **+2,26%** |
| **Diaria** | 7 | 1-3% | **−0,085R** | 2 de 7 | +1,52% |
| Semanal | 6 | <1% | −0,201R | 1 de 6 | **−12,19%** |

Hay un óptimo en velas diarias y **no cruza cero**. Por debajo lo mata el coste;
por encima, la muestra se queda sin operaciones suficientes.

### El efecto es real, pero es más pequeño que el peaje

La columna "vs control" es positiva y consistente a 4 horas y en diario: la
ruptura de Bollinger bate a un control ingenuo por **+1,5 a +2,3 puntos** de
winrate, en dos horizontes independientes y unas 14.000 operaciones. Eso no es
ruido; es el efecto momentum que la literatura describe en cripto.

El problema es la aritmética: el efecto vale ~2 puntos y operarlo cuesta ~4.

### La trampa del horizonte semanal

En semanal el signo se invierte (−12,19% frente al control) y el motivo no es la
estrategia, es la muestra. Separando por dirección:

| | Operaciones | Winrate |
|---|---:|---:|
| CALL | 320 | 35,6% |
| PUT | 82 | **4,9%** |

Cero por ciento de acierto en los PUT de cinco de los seis activos. La muestra
son nueve años de mercado alcista: a horizonte semanal y con objetivo a 2R,
ponerse corto en cripto prácticamente nunca llegó al objetivo.

Y el detalle que cierra el argumento: **los CALL de la estrategia (35,6%)
rinden menos que comprar en momentos arbitrarios (39-45% del control).** Un
`siempre CALL` rentable en esta muestra no es una ventaja: es que el mercado
subió. Confundir las dos cosas es sobreajustar a la época en vez de a los datos.

---

## Conclusión

No se encontró ventaja explotable en ningún instrumento ni horizonte probado.

Eso no demuestra que no exista ninguna en ningún sitio — demuestra que no está
en el sitio obvio, que es donde busca la mayoría: indicadores técnicos estándar
sobre precios públicos, con costes de minorista.

Lo que sí queda demostrado, y es reproducible con los comandos de abajo:

1. El coste de operar no es un detalle de segundo orden. Un spread de 0,2 pips
   borró una señal de 12,9 sigmas.
2. Las muestras pequeñas mienten sistemáticamente y en la dirección que te gusta.
3. Sin un control con el que comparar, un winrate no significa nada.

---

## Reproducir

Sin dependencias externas: solo Python 3.11+.

```bash
python -m kronos selftest        # 359 tests, ~47 s, sin red
```

**Forex.** Descarga *1 Minute Bar Quotes* de EURUSD en
[HistData.com](https://www.histdata.com/download-free-forex-historical-data/)
(gratis, sin registro) y:

```bash
python -m kronos importar ~/Descargas/HISTDATA_*.zip --out data/eurusd.csv
python -m kronos explorar --data data/eurusd.csv --symbol EURUSD --spread 0.5
```

Para el tramo de 25 años, reagrupa a velas horarias con `--timeframe 3600` en el
importador.

**Cripto.** Los datos se bajan solos de la API pública de Binance:

```bash
python -m kronos cripto --symbol ETHUSDT --timeframe 86400 --velas 3000
```

`--timeframe` admite `3600` (1 h), `14400` (4 h), `86400` (diario) y `604800`
(semanal).

---

## Limitaciones conocidas

Para que nadie tenga que descubrirlas leyendo el código:

* **Los datos de forex son del contado, no del bróker.** Las binarias se liquidan
  contra el feed propio del bróker, que no es idéntico, sobre todo en el precio
  exacto de vencimiento. El backtest es orientativo.
* **`kronos/broker/iqoption.py` es un adaptador estructural.** Depende de
  `iqoptionapi`, un cliente comunitario de ingeniería inversa que no es
  dependencia del proyecto. `activos_abiertos()` devolvió 0 contra una cuenta
  real y no se llegó a diagnosticar.
* **La línea "Cobertura %" del importador asume 1.440 velas por día** sea cual
  sea el timeframe, así que infravalora la cobertura de datos horarios.
* **El backtest no modela latencia, requotes ni payouts variables.**
* **Un test de `tests/test_live.py` depende de temporización** y puede fallar de
  forma intermitente en máquinas cargadas.
* **La muestra de cripto semanal (402 operaciones) es insuficiente** para
  concluir por sí sola. Se corrió sabiéndolo y se reporta con esa advertencia.

---

## Aviso

Software educativo y de investigación. Nada aquí es asesoramiento financiero.
Las opciones binarias son instrumentos de alto riesgo con esperanza negativa por
diseño, y están restringidas o prohibidas para minoristas en varias
jurisdicciones (entre ellas la UE, vía ESMA).
