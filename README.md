***Español** · [English](README.en.md)*

# Kronos AI — AutoIQ

**Un backtester construido para no poder mentirte.**

Casi todos los backtests de trading dan resultados mejores que la realidad, y
casi siempre por las mismas cuatro razones: miran datos del futuro sin querer,
no modelan el coste de operar, se quedan con la mejor de cien pruebas sin
corregir por ello, y no comparan contra ningún control. Este repositorio
convierte esas cuatro trampas en tests automatizados que fallan si alguien las
reintroduce.

Python 3.11+ puro, **cero dependencias externas**, 359 tests.

```bash
git clone <repo> && cd kronos-ai-autoiq
python -m kronos selftest    # 359 tests, ~47 s, sin red
python -m kronos demo        # pipeline completo de extremo a extremo
```

## Qué pasó cuando se apuntó a una pregunta real

Se usó para buscar una estrategia rentable en mercados reales: ~300 hipótesis,
cinco mercados, cuatro horizontes temporales, hasta 25 años de datos.

| Mercado | Muestra | Hipótesis | Supervivientes |
|---|---:|---:|---:|
| EURUSD binarias 1 min | 744.403 velas | 72 | **0** |
| EURUSD binarias 1 h | 149.617 velas (25,6 años) | 90 | **0** |
| EURUSD-OTC de IQ Option | 20.000 velas | 72 | **0** |
| Cripto stop/objetivo 2:1 | 7 activos × 4 horizontes | ~60 | **0** |

Ninguna ventaja sobrevivió a los costes de operar. **Ese es el resultado
publicado**, y también la demostración de que el instrumento funciona: un
backtester que produce estrategias ganadoras a demanda no está midiendo, está
generando ruido bonito.

### El dato que resume el problema

La misma regla, sobre el mismo activo, según cuántos datos se miren:

| Muestra | Winrate | Edge |
|---|---:|---:|
| 12.468 velas (2 años) | 56,36% | **+2,01%** |
| 36.565 velas (6 años) | 53,48% | −0,87% |
| 149.617 velas (25 años) | 52,81% | −1,54% |

No cambió la estrategia ni el mercado. Cambió cuánto se miraba. Con dos años de
datos, esa regla parece un sistema ganador y justifica poner dinero; con
veinticinco, es una pérdida lenta.

**[→ Informe completo: metodología, resultados y cómo reproducirlo](INFORME.md)**

---

## Las cuatro trampas, y cómo se cierran

| Trampa | Qué hace Kronos | Test que lo fija |
|---|---|---|
| **Look-ahead**: el indicador usa datos que en ese momento no existían | El valor en `i` solo depende de datos `<= i`; la estrategia recibe únicamente `series[:i+1]` | `test_sin_look_ahead`, `test_la_estrategia_solo_ve_el_pasado` |
| **Coste ignorado**: evaluar como si operar fuese gratis | Spread modelado siempre, por defecto 0,5 pips y nunca cero; siempre en contra | `test_slippage_empeora_el_resultado` |
| **Dragado de datos**: quedarse con la mejor de cien pruebas | Corrección de Bonferroni sobre el número real de hipótesis + validación fuera de muestra obligatoria | `tests/test_research.py` |
| **Sin control**: un winrate sin nada con que compararlo | Catálogo con *siempre CALL*, *siempre PUT* y *moneda al aire* | `tests/test_research.py` |

Un quinto, propio del backtest con stops: dentro de una vela OHLC no se sabe si
el máximo llegó antes que el mínimo. Si en la misma vela caben stop y objetivo,
Kronos cuenta **STOP**. Asumir lo contrario infla los resultados y es la causa
número uno de sistemas que lucen bien en el histórico y no se reproducen.

**Omisión deliberada:** no hay optimizador de parámetros. Una rejilla sobre 15
umbrales encuentra siempre una combinación que brilla en el histórico y casi
nunca fuera de él. Añadirlo habría hecho el proyecto más vendible y menos útil.

