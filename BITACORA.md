# Bitácora del proyecto

Todo lo que se ha hecho, en orden, con lo que salió bien y lo que salió mal.
Este documento se actualiza cada vez que el proyecto cambia.

**Última actualización:** 29 de julio de 2026 · commit `081d953` · 488 tests

---

## Índice

1. [De dónde salió esto](#1-de-dónde-salió-esto)
2. [El motor](#2-el-motor)
3. [Datos reales](#3-datos-reales)
4. [En vivo: panel, IA y bróker](#4-en-vivo-panel-ia-y-bróker)
5. [La búsqueda del edge](#5-la-búsqueda-del-edge)
6. [Cambio de terreno: stops y cripto](#6-cambio-de-terreno-stops-y-cripto)
7. [El cierre y la publicación](#7-el-cierre-y-la-publicación)
8. [Kronos Studio](#8-kronos-studio)
9. [La PWA](#9-la-pwa)
10. [Estado actual](#10-estado-actual)
11. [Invariantes que no se tocan](#11-invariantes-que-no-se-tocan)
12. [Errores cometidos](#12-errores-cometidos)
13. [Pendiente](#13-pendiente)

---

## 1. De dónde salió esto

La petición inicial fue que actuara como un algoritmo de trading de alta
frecuencia y devolviera señales `CALL`/`PUT` en JSON para operar opciones
binarias de 1 a 5 minutos.

**No lo hice**, y conviene que quede escrito por qué: emitir señales de compra o
venta sobre dinero real es asesoramiento financiero personalizado, y no soy un
asesor autorizado. Lo que sí se podía hacer —y se hizo— es construir el software
que responde la pregunta de debajo: *¿tiene ventaja esta estrategia, sí o no?*

A partir de ahí el encargo fue de autonomía total: «que el proyecto quede de 0 a
100». El resultado es un repositorio de ~14.000 líneas con 488 tests.

---

## 2. El motor

Python 3.11+ puro, **cero dependencias externas** en el paquete `kronos`. Se
clona y funciona.

| Capa | Qué hace |
|---|---|
| `core/` | `Candle` inmutable y autovalidada, `Series`, y ocho indicadores |
| `strategy/` | Reglas de decisión con filtros de régimen → `Signal` |
| `risk/` | Límites y sizing, **con derecho de veto** sobre la estrategia |
| `backtest/` | Simulación honesta + métricas con contraste estadístico |
| `data/` | Carga CSV/JSON, importador de HistData, descarga de Binance |
| `broker/` | Ejecución: papel (por defecto) e IQ Option (opcional) |
| `research/` | Barrido de hipótesis, reglas declarativas, veredicto, gráficos |
| `live/` | Bucle de ejecución en tiempo real |
| `ia/` | Cerebro opcional sobre la API de Anthropic |

Las cuatro capas centrales están separadas a propósito: la estrategia **no sabe**
cuánto dinero hay, el riesgo **no sabe** por qué se generó la señal, el backtest
**no sabe** cómo se calcula un RSI.

### Decisiones de diseño que importan

**El winrate nunca se muestra solo.** Siempre junto al umbral de equilibrio
`1/(1+payout)`, el edge y un p-valor. Un informe que solo enseña aciertos induce
a desplegar sistemas en pérdida estructural.

**No hay optimizador de parámetros, y es deliberado.** Una rejilla sobre 15
umbrales encuentra siempre una combinación que brilla en el histórico y casi
nunca fuera de él. Añadirlo habría hecho el proyecto más vendible y menos útil.

**No hay martingala ni progresión tras pérdida.** En un instrumento de esperanza
negativa, doblar no mejora la esperanza: concentra toda la ruina en una racha.
`test_sin_martingala_tras_perder` impide que se cuele en el futuro.

**El coste de operar se modela siempre**, por defecto 0,5 pips y nunca cero.

---

## 3. Datos reales

Fuente recomendada: **HistData.com** — gratis, sin registro, velas de 1 minuto
desde el año 2000. Se descargaron **25,6 años de EURUSD**.

Dos detalles del formato que arruinan un backtest y que el importador resuelve:

- **Las marcas temporales están en EST sin horario de verano** (UTC-5 todo el
  año). Cargarlas como UTC desplaza los límites de día del gestor de riesgo cinco
  horas y parte las sesiones por la mitad.
- **La columna de volumen siempre vale 0.** No es un fallo de descarga: en forex
  no existe volumen agregado real.

Después se añadió descarga desde la **API pública de Binance** (sin
credenciales), con intervalos de 1 minuto a semanal.

---

## 4. En vivo: panel, IA y bróker

- **Panel Streamlit** (`dashboard/app.py`, puerto 8501) para monitorizar el bot.
- **Bucle de 5 segundos** con detección de parada del feed.
- **Cerebro IA opcional** sobre `claude-opus-5`, con *fallo cerrado*: cualquier
  error —timeout, cuota, red, respuesta fuera de contrato— devuelve `ESPERAR`,
  nunca una excepción ni una decisión inventada.
- **IQ Option en cuenta DEMO**, verificado de extremo a extremo.

### El coste manda en el diseño

Un ciclo de 5 segundos son 17.280 llamadas al día: unos **$86/día** con Opus 5.
Y en velas de 1-5 minutos, once de cada doce analizan la misma vela.

| Cadencia | Llamadas/día | Coste/día |
|---|---:|---:|
| Cada 5 s | 17.280 | ~$86 |
| Al cerrar vela de 1 min | 1.440 | ~$7 |
| Al cerrar vela de 5 min | 288 | ~$1,4 |

Por eso `solo_en_cierre_de_vela` viene activado. Además el prompt de sistema (847
tokens) se cachea, y la salida se fuerza con esquema JSON.

### Por qué reglas y no un LLM en el lazo

| | LLM por vela | Motor de reglas |
|---|---|---|
| Mismo input → mismo output | no garantizado | garantizado |
| Backtesteable sobre 100k velas | inviable | segundos |
| Coste por decisión | por token | cero |

### Seguridad

- Credenciales **solo** por variable de entorno (`ANTHROPIC_API_KEY`,
  `IQ_EMAIL`, `IQ_PASSWORD`). Nunca por CLI ni fichero.
- Operar en REAL exige **dos gestos independientes**: `tipo_cuenta=REAL` y
  `KRONOS_ALLOW_REAL=1`. Me negué a quitar esas dos puertas.
- Durante la sesión se expusieron una clave de API y una contraseña en capturas.
  En ambos casos avisé de revocarlas de inmediato; nunca las usé ni las guardé.

---

## 5. La búsqueda del edge

Payout real de la cuenta: **84%** → umbral **54,35%**.

| Mercado | Muestra | Hipótesis | Supervivientes |
|---|---:|---:|---:|
| EURUSD 1 min | 744.403 velas | 72 | **0** |
| EURUSD 1 hora | 149.617 velas (25,6 años) | 90 | **0** |
| EURUSD-OTC de IQ Option | 20.000 velas | 72 | **0** |

La estrategia `confluence` sobre EURUSD de 1 minuto: **48,31% en 3.508
operaciones**. La cuenta simulada cayó de $1.000 a $99,84 y el kill switch la
paró a los once meses.

### El hallazgo más importante de todo el proyecto

El mismo edge, misma regla, mismo activo, según cuántos datos se miren:

| Muestra | Winrate | Edge |
|---|---:|---:|
| 12.468 velas (2 años) | 56,36% | **+2,01%** |
| 36.565 velas (6 años) | 53,48% | −0,87% |
| 149.617 velas (25 años) | 52,81% | −1,54% |

**El edge positivo se evaporó al multiplicar la muestra por doce.** No cambió la
estrategia ni el mercado: cambió cuánto se miraba. Lo mismo pasó en vivo, donde
el bot dio 80% en 10 operaciones y 40% en las 5 siguientes.

Corolario: **desconfía de cualquier resultado con menos de 1.000 operaciones**,
incluido el tuyo.

---

## 6. Cambio de terreno: stops y cripto

Se atacaron las dos limitaciones a la vez: de binarias a stop/objetivo (el umbral
deja de fijarlo el bróker: 33,3% en vez de 54,35%) y de forex a cripto (mayor
volatilidad respecto al coste, mercado 24/7, API pública).

Estrategia: ruptura de Bollinger, stop a 1,5× ATR, objetivo 2R, comisión 0,2%.

| Velas | Activos | Comisión / R | Esperanza media | Positivas | vs control |
|---|---:|---:|---:|---:|---:|
| 1 hora | 1 | 18% | muy negativa | — | — |
| 4 horas | 7 | 5-10% | −0,134R | 1 de 7 | **+2,26%** |
| **Diaria** | 7 | 1-3% | **−0,085R** | 2 de 7 | +1,52% |
| Semanal | 6 | <1% | −0,201R | 1 de 6 | **−12,19%** |

Hay un óptimo en diario y **no cruza cero**.

### El umbral estaba incompleto en todo el proyecto

Con comisión, el umbral de un 2:1 no es 33,3% sino **`(1 + coste_R) / 3`**. Con
una comisión que valga el 7% del riesgo, el listón sube a **35,7%**. Se corrigió
y ahora se calcula y se muestra siempre.

### El efecto existe, pero es más pequeño que el peaje

La ruptura de Bollinger bate a un control ingenuo por **+1,5 a +2,3 puntos** de
winrate, de forma consistente, en dos horizontes independientes y ~14.000
operaciones. Eso no es ruido: es el efecto momentum que la literatura describe en
cripto. El problema es aritmético: **el efecto vale ~2 puntos y operarlo cuesta
~4**.

### La trampa del semanal

En semanal el signo se invierte (−12,19% frente al control) y el motivo no es la
estrategia sino la muestra:

| | Operaciones | Winrate |
|---|---:|---:|
| CALL | 320 | 35,6% |
| PUT | 82 | **4,9%** |

Cero por ciento de acierto en los PUT de cinco de seis activos: la muestra son
nueve años de mercado alcista. Y el detalle que cierra el argumento — **los CALL
de la estrategia (35,6%) rinden menos que comprar en momentos arbitrarios
(39-45% del control)**. Un `siempre CALL` rentable no es una ventaja: es que el
mercado subió. Confundirlo es sobreajustar a la época en vez de a los datos.

---

## 7. El cierre y la publicación

Se decidió cerrar el proyecto y publicarlo, documentando el resultado negativo en
vez de esconderlo.

- **`INFORME.md`** con metodología, resultados y limitaciones conocidas.
- **README reordenado**: la primera frase pasó de «se construyó para encontrar
  una estrategia rentable, no la encontró» a «un backtester construido para no
  poder mentirte». Mismos hechos, énfasis correcto.
- **Traducción al inglés** (`README.en.md`, `REPORT.en.md`) con selector de
  idioma.
- **Publicado** en
  [github.com/gerenciakronosai-del/kronos-ai-autoiq](https://github.com/gerenciakronosai-del/kronos-ai-autoiq),
  licencia MIT, con descripción y ocho topics.

Antes de publicar se corrigió un agujero del `.gitignore`: ignoraba `data/*.csv`
pero **no los zips de HistData ni los `.jsonl`** — 114 MB que se habrían subido,
incluidos los registros de decisiones. Redistribuir los ficheros de HistData
tampoco lo permite su licencia.

---

## 8. Kronos Studio

Banco de pruebas donde se define una estrategia **sin escribir código** y se mide
con los cinco filtros. Puerto 8502, `iniciar_estudio.bat`.

- **`research/reglas.py`** — estrategias como datos: once canales, seis
  operadores (incluidos los de cruce), condiciones combinadas con Y, y
  serialización JSON. El vocabulario **no permite expresar una condición que mire
  al futuro**, y `test_sin_look_ahead` lo verifica sobre los once canales.
- **`research/veredicto.py`** — los cinco filtros sobre una sola estrategia. Si
  no sobrevive, `motivos()` explica exactamente por qué.
- **`research/curva.py`** — operaciones una a una, curva de capital con riesgo
  fijo, peor caída y pérdidas seguidas.
- **`research/biblioteca.py`** — persistencia en JSON, con el nombre saneado.
- **`research/grafico.py`** — SVG a mano, sin dependencias.

### El contador de intentos

Es la decisión que separa esto de cualquier otro backtester con interfaz. Un
barrido corrige por las hipótesis simultáneas; una interfaz tiene el problema
inverso y peor:

> Defines una estrategia, no te gusta, mueves un umbral y vuelves a probar.
> Cuarenta veces.

Eso son cuarenta hipótesis contra los mismos datos, y la mejor sale preciosa por
azar. Probarlas de una en una no lo evita: **solo lo esconde**. El contador está
a la vista, el p-valor se multiplica por él, y el historial de la sesión se
muestra entero.

### Por qué los gráficos son SVG escritos a mano

La primera versión usaba Altair y falló así:

```
ImportError: DLL load failed while importing hashtable:
Una directiva de Control de aplicaciones ha bloqueado este archivo
```

El Control de aplicaciones de Windows bloquea las DLL compiladas de pandas, y
Streamlit lo usa por dentro para cualquier gráfico. La alternativa era pedir que
se cambiara una política de seguridad del sistema para ver una línea azul.
Doscientas líneas de SVG mantienen la promesa de que se clona y funciona, y
además se pueden testear sin navegador.

---

## 9. La PWA

Se estudió publicar en App Store y Play Store. Conclusión: **las opciones
binarias están prohibidas por categoría en Google Play**, y Apple exige que las
apps de trading de valores las publique la entidad financiera autorizada. Además
lo que había era Streamlit, que no es una app móvil y no puede llegar a serlo
sin reescribir la interfaz entera.

Se eligió **PWA**: instalable desde el navegador, sin tiendas, sin comisiones,
sin revisión. Y una decisión de arquitectura que el proyecto llevaba preparando
sin saberlo:

**El motor corre dentro del navegador, sobre Pyodide.** `kronos` es Python puro
sin dependencias —restricción que costó trabajo mantener— y eso es exactamente
lo que permite compilarlo a WebAssembly. Consecuencias:

* **Sin servidor.** Nada que desplegar, nada que pagar, nada que mantener.
* **Sin envío de datos.** El CSV del usuario no sale de su dispositivo, así que
  no hay nada que custodiar ni política de privacidad que prometer de más.
* **Funciona sin conexión** tras la primera carga.

El paquete comprimido pesa **59 KB**. El runtime de Python son ~10 MB que se
cachean aparte, en su propia caché, para no volver a bajarlos nunca.

Regla que gobierna `pwa/app.js`: **ningún cálculo estadístico vive en
JavaScript.** Umbrales, p-valores, corrección por intentos y gráficos salen de
`kronos`, que está cubierto por tests. Reimplementarlos en JS crearía una segunda
verdad sin tests que la sujete, y la versión que ve el usuario sería justo la no
verificada.

La app **no ejecuta operaciones ni mueve dinero**, y lo dice en su propia
interfaz. Es lo único de todo esto que puede ser honesto: vender la medición, no
la promesa.

---

## 10. Estado actual

```bash
python -m kronos selftest      # 488 tests, ~68 s, sin red
python -m kronos demo          # pipeline completo
iniciar_estudio.bat            # Kronos Studio, puerto 8502
iniciar_panel.bat              # panel del bot, puerto 8501
python construir_pwa.py        # empaqueta el motor para el navegador
python -m http.server 8503 --directory pwa    # sirve la PWA
```

Comandos: `decide`, `backtest`, `validar`, `explorar`, `cripto`, `descargar`,
`importar`, `duelo`, `broker`, `paper`, `indicadores`, `datos`, `config-init`,
`demo`, `selftest`.

### Historial de commits

| Commit | Qué trajo |
|---|---|
| `e8e03ac` | El motor completo, 359 tests |
| `6914845` | README reordenado: liderar con lo que demuestra |
| `b1da402` | Versión en inglés del README y del informe |
| `7bc5a36` | Kronos Studio: estrategias declarativas + veredicto |
| `ee702bb` | Curva de capital, entradas sobre precio, biblioteca |
| `081d953` | Esta bitácora |

---

## 11. Invariantes que no se tocan

1. **Sin look-ahead.** El valor en `i` solo depende de datos `<= i`.
2. **El winrate nunca se muestra solo.**
3. **Nada de martingala.**
4. **El riesgo veta a la estrategia**, nunca al revés.
5. **Salida ASCII** en los informes.
6. **`decide` escribe solo JSON en stdout**; los avisos van a stderr.
7. **Credenciales solo por variable de entorno.**
8. **El coste de operar se modela siempre**, nunca cero.
9. **Convención pesimista en stops**: si caben stop y objetivo en la misma vela,
   cuenta STOP.
10. **La demo debe terminar en `NO DESPLEGAR`.** Si algún día sale positiva sobre
    la serie sintética —que es un paseo aleatorio— es una alarma de que se ha
    colado un sesgo, no una mejora.

---

## 12. Errores cometidos

Se listan porque forman parte del historial real y porque un proyecto que
presume de no engañarse tampoco debería maquillar esto.

**En el código, encontrados por los tests o por medición:**

- El umbral con stops era `1/(1+rr)` e ignoraba la comisión. Corregido a
  `(1+coste_R)/(1+rr)`.
- Detección de timestamps: 1,7e12 se leía como microsegundos.
- Mercado plano reportado como «calentamiento» porque el veto de ATR se evaluaba
  después de comprobar la completitud de datos.
- `atr_min_pct` por defecto (0,035%) superaba el ATR real de EURUSD a 1 minuto:
  vetaba todo. Recalibrado a 0,005%.
- El zip de HistData trae un `.txt` de huecos que rompía el parseo.
- `--sin-ia` con `cerebro_ejecutor="ia"` vetaba las 36 decisiones en silencio.
- El PnL mostraba un falso +9.000 (config 1.000 contra demo 10.000).
- El PnL atribuía al bot una operación manual del usuario.
- Un guion largo rompía la regla de salida ASCII; un `·` en los SVG, también.
- El contador de intentos se pintaba antes de incrementarse.

**Míos, de proceso:**

- Un test afirmaba que 56,25% sobre 800 operaciones era significativo. No lo es
  (p=0,36). **Arreglé el test, no el código**, y lo dejé como test de regresión
  porque es el caso más peligroso que existe.
- Tres de mis propios dobles de test estaban mal construidos. Los arreglé y lo
  dije.
- Diagnostiqué mal un feed parado: **estaba funcionando** a la cadencia
  diseñada. Me corregí explícitamente.
- Dije que el panel arrancaba cuando no arrancaba. El usuario lo señaló
  («Mentira, mira el error del panel») y tenía razón.
- Di un comando con `&&`, que no funciona en Windows PowerShell 5.1.
- Escribí `` `load_csv` `` con acentos graves dentro del bloque Python que
  vive en una plantilla de JavaScript. Eso cerró la plantilla y `app.js`
  dejó de parsearse **entero, sin ningún error en consola**: la app se quedó
  en blanco sin pista alguna. Ahora `test_el_bloque_python_no_rompe_la_plantilla`
  lo impide.

**Conocido y sin arreglar:**

- Un test de `tests/test_live.py` depende de temporización y puede fallar de
  forma intermitente.
- `activos_abiertos()` devuelve 0 contra una cuenta real de IQ Option; nunca se
  llegó a diagnosticar.
- La línea «Cobertura %» del importador asume 1.440 velas por día sea cual sea el
  timeframe, así que infravalora la cobertura de datos horarios.

---

## 13. Pendiente

- `git push` de todo lo pendiente.
- Desplegar la PWA en un hosting estático (GitHub Pages sirve y es gratis).
- Probarla instalada en un móvil real, no solo en el navegador de escritorio.
- Guardar el histórico de intentos entre sesiones, no solo las estrategias.
- Traducir esta bitácora al inglés si el repo apunta a público internacional.

---

## La conclusión, en una frase

**No se encontró ventaja explotable en ningún instrumento ni horizonte probado**,
y eso no es el fracaso del proyecto sino su resultado: lo que queda es un
instrumento de medición que se negó a decir lo que se quería oír, que es
exactamente lo que se le pedía.
