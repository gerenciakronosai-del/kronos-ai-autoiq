*[Español](INFORME.md) · **English***

# Results report

What was measured, on what data, and what came out. This document exists because
the project's conclusion is negative, and a negative conclusion without
methodology is worth nothing: anyone can say "it doesn't work".

**One-line summary:** none of the ~300 hypotheses tested, across five markets
and four time horizons, produced an edge that survived the cost of trading.

---

## How "it works" is decided

Four filters, all mandatory. A hypothesis is only called a *survivor* if it
passes all four.

**1. It beats the breakeven threshold.** Winning more than half is not enough.

| Instrument | Threshold | Formula |
|---|---|---|
| Binary option, 84% payout | 54.35% | `1 / (1 + payout)` |
| Stop and target, 2:1 | 33.33% | `1 / (1 + rr)` |
| Stop and target 2:1 with commission | `(1 + cost_R) / 3` | |

That third row is the one most often forgotten. With a commission worth 7% of
risk, the real threshold for a 2:1 isn't 33.3% but 35.7%.

**2. It is distinguishable from chance.** One-sided binomial p-value against the
threshold, **Bonferroni-corrected** over the real number of hypotheses tested.
With 72 hypotheses at 5% you expect ~3.6 false positives even if none of them
works.

**3. It survives out of sample.** The edge has to hold on a stretch that wasn't
looked at while building the rule.

**4. It beats a control.** The catalogue includes *always CALL*, *always PUT*
and *coin flip*. Without them the result isn't interpretable: if "always CALL"
shows an edge, you're measuring the drift of the period, not predictive power.

On top of that, **every backtest models the cost of trading** (0.5 pips of
spread by default in forex, 0.2% taker commission in crypto). Never zero. It's
the safeguard that kills the most results.

---

## Part 1 — Binary options on EURUSD

Real account payout: **84%**, so the threshold is 54.35%.

| Market | Sample | Hypotheses | Survivors | Best result |
|---|---:|---:|---:|---|
| EURUSD 1 min | 744,403 candles (2024-2025) | 72 | **0** | — |
| EURUSD 1 h | 149,617 candles (2000-2025, 25.6 years) | 90 | **0** | Extreme RSI at 8 h: 52.81% |
| EURUSD-OTC (IQ Option) | 20,000 candles | 72 | **0** | 50.58% (coin-flip control: 48.00%) |

The `confluence` strategy on 1-minute EURUSD: **48.31% over 3,508 trades**.
Simulated with full risk management, the account fell from $1,000 to $99.84 and
the *kill switch* stopped it after eleven months.

### The most important methodological finding

The same edge, on the same asset and the same rule, depending on how much data
you look at:

| Sample | Win rate | Edge |
|---|---:|---:|
| 12,468 candles (2 years) | 56.36% | **+2.01%** |
| 36,565 candles (6 years) | 53.48% | −0.87% |
| 149,617 candles (25 years) | 52.81% | −1.54% |

**The positive edge evaporated when the sample was multiplied by twelve.**
Neither the strategy nor the market changed: how much was being looked at did.
The same thing happened in live execution, where the bot hit 80% accuracy over
10 trades and 40% over the next 5.

Practical corollary: **distrust any result with fewer than 1,000 trades**,
including your own.

### Why lengthening the horizon wasn't enough

Lengthening expiry does improve the ratio of predictable movement to spread —
from 1x at 1 minute to 9.6x at 1 hour — and the edge rises from −6.81% to
−1.54%. But it doesn't cross the threshold. The broker calibrates the payout
just above what the market offers; there is no gap there.

---

## Part 2 — Crypto with stop and target

A deliberate change of ground, attacking both previous limitations:

* **From binaries to stop/target.** The threshold is no longer set by the
  broker. With 2:1 you only need 33.3% instead of 54.35%.
* **From forex to crypto.** Higher volatility relative to cost, a 24/7 market,
  and a public documented API (Binance) instead of reverse engineering.

Strategy: Bollinger band breakout. Stop at 1.5× ATR, target at 2R, 0.2% taker
commission, maximum horizon 30-48 candles.