La demo lo enseña en diez segundos. Corre el pipeline entero sobre una serie
sintética y termina así:

```
  VEREDICTO: NO DESPLEGAR - ESPERANZA NEGATIVA
```

Es el resultado correcto: esa serie es un paseo aleatorio sin estructura
explotable, y un backtester honesto tiene que decirlo. Un simulador que sacara
beneficio de ahí estaría mintiendo.

### La aritmética que decide todo

Un bróker que paga un 80% de beneficio te obliga a acertar:

```
umbral de equilibrio = 1 / (1 + payout) = 1 / 1.8 = 55.6%
```

Es decir: **acertar el 54% de las veces pierde dinero de forma sostenida.**
No por mala suerte — por diseño del instrumento. La mayoría de sistemas que
"aciertan más de la mitad" están, en realidad, en pérdida estructural.

Por eso ninguna salida de Kronos muestra el winrate a secas. Siempre aparece
junto al umbral, el *edge* (winrate − umbral) y un contraste estadístico que
responde a la única pregunta que importa: **¿esto es ventaja o es ruido?**

---

## Panel en tiempo real y cerebro IA

```bash
pip install -r requirements-dashboard.txt
setx ANTHROPIC_API_KEY "sk-ant-..."
streamlit run dashboard/app.py
```

El motor corre en un hilo propio y cada ciclo pregunta a **dos cerebros** sobre
los mismos datos: la API de Anthropic (`claude-opus-5`) y el motor de reglas
local. Solo uno ejecuta las órdenes — configurable — pero se registran ambos.

**La tasa de acuerdo es la métrica que justifica el gasto.** Si la IA coincide
casi siempre con las reglas, estás pagando por replicar algo gratuito. Si difiere
mucho, hay que ver cuál de las dos acierta antes de fiarse de ninguna.

Sin panel, para dejarlo en un servidor:

```bash
python -m kronos.live --data data/eurusd.csv --velocidad 60
```

### El coste manda en el diseño

Un ciclo de 5 segundos son 17.280 llamadas al día. Con Opus 5, unos **$86/día**.
Y en velas de 1–5 minutos, once de cada doce analizan la misma vela y devuelven
la misma respuesta:

| Cadencia de consulta | Llamadas/día | Coste/día |
|---|---:|---:|
| Cada 5 s | 17.280 | ~$86 |
| Al cerrar vela de 1 min | 1.440 | ~$7 |
| Al cerrar vela de 5 min | 288 | ~$1,4 |

Por eso `solo_en_cierre_de_vela` viene activado: el bucle sigue latiendo cada
5 s (datos, liquidación de posiciones, panel), pero la API solo se consulta
cuando hay información nueva. Se puede desactivar con un clic; el panel avisa.

Dos optimizaciones más van de serie: el prompt de sistema (847 tokens) se cachea
con `cache_control`, así que a partir de la segunda llamada cuesta el 10%; y la
salida se fuerza con `output_config.format` y un esquema JSON, de modo que el
contrato lo garantiza la API en vez de pedirlo por prompt.

### Fallo cerrado

Cualquier error del cerebro IA — timeout, cuota, red, respuesta fuera de
contrato — devuelve `ESPERAR`, nunca una excepción que tumbe el bot ni una
decisión inventada. Está cubierto por `test_un_fallo_de_la_ia_no_opera_ni_rompe`.

Todas las órdenes van contra el broker simulado. No se mueve dinero real.

---

## Por qué reglas y no un LLM

Este repositorio nació de la idea de pedirle a un modelo de lenguaje una
decisión `CALL`/`PUT` en cada vela. Se implementó como código porque:

| | LLM por vela | Motor de reglas |
|---|---|---|
| Mismo input → mismo output | no garantizado | garantizado |
| Backtesteable sobre 100k velas | inviable (coste, latencia) | segundos |
| Auditable ("¿por qué esta orden?") | explicación *a posteriori* | los votos que la produjeron |
| Latencia por decisión | segundos | microsegundos |
| Coste por decisión | por token | cero |

