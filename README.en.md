*[Español](README.md) · **English***

# Kronos AI — AutoIQ

**A backtester built so it can't lie to you.**

Almost every trading backtest reports better results than reality, and almost
always for the same four reasons: it accidentally peeks at future data, it
doesn't model the cost of trading, it keeps the best of a hundred trials without
correcting for that, and it compares against nothing. This repository turns
those four failure modes into automated tests that break if anyone reintroduces
them.

Pure Python 3.11+, **zero external dependencies**, 409 tests.

```bash
git clone <repo> && cd kronos-ai-autoiq
python -m kronos selftest    # 409 tests, ~57 s, no network
python -m kronos demo        # full end-to-end pipeline
```

> **A note on language.** The codebase, CLI and console output are in Spanish;
> this document is the English translation of [README.md](README.md). Command
> names are shown as you actually type them (`backtest`, `validar`, `explorar`,
> `cripto`, `importar`).

## What happened when it was pointed at a real question

It was used to look for a profitable strategy in real markets: ~300 hypotheses,
five markets, four time horizons, up to 25 years of data.

| Market | Sample | Hypotheses | Survivors |
|---|---:|---:|---:|
| EURUSD binary options, 1 min | 744,403 candles | 72 | **0** |
| EURUSD binary options, 1 h | 149,617 candles (25.6 years) | 90 | **0** |
| EURUSD-OTC (IQ Option) | 20,000 candles | 72 | **0** |
| Crypto, stop/target 2:1 | 7 assets × 4 horizons | ~60 | **0** |

No edge survived trading costs. **That is the published result**, and also the
demonstration that the instrument works: a backtester that produces winning
strategies on demand isn't measuring anything, it's generating attractive noise.

### The number that sums up the problem

The same rule, on the same asset, depending only on how much data you look at:

| Sample | Win rate | Edge |
|---|---:|---:|
| 12,468 candles (2 years) | 56.36% | **+2.01%** |
| 36,565 candles (6 years) | 53.48% | −0.87% |
| 149,617 candles (25 years) | 52.81% | −1.54% |

The strategy didn't change and neither did the market. Only the sample size did.
With two years of data that rule looks like a winning system and justifies
putting money behind it; with twenty-five, it's a slow bleed.

**[→ Full report: methodology, results and how to reproduce them](REPORT.en.md)**

---

## The four traps, and how each is closed

| Trap | What Kronos does | Test that pins it |
|---|---|---|
| **Look-ahead**: the indicator uses data that didn't exist yet | The value at `i` depends only on data `<= i`; the strategy receives only `series[:i+1]` | `test_sin_look_ahead`, `test_la_estrategia_solo_ve_el_pasado` |
| **Ignored cost**: evaluating as if trading were free | Spread always modelled, 0.5 pips by default and never zero; always adverse | `test_slippage_empeora_el_resultado` |
| **Data dredging**: keeping the best of a hundred trials | Bonferroni correction over the real number of hypotheses + mandatory out-of-sample validation | `tests/test_research.py` |
| **No control**: a win rate with nothing to compare it to | Catalogue includes *always CALL*, *always PUT* and *coin flip* | `tests/test_research.py` |

A fifth one, specific to stop/target backtests: inside an OHLC candle you cannot
know whether the high came before the low. If stop and target both fall inside
the same candle, Kronos counts a **STOP**. Assuming the opposite inflates
results and is the number one reason systems look good on history and don't
reproduce live.

**Deliberate omission:** there is no parameter optimiser. A grid over 15
thresholds will always find a combination that shines on history and almost
never outside it. Adding one would have made the project more marketable and
less useful.

The demo shows the principle in ten seconds. It runs the whole pipeline over a
synthetic series and ends like this:

```
  VEREDICTO: NO DESPLEGAR - ESPERANZA NEGATIVA
```

("Verdict: do not deploy — negative expectancy.") That's the correct answer:
the series is a random walk with no exploitable structure, and an honest
backtester has to say so. A simulator that extracted profit from it would be
lying.

### The arithmetic that decides everything

A broker paying an 80% return forces you to be right:

```
breakeven win rate = 1 / (1 + payout) = 1 / 1.8 = 55.6%
```

In other words: **winning 54% of the time loses money consistently.** Not
through bad luck — by design of the instrument. Most systems that "win more
than half" are, in fact, structurally unprofitable.

That's why no Kronos output ever shows a win rate on its own. It always appears
next to the breakeven threshold, the *edge* (win rate − threshold) and a
statistical test answering the only question that matters: **is this an edge or
is it noise?**

---

## Kronos Studio: define a strategy without writing code

```bash
pip install -r requirements-dashboard.txt
streamlit run dashboard/estudio.py
```

A test bench where you build a strategy out of rules — `IF rsi < 35 AND adx < 25
-> CALL` — run it against real data, and get a verdict that applies all five
filters. Strategies are data, not code: export to JSON, share, load back.

The eleven available channels come from `python -c "from kronos.research.reglas
import catalogo; print(catalogo())"`. Each is compared with `<`, `>`, `<=`, `>=`,
`cruza_arriba` or `cruza_abajo`, and several conditions inside one rule must hold
simultaneously.

### The attempt counter

This is the design decision that separates it from any other backtester with a
UI. A sweep corrects for the hypotheses it tries at once; a UI has the opposite
and worse problem:

> You define a strategy, don't like the result, move a threshold and try again.
> Forty times.

Statistically those are forty hypotheses against the same data, and the best of
the forty looks great by pure chance. Trying them one at a time doesn't change
that — it only hides it.

**Kronos Studio counts and applies it.** The required p-value is multiplied by
the number of evaluations you've run against that dataset, the counter is on
screen, and the full session history is shown. Testing a lot is legitimate;
doing it for free is not.

The counter can be reset, but that only makes sense with data you haven't looked
at before — which is precisely the point.

---

## Live dashboard and AI brain

```bash
pip install -r requirements-dashboard.txt
setx ANTHROPIC_API_KEY "sk-ant-..."
streamlit run dashboard/app.py
```

The engine runs on its own thread, and every cycle it asks **two brains** about
the same data: the Anthropic API (`claude-opus-5`) and the local rules engine.
Only one of them places orders — configurable — but both are logged.

**The agreement rate is the metric that justifies the spend.** If the AI almost
always matches the rules, you're paying to replicate something free. If it
diverges a lot, you need to find out which of the two is right before trusting
either.

Headless, to leave it running on a server:

```bash
python -m kronos.live --data data/eurusd.csv --velocidad 60
```

### Cost drives the design

A 5-second cycle is 17,280 API calls a day. With Opus 5, about **$86/day**. And
on 1–5 minute candles, eleven out of twelve of those analyse the same candle and
return the same answer:

| Query cadence | Calls/day | Cost/day |
|---|---:|---:|
| Every 5 s | 17,280 | ~$86 |
| On 1-min candle close | 1,440 | ~$7 |
| On 5-min candle close | 288 | ~$1.4 |

That's why `solo_en_cierre_de_vela` ships enabled: the loop keeps beating every
5 s (data, position settlement, dashboard) but the API is only consulted when
there is new information. It can be turned off with one click; the dashboard
warns you.

Two more optimisations are built in: the system prompt (847 tokens) is cached
with `cache_control`, so from the second call onwards it costs 10%; and the
output is constrained with `output_config.format` and a JSON schema, so the
contract is guaranteed by the API instead of requested in the prompt.

### Fail closed

Any error in the AI brain — timeout, quota, network, off-contract response —
returns `ESPERAR` (wait), never an exception that takes the bot down and never
an invented decision. Covered by `test_un_fallo_de_la_ia_no_opera_ni_rompe`.

All orders go to the simulated broker. No real money moves.

---

## Why rules and not an LLM

This repository started from the idea of asking a language model for a
`CALL`/`PUT` decision on every candle. It was implemented as code because:

| | LLM per candle | Rules engine |
|---|---|---|
| Same input → same output | not guaranteed | guaranteed |
| Backtestable over 100k candles | infeasible (cost, latency) | seconds |
| Auditable ("why this order?") | *post hoc* explanation | the votes that produced it |
| Latency per decision | seconds | microseconds |
| Cost per decision | per token | zero |