| Candles | Assets | Commission / R | Mean expectancy | Positive | vs control |
|---|---:|---:|---:|---:|---:|
| 1 hour | 1 | 18% | strongly negative | — | — |
| 4 hours | 7 | 5-10% | −0.134R | 1 of 7 | **+2.26%** |
| **Daily** | 7 | 1-3% | **−0.085R** | 2 of 7 | +1.52% |
| Weekly | 6 | <1% | −0.201R | 1 of 6 | **−12.19%** |

There is an optimum at daily candles and **it does not cross zero**. Below it,
cost kills the result; above it, the sample runs out of trades.

### The effect is real, but smaller than the toll

The "vs control" column is positive and consistent at 4 hours and daily: the
Bollinger breakout beats a naive control by **+1.5 to +2.3 points** of win rate,
across two independent horizons and some 14,000 trades. That isn't noise; it's
the momentum effect the literature describes in crypto.

The problem is the arithmetic: the effect is worth ~2 points and trading it
costs ~4.

### The weekly-horizon trap

At weekly the sign flips (−12.19% against the control), and the reason isn't the
strategy, it's the sample. Split by direction:

| | Trades | Win rate |
|---|---:|---:|
| CALL | 320 | 35.6% |
| PUT | 82 | **4.9%** |

Zero percent accuracy on the PUTs of five of the six assets. The sample is nine
years of a bull market: at a weekly horizon with a 2R target, going short on
crypto essentially never reached the target.

And the detail that closes the argument: **the strategy's CALLs (35.6%)
underperform buying at arbitrary moments (39-45% for the control).** A profitable
`always CALL` in this sample isn't an edge: it's that the market went up.
Confusing the two is overfitting to the era instead of to the data.

---

## Conclusion

No exploitable edge was found in any instrument or horizon tested.

That doesn't prove none exists anywhere — it proves it isn't in the obvious
place, which is where most people look: standard technical indicators over
public prices, at retail costs.

What is demonstrated, and is reproducible with the commands below:

1. Trading cost is not a second-order detail. A 0.2-pip spread erased a
   12.9-sigma signal.
2. Small samples lie systematically, and in the direction you like.
3. Without a control to compare against, a win rate means nothing.

---

## Reproducing

No external dependencies: Python 3.11+ only.

```bash
python -m kronos selftest        # 359 tests, ~47 s, no network
```

**Forex.** Download *1 Minute Bar Quotes* for EURUSD from
[HistData.com](https://www.histdata.com/download-free-forex-historical-data/)
(free, no registration) and:

```bash
python -m kronos importar ~/Downloads/HISTDATA_*.zip --out data/eurusd.csv
python -m kronos explorar --data data/eurusd.csv --symbol EURUSD --spread 0.5
```

For the 25-year stretch, resample to hourly candles with `--timeframe 3600` on
the importer.

**Crypto.** Data downloads itself from Binance's public API:

```bash
python -m kronos cripto --symbol ETHUSDT --timeframe 86400 --velas 3000
```

`--timeframe` accepts `3600` (1 h), `14400` (4 h), `86400` (daily) and `604800`
(weekly).

---

## Known limitations

So nobody has to discover them by reading the code:

* **Forex data is spot, not the broker's.** Binaries settle against the broker's
  own feed, which is not identical, especially at the exact expiry price. The
  backtest is indicative.
* **`kronos/broker/iqoption.py` is a structural adapter.** It depends on
  `iqoptionapi`, a reverse-engineered community client that is not a project
  dependency. `activos_abiertos()` returned 0 against a real account and this
  was never diagnosed.
* **The importer's "Coverage %" line assumes 1,440 candles per day** regardless
  of timeframe, so it understates coverage for hourly data.
* **The backtest doesn't model latency, requotes or variable payouts.**
* **One test in `tests/test_live.py` depends on timing** and can fail
  intermittently on loaded machines.
* **The weekly crypto sample (402 trades) is insufficient** to conclude on its
  own. It was run knowing that, and is reported with the caveat.

---

## Disclaimer

Educational and research software. Nothing here is financial advice. Binary
options are high-risk instruments with negative expectancy by design, and are
restricted or banned for retail investors in several jurisdictions (including
the EU, via ESMA).