Un LLM sigue siendo útil **alrededor** del bot: analizar el calendario
macroeconómico, resumir informes de sesión, revisar el código. No dentro del
lazo de decisión, donde la reproducibilidad no es negociable.

---

## Instalación

Ninguna. Requiere solo Python 3.11 o superior.

```bash
git clone <tu-repo> kronos-ai-autoiq
cd kronos-ai-autoiq
python -m kronos selftest
```

---

## Uso

### Decisión sobre la última vela

Devuelve exactamente el contrato JSON que consume un script ejecutor, y nada
más en stdout (los avisos van a stderr):

```bash
python -m kronos decide --data velas.csv
```

```json
{
  "decision": "CALL",
  "confianza": "MEDIA",
  "razon": "TENDENCIA: 3 confluencias CALL (EMA+DI, MACD, Estocastico); RSI 54.2, %B 0.38, ADX 28.1."
}
```

Con `--full` añade régimen, score, votos individuales y el valor de cada
indicador, para depurar por qué salió esa decisión.

Desde otro proceso:

```bash
cat velas.json | python -m kronos decide --stdin --symbol EURUSD
```

### Backtest con veredicto

```bash
python -m kronos backtest --data velas.csv --payout 0.80 --expiry 5
```

```
  RESULTADO
  Ganadas / Perdidas / Empates       450 / 350 / 0
  Winrate (sobre decisivas)          56.25%
  Umbral equilibrio (payout 80%)     55.56%
  EDGE (winrate - umbral)            0.69%  [+]
  IC 95% del winrate                 [52.77%, 59.67%]
  p-valor (una cola vs umbral)       0.3600

========================================================================
  VEREDICTO: NO CONCLUYENTE
========================================================================
  El edge de 0.69% es positivo pero p=0.360 (>= 0.05): con 800
  operaciones no se distingue del azar.
========================================================================
```

Ese ejemplo es real y es el caso más peligroso que existe: 56.25% de aciertos
sobre 800 operaciones **parece** un sistema ganador y no lo es. Está fijado como
test de regresión en `tests/test_backtest.py`.

Código de salida: `0` si hay edge significativo, `1` si no — encadenable en CI.

### Validación fuera de muestra

La prueba que de verdad separa un sistema de un ajuste a la curva:

```bash
python -m kronos validar --data velas.csv --split 0.6
```

Entrena el juicio en el primer 60% y comprueba si el edge sobrevive en el 40%
que nunca se miró. Si desaparece, lo dice: `SOBREAJUSTE: no desplegar`.

### Exploración de hipótesis

```bash
python -m kronos explorar --data data/eurusd.csv --spread 0.5 --sensibilidad "RSI extremo"
```

Barre decenas de hipótesis a la vez sobre arrays (segundos, no minutos), con
tres salvaguardas contra el sobreajuste que un barrido casero no tiene:

- **Corrección de Bonferroni** sobre el número real de pruebas. Con 72 hipótesis
  al 5% esperas ~3,6 falsos positivos aunque ninguna sirva.
- **Dentro y fuera de muestra** desde el principio. Solo llama superviviente a
  lo que pasa ambos filtros.
- **Spread de 0,5 pips por defecto, no cero.** Es la salvaguarda que más
  importa: en horizontes de 1–10 minutos el movimiento predecible del precio es
  del orden del spread, así que evaluar sin él fabrica ganadores fantasma.

El catálogo incluye tres **controles** (siempre CALL, siempre PUT, moneda al
aire). Sin ellos no se puede interpretar el resultado: si "siempre CALL" saca
edge, estás midiendo la deriva del periodo, no capacidad predictiva.

### Cripto con stop y objetivo

El otro modo de backtest, y el que cambia la aritmética. En binarias el payoff lo
fija el bróker; con stop y objetivo lo eliges tú:

```bash
python -m kronos cripto --symbol ETHUSDT --timeframe 86400 --velas 3000
```

Los datos se descargan solos de la API pública de Binance (sin credenciales).
`--timeframe` admite desde `60` (1 min) hasta `604800` (semanal).

**La convención pesimista es lo que hace creíble este modo.** Dentro de una vela
OHLC no sabes si el máximo llegó antes que el mínimo. Si en la misma vela se
tocan stop y objetivo, Kronos cuenta **STOP**. Asumir lo contrario es la causa
número uno de backtests de stops que lucen bien y no se reproducen en real.

Y el umbral que hay que batir no es `1/(1+rr)` a secas: con comisión es
`(1 + coste_R) / 3` para un 2:1. Con una comisión que valga el 7% del riesgo, el
listón sube de 33,3% a 35,7%. Kronos reporta el coste en unidades de R
precisamente para que ese ajuste sea visible.

### Resto de comandos

```bash
python -m kronos paper       --data velas.csv   # replay contra bróker simulado
python -m kronos descargar   --symbol BTCUSDT   # histórico de Binance a CSV
python -m kronos duelo       --data velas.csv   # reglas vs IA sobre precios reales
python -m kronos broker      --symbol EURUSD    # diagnóstico de conexión IQ Option
python -m kronos indicadores --data velas.csv   # volcado de indicadores
python -m kronos datos       --out velas.csv    # genera serie sintética
python -m kronos config-init                    # escribe config/default.json
python -m kronos selftest                       # 359 tests
```

### De dónde sacar histórico real