An LLM is still useful **around** the bot: analysing the macro calendar,
summarising session reports, reviewing code. Not inside the decision loop, where
reproducibility isn't negotiable.

---

## Installation

None. Python 3.11 or newer is the only requirement.

```bash
git clone <your-repo> kronos-ai-autoiq
cd kronos-ai-autoiq
python -m kronos selftest
```

---

## Usage

### Decision on the latest candle

Returns exactly the JSON contract an executor script consumes, and nothing else
on stdout (warnings go to stderr):

```bash
python -m kronos decide --data candles.csv
```

```json
{
  "decision": "CALL",
  "confianza": "MEDIA",
  "razon": "TENDENCIA: 3 confluencias CALL (EMA+DI, MACD, Estocastico); RSI 54.2, %B 0.38, ADX 28.1."
}
```

`--full` adds regime, score, individual votes and every indicator value, to
debug why that decision came out.

From another process:

```bash
cat candles.json | python -m kronos decide --stdin --symbol EURUSD
```

### Backtest with a verdict

```bash
python -m kronos backtest --data candles.csv --payout 0.80 --expiry 5
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

That example is real, and it's the most dangerous case there is: 56.25% accuracy
over 800 trades **looks like** a winning system and isn't. It's pinned as a
regression test in `tests/test_backtest.py`.

Exit code: `0` if there is a significant edge, `1` otherwise — chainable in CI.

### Out-of-sample validation

The test that actually separates a system from a curve fit:

```bash
python -m kronos validar --data candles.csv --split 0.6
```

Trains judgement on the first 60% and checks whether the edge survives in the
40% that was never looked at. If it vanishes, it says so: `SOBREAJUSTE: no
desplegar` (overfitting: do not deploy).

### Hypothesis exploration

```bash
python -m kronos explorar --data data/eurusd.csv --spread 0.5 --sensibilidad "RSI extremo"
```

Sweeps dozens of hypotheses at once over arrays (seconds, not minutes), with
three anti-overfitting safeguards a homemade sweep doesn't have:

- **Bonferroni correction** over the real number of trials. With 72 hypotheses
  at 5% you expect ~3.6 false positives even if none of them works.
- **In-sample and out-of-sample** from the start. Only what passes both filters
  is called a survivor.
- **0.5 pips of spread by default, not zero.** This is the safeguard that
  matters most: over 1–10 minute horizons the predictable price movement is on
  the order of the spread, so evaluating without it manufactures phantom
  winners.

The catalogue includes three **controls** (always CALL, always PUT, coin flip).
Without them the result can't be interpreted: if "always CALL" shows an edge,
you're measuring the drift of the period, not predictive power.

### Crypto with stop and target

The other backtest mode, and the one that changes the arithmetic. With binary
options the broker fixes the payoff; with stop and target you choose it:

```bash
python -m kronos cripto --symbol ETHUSDT --timeframe 86400 --velas 3000
```

Data downloads itself from Binance's public API (no credentials). `--timeframe`
accepts anything from `60` (1 min) to `604800` (weekly).

**The pessimistic convention is what makes this mode credible.** Inside an OHLC
candle you don't know whether the high came before the low. If stop and target
are both touched in the same candle, Kronos counts a **STOP**.

And the threshold to beat isn't plain `1/(1+rr)`: with commission it's
`(1 + cost_R) / 3` for a 2:1. With a commission worth 7% of risk, the bar rises
from 33.3% to 35.7%. Kronos reports cost in units of R precisely so that
adjustment stays visible.

### Other commands

```bash
python -m kronos paper       --data candles.csv  # replay against simulated broker
python -m kronos descargar   --symbol BTCUSDT    # Binance history to CSV
python -m kronos duelo       --data candles.csv  # rules vs AI on real prices
python -m kronos broker      --symbol EURUSD     # IQ Option connection diagnostics
python -m kronos indicadores --data candles.csv  # indicator dump
python -m kronos datos       --out candles.csv   # generate synthetic series
python -m kronos config-init                     # write config/default.json
python -m kronos selftest                        # 409 tests
```

### Where to get real history

Recommended: **[HistData.com](https://www.histdata.com/download-free-forex-historical-data/)**
— free, no registration, 1-minute candles per pair/year/month since 2000. Grab
the *Generic ASCII M1* format for EURUSD and run it through the importer:

```bash
python -m kronos importar ~/Downloads/DAT_ASCII_EURUSD_M1_*.zip --out data/eurusd.csv
```

It accepts zips, CSVs or a whole folder, and merges them sorted and
deduplicated. For closed years HistData offers *Full Year Data*, so one or two
zips is normal; only the current year comes as separate months.

Make sure you take **1 Minute Bar Quotes**, not *Tick Data*: the latter is
hundreds of MB per month, carries bid/ask and milliseconds, and the importer
doesn't read it.

Two format details that ruin a backtest if missed, and which the importer
already handles:

- **Timestamps are in EST without daylight saving** (UTC-5 all year). Loading
  them as UTC shifts the risk manager's day boundaries by five hours and cuts
  sessions in half.
- **The volume column is always 0.** Not a download failure: in a decentralised
  market like forex there is no real aggregate volume.

**Higher-quality alternative:** [Dukascopy](https://www.dukascopy.com/swiss/english/marketwatch/historical/)
publishes tick-level data with real bid and ask. Better fidelity, but
downloading it needs an external tool (`duka` in Python, or JForex). Worth it
when you want to model the spread; to get started, HistData is plenty.

**Caveat that applies to any source:** these are *spot forex* prices, and your
broker's binaries settle against its own feed, which is not identical —
especially at the exact expiry price. The backtest is indicative, not a
reproduction of what the broker would have settled.

### Performance

Around 800 candles evaluated per second (indicators recomputed over a rolling
window of 150). One year of 1-minute EURUSD (~370,000 candles) takes about 8
minutes; two months, about 75 seconds. It's batch work, not interactive.

### Data format

CSV with a header; column order doesn't matter and common aliases are accepted
(`time`/`date`/`open_time`, `o`/`h`/`l`/`c`, `vol`…), so exports from MetaTrader,
TradingView or IQ Option load without editing.

```csv
timestamp,open,high,low,close,volume
1700000000,1.09321,1.09355,1.09310,1.09348,142
```

`timestamp` accepts epoch in seconds, milliseconds, microseconds, or ISO-8601.

---

## Architecture

```
kronos/
├── core/         indicators and market structures
│   ├── candle.py       Candle (immutable, self-validating) and Series
│   └── indicators.py   SMA, EMA, RSI, Bollinger, ATR, ADX, MACD, Stochastic
├── strategy/     decision rules → Signal
│   ├── base.py         Decision, Confidence, Vote, Signal, Strategy
│   ├── confluence.py   confluence engine with regime filters
│   └── registry.py     strategy selection by name
├── risk/         limits and sizing, with veto power
├── backtest/     honest simulation + metrics with statistical testing
├── data/         CSV/JSON loading and reproducible synthetic generator
└── broker/       execution: paper (default) and IQ Option (optional)
```

Four responsibilities separated on purpose. The strategy **doesn't know** how
much money there is; risk **doesn't know** why the signal was generated; the
backtest **doesn't know** how an RSI is computed. Each layer can be replaced
without touching the others.

### How the confluence engine decides

The order is deliberate: **vetoes first**, counting second. A veto is never
offset by votes.

```
1. Enough data?                             no → WAIT
2. ATR% within range?          dead or chaotic → WAIT
3. Anomalous candle (>3× ATR)?   spike/news    → WAIT
4. Low ADX + squeezed Bollinger? tight range   → WAIT
5. Classify regime:  ADX >= 25 → TREND │ else → REVERSION
6. Count votes for the corresponding regime
7. Votes in both directions?         conflict → WAIT
8. Fewer than `min_votos` aligned?            → WAIT
9. Reversion against dominant direction?      → WAIT
10. Emit CALL/PUT; confidence from the number of confluences
```

**In a trend** it trades with it, looking for the end of a pullback (RSI
crossing 50, %B recovering, MACD expanding, stochastic cross). **In reversion**
it looks for exhaustion at the channel extremes (RSI oversold/overbought, price
at the band, stochastic cross in the extreme zone).

Never both criteria at once: buying reversion in the middle of a strong trend is
the fastest way to lose money, and step 9 blocks it explicitly.

### Risk management

The manager has **veto power** over the strategy. Rejecting a valid signal isn't
a bug, it's the job:

- Flat sizing or fixed fraction of balance.
- Daily loss limit **and daily profit limit** (stopping while ahead, too).
- Maximum trades per day.
- Mandatory cooldown after N consecutive losses.
- Permanent *kill switch* on minimum balance — not even a new day lifts it.
- Minimum-confidence filter.
- One simultaneous position (configurable).

**There is no martingale and no post-loss stake progression, and that is not an
oversight.** On a negative-expectancy instrument, doubling down doesn't improve
expectancy: it just concentrates all the ruin into a single streak. The test
`test_sin_martingala_tras_perder` stops it from creeping back in.

---

## Simulator honesty

A backtest that fools itself is worse than no backtest: it gives confidence
without grounds. These guarantees are covered by tests, not merely promised:

| Guarantee | Test |
|---|---|
| Indicators never use data past index `i` | `test_sin_look_ahead` |
| The strategy only receives `series[:i+1]` | `test_la_estrategia_solo_ve_el_pasado` |
| The same window yields the same signal, with or without later candles | `test_no_depende_de_datos_futuros` |
| Entry executes at the close of the candle that produced the signal | `test_call_en_serie_creciente_gana_siempre` |
| No trade expires outside the available data | `test_ninguna_operacion_vence_fuera_de_los_datos` |
| A tie returns the full stake | `test_empate_devuelve_el_stake` |
| Slippage always plays against you | `test_slippage_empeora_el_resultado` |
| The books balance to the cent | `test_curva_de_capital_coherente` |

```bash
python -m kronos selftest        # 409 tests, ~57 s, no network
```

Since there is no parameter optimiser and there won't be one, if you tune
thresholds do it by hand and validate every change with `kronos validar`. A
change that improves the backtest and worsens the out-of-sample stretch is
overfitting, not an improvement.

---

## Live execution

`PaperBroker` is the default broker and it is complete: the bot runs end to end
without touching money.

`kronos/broker/iqoption.py` is a **structural, unverified** adapter against the
real endpoint. It depends on `iqoptionapi`, a reverse-engineered community
client that is not a project dependency and could not be exercised against a
server. Treat it as an integration starting point, not as tested code.

Safeguards, in order:

1. The default account is **DEMO**. REAL has to be requested explicitly.
2. Trading REAL **additionally** requires `KRONOS_ALLOW_REAL=1` in the
   environment. Two independent gestures, so no real trade comes out of a slip.
3. Credentials are read **only** from `IQ_EMAIL` / `IQ_PASSWORD`. Never via CLI
   or file, so they don't end up in shell history or in git.

---

## Recommended path

1. `python -m kronos demo` — check the pipeline works.
2. Get **real** history for the asset and timeframe you intend to trade.
3. `kronos backtest` — if the verdict is `NO DESPLEGAR`, stop here. Tuning
   parameters until the number looks good is exactly how you build a system that
   only works in the past.
4. `kronos validar` — if the edge doesn't survive out of sample, it doesn't
   exist.
5. A **real demo account** for several weeks. The backtest doesn't model
   latency, weekend gaps, requotes or variable payouts.
6. Only then, and only with money you can lose entirely.

Most configurations don't get past step 3. Having the system tell you early and
with numbers is the point of the project, not its failure.

None of the ones tested here got past it: see [REPORT.en.md](REPORT.en.md).

---

## Disclaimer

Educational and research software. Binary options are high-risk instruments with
negative expectancy by design, and are restricted or banned for retail investors
in several jurisdictions (including the EU, via ESMA). Check your legal
framework. Nothing in this repository is financial advice.

## License

MIT.