Recomendado: **[HistData.com](https://www.histdata.com/download-free-forex-historical-data/)**
— gratis, sin registro, velas de 1 minuto por par/año/mes desde 2000. Descarga
el formato *Generic ASCII M1* de EURUSD y pásalo por el importador:

```bash
python -m kronos importar ~/Descargas/DAT_ASCII_EURUSD_M1_*.zip --out data/eurusd.csv
```

```
  60480 velas -> data/eurusd.csv
  Rango (UTC)          2023-01-02 05:00 .. 2023-03-01 04:59  (57 dias)
  Cobertura            73.7% del calendario natural
  Huecos > 5 velas     8  (los fines de semana cuentan aqui, es normal)

  Operaciones estimadas con la estrategia por defecto: ~241
  Insuficiente para concluir. Descarga ~39520 velas mas
  (unos 38 dias naturales) antes de sacar conclusiones.
```

Acepta zips, CSVs o una carpeta entera, y los fusiona ordenados y sin duplicados.
Para años ya cerrados HistData ofrece *Full Year Data*, así que lo normal son uno
o dos zips; solo el año en curso va por meses sueltos.

Asegúrate de coger **1 Minute Bar Quotes**, no *Tick Data*: esta última son
cientos de MB por mes, lleva bid/ask y milisegundos, y el importador no la lee.

Dos detalles del formato que arruinan un backtest si se pasan por alto, y que el
importador ya resuelve:

- **Las marcas temporales están en EST sin horario de verano** (UTC-5 todo el
  año). Cargarlas como UTC desplaza los límites de día del gestor de riesgo
  cinco horas y parte las sesiones por la mitad.
- **La columna de volumen siempre vale 0.** No es un fallo de descarga: en un
  mercado descentralizado como el forex no existe volumen agregado real.

**Alternativa de más calidad:** [Dukascopy](https://www.dukascopy.com/swiss/english/marketwatch/historical/)
publica datos a nivel de tick con bid y ask reales. Mejor fidelidad, pero
descargarlos requiere una herramienta externa (`duka` en Python, o JForex). Vale
la pena cuando quieras modelar el spread; para empezar, HistData sobra.

**Caveat que aplica a cualquier fuente:** estos son precios de *forex al
contado*, y las binarias de tu bróker se liquidan contra su propio feed, que no
es idéntico — sobre todo en el precio exacto de vencimiento. El backtest es
orientativo, no una reproducción de lo que el bróker habría liquidado.

### Rendimiento

Unas 800 velas evaluadas por segundo (indicadores recalculados sobre una ventana
rodante de 150). Un año de EURUSD en 1 minuto (~370.000 velas) tarda unos 8
minutos; dos meses, unos 75 segundos. Es trabajo por lotes, no interactivo.

### Formato de datos

CSV con cabecera; el orden de columnas da igual y se aceptan alias habituales
(`time`/`date`/`open_time`, `o`/`h`/`l`/`c`, `vol`…), de modo que los exportados
de MetaTrader, TradingView o IQ Option entran sin editar.

```csv
timestamp,open,high,low,close,volume
1700000000,1.09321,1.09355,1.09310,1.09348,142
```

`timestamp` admite epoch en segundos, milisegundos, microsegundos o ISO-8601.

---

## Arquitectura

```
kronos/
├── core/         indicadores y estructuras de mercado
│   ├── candle.py       Candle (inmutable, autovalidada) y Series
│   └── indicators.py   SMA, EMA, RSI, Bollinger, ATR, ADX, MACD, Estocástico
├── strategy/     reglas de decisión → Signal
│   ├── base.py         Decision, Confidence, Vote, Signal, Strategy
│   ├── confluence.py   motor de confluencia con filtros de régimen
│   └── registry.py     selección de estrategia por nombre
├── risk/         límites y sizing, con derecho de veto
├── backtest/     simulación honesta + métricas con contraste estadístico
├── data/         carga CSV/JSON y generador sintético reproducible
└── broker/       ejecución: papel (por defecto) e IQ Option (opcional)
```

Cuatro responsabilidades separadas a propósito. La estrategia **no sabe** cuánto
dinero hay; el riesgo **no sabe** por qué se generó la señal; el backtest **no
sabe** cómo se calcula un RSI. Cada capa se puede sustituir sin tocar las demás.

### Cómo decide el motor de confluencia

El orden es deliberado: primero los **vetos**, después el conteo. Un veto nunca
se compensa con votos.

```
1. ¿Hay datos suficientes?                    no → ESPERAR
2. ¿ATR% dentro de rango?          muerto o caótico → ESPERAR
3. ¿Vela anómala (>3× ATR)?           spike/noticia → ESPERAR
4. ¿ADX bajo + Bollinger comprimido?  lateral estrecho → ESPERAR
5. Clasificar régimen:  ADX ≥ 25 → TENDENCIA │ resto → REVERSIÓN
6. Contar votos del régimen correspondiente
7. ¿Votos en ambos sentidos?              conflicto → ESPERAR
8. ¿Menos de `min_votos` alineados?               → ESPERAR
9. ¿Reversión contra direccional dominante?       → ESPERAR
10. Emitir CALL/PUT; confianza según el nº de confluencias
```

**En tendencia** se opera a favor, buscando el final de un retroceso (cruce del
RSI por 50, %B recuperando, MACD expandiendo, cruce estocástico).
**En reversión** se busca agotamiento en los extremos del canal (RSI en
sobreventa/sobrecompra, precio en banda, cruce estocástico en zona extrema).

Nunca los dos criterios a la vez: comprar reversión en mitad de una tendencia
fuerte es la forma más rápida de perder dinero, y el paso 9 lo bloquea
explícitamente.

### Gestión de riesgo

El gestor tiene **derecho de veto** sobre la estrategia. Que rechace una señal
válida no es un error, es su trabajo:

- Sizing plano o fracción fija del balance.
- Límite de pérdida diaria **y de ganancia diaria** (parar en verde también).
- Máximo de operaciones por día.
- Enfriamiento obligatorio tras N pérdidas seguidas.
- *Kill switch* permanente por balance mínimo — no lo levanta ni el cambio de día.
- Filtro por confianza mínima.
- Una posición simultánea (configurable).

**No hay martingala ni ninguna progresión tras pérdida, y no es un olvido.**
En un instrumento de esperanza negativa, doblar la apuesta no mejora la
esperanza: solo concentra toda la ruina en una única racha. El test
`test_sin_martingala_tras_perder` impide que se cuele en el futuro.

---

## Honestidad del simulador

Un backtest que se engaña a sí mismo es peor que no tener backtest: da confianza
sin fundamento. Estas garantías están cubiertas por tests, no solo prometidas:

| Garantía | Test |
|---|---|
| Los indicadores nunca usan datos posteriores al índice `i` | `test_sin_look_ahead` |
| La estrategia solo recibe `series[:i+1]` | `test_la_estrategia_solo_ve_el_pasado` |
| La misma ventana da la misma señal, haya o no velas después | `test_no_depende_de_datos_futuros` |
| La entrada se ejecuta al cierre de la vela que dio la señal | `test_call_en_serie_creciente_gana_siempre` |
| Ninguna operación vence fuera de los datos disponibles | `test_ninguna_operacion_vence_fuera_de_los_datos` |
| El empate devuelve el stake íntegro | `test_empate_devuelve_el_stake` |
| El deslizamiento siempre juega en contra | `test_slippage_empeora_el_resultado` |
| La contabilidad cuadra al céntimo | `test_curva_de_capital_coherente` |

```bash
python -m kronos selftest        # 359 tests, ~47 s, sin red
```

Como no hay optimizador de parámetros y no va a haberlo, si ajustas umbrales
hazlo a mano y valida cada cambio con `kronos validar`. Un cambio que mejora el
backtest y empeora el tramo fuera de muestra es sobreajuste, no una mejora.

---

## Ejecución real

`PaperBroker` es el bróker por defecto y está completo: el bot corre de extremo
a extremo sin tocar dinero.

`kronos/broker/iqoption.py` es un adaptador **estructural y no verificado**
contra el endpoint real. Depende de `iqoptionapi`, un cliente comunitario de
ingeniería inversa que no es dependencia del proyecto ni se ha podido ejecutar
contra un servidor. Trátalo como punto de partida de integración, no como código
probado.

Salvaguardas, en orden:

1. La cuenta por defecto es **DEMO**. Hay que pedir REAL explícitamente.
2. Operar en REAL exige **además** `KRONOS_ALLOW_REAL=1` en el entorno. Dos
   gestos independientes, para que ninguna operación real salga de un descuido.
3. Las credenciales se leen **solo** de `IQ_EMAIL` / `IQ_PASSWORD`. Nunca por
   CLI ni por fichero, para que no acaben en el historial del shell ni en git.

---

## Camino recomendado

1. `python -m kronos demo` — comprueba que el pipeline funciona.
2. Consigue histórico **real** del activo y timeframe que vas a operar.
3. `kronos backtest` — si el veredicto es `NO DESPLEGAR`, para aquí. Ajustar
   parámetros hasta que el número guste es exactamente cómo se construye un
   sistema que solo funciona en el pasado.
4. `kronos validar` — si el edge no sobrevive fuera de muestra, no existe.
5. Cuenta **demo real** varias semanas. El backtest no modela latencia, huecos
   de fin de semana, requotes ni payouts variables.
6. Solo entonces, y con dinero que puedas perder entero.

La mayoría de configuraciones no pasan del paso 3. Que el sistema te lo diga
pronto y con números es el objetivo del proyecto, no su fracaso.

Las que se probaron aquí no pasaron ninguna: ver [INFORME.md](INFORME.md).

---

## Aviso

Software educativo y de investigación. Las opciones binarias son instrumentos de
alto riesgo con esperanza negativa por diseño, y están restringidas o prohibidas
para minoristas en varias jurisdicciones (entre ellas la UE, vía ESMA).
Comprueba tu marco legal. Nada en este repositorio es asesoramiento financiero.

## Licencia

MIT.
