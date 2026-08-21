# The production universe — 1H / 4H / Daily, and the Daily discrepancy

**Module:** [`ict_kronos/features/production.py`](../../ict_kronos/features/production.py)
· **Dataset:** [dataset.md](dataset.md) · **Targets:** [targets.md](targets.md)

## 1. What production means

```
EURUSD × {1H, 4H, 1D}     production: dataset, features, targets, training, decisions
XAUUSD × {1H, 4H, 1D}

*      × {1m, 5m, 15m}    research and regression ONLY — never production
```

Lower timeframes keep full detector support. They are how the engine is regression
tested, how leakage is probed at every cut, and how research questions get asked. They
are **refused** at the production dataset boundary.

`assert_production_pair` raises rather than filtering. A silently dropped combination
looks exactly like a combination that produced no rows, and the failure this guard
exists to prevent — a lower timeframe reaching a model unnoticed — does not look like an
error at all. It looks like more data.

The universe is a module constant, not configuration. A configurable production universe
is one environment variable away from training on 1-minute bars.

## 1a. Where production candles come from

```
Dukascopy native 1H  ──►  Production 1H
                     └──►  Production 4H   (exactly four valid native 1H bars)
Dukascopy native 1D  ──►  Production 1D

ticks / 1M / 5M / 15M ──►  NOT a production dependency, at any point
```

Probed against the live feed rather than assumed:

| File | Result |
|---|---|
| `{SYM}/{YYYY}/{MM}/BID_candles_hour_1.bi5` | **200** — native 1H |
| `{SYM}/{YYYY}/{MM}/BID_candles_day_1.bi5` | **200** — native 1D |
| `BID_candles_hour_4.bi5`, `BID_candles_min_240.bi5` | **404** |
| `BID_candles_min_5.bi5`, `BID_candles_min_15.bi5` | **404** |

There is no native 4H series, so 4H is aggregated from native 1H and from nothing else.
`MM` is **zero-based** — July is `06`.

**The provider pads closed periods.** A shut hour is not absent from the native file; it
is a flat zero-volume candle carrying the prior close forward — 195 of 744 EURUSD hourly
records in July 2026, all `O==H==L==C`, including every hour of Saturday. Consuming those
would feed forward-filled prices to the feature pipeline, so they are identified, dropped
and counted. Dropping them is not a repair: it restores the absence the market had.

**A 4H bar needs four 1H bars.** A window missing an hour is withheld, never compressed.
Even a *proven* market closure withholds it: proving why an hour is absent explains the
gap, it does not restore the hour, and three traded hours labelled `4h` would be a
different candle wearing the same name. Four dispositions, each window in exactly one:
`EMITTED`, `WITHHELD_BOUNDARY`, `WITHHELD_MARKET_CLOSED`, `WITHHELD_UNDETERMINED`.

## 2. The Daily discrepancy — read this before using `1D`

> **This repository's `1D` bar is a UTC-midnight day. It is not the FX broker daily, and
> it does not align with the engine's own True Daily Open.**

Three different "days" are in play, and conflating any two of them would be a silent
transformation of the data:

| Definition | Boundary | Where it comes from |
|---|---|---|
| `Timeframe.D1` in production | **00:00 UTC** | Dukascopy **native** `BID_candles_day_1` |
| FX broker convention | 17:00 New York | Dukascopy and most retail feeds |
| R2-05.1 True Daily Open | **00:00 America/New_York** | ICT, `docs/ict/true_daily_open.md` |

### The measured consequence

`TrueDailyOpenDetector` finds a level by **exact match** on the boundary instant — it
never substitutes, interpolates or carries forward (that is deliberate, and documented in
`true_daily_open.md` §3.1). The boundary for 2026-06-15 is `04:00 UTC`. A `1D` bar opens
at `00:00 UTC`. They never coincide, in either half of the year:

```
D1 bar opens          2026-06-15 00:00 UTC
True Daily Open at    2026-06-15 04:00 UTC   (00:00 New York, EDT)
                      2026-01-15 05:00 UTC   (00:00 New York, EST)
```

So on `1D`:

```
TrueDailyOpen levels                       = 0, always
DailyOpenContext.level_id                  = None
distance_from_true_daily_open_points        = None
session.trading_day_age_minutes             = None
```

**This is correct behaviour, not a bug.** The detector is answering "was there a bar that
opened exactly at the New York midnight boundary?" and on a UTC-midnight grid the answer
is no. It is the same answer it already gives for 4H under EST, which R2-05.1 documented.

### What is deliberately NOT done

* **No NY-aligned daily bar is fabricated.** Re-rolling 1m bars on a 00:00-New-York
  origin would produce a `1D` series this data source never published, under a label
  (`1D`) that already means something else in this repository.
* **No boundary is snapped, rounded or carried forward** to make the detector fire.
* **No second daily definition is introduced** alongside the existing one.

Each of those would make the feature *appear*, and what would appear is an artefact.

### What this costs, concretely

Of the 56 features, the two daily-open-derived ones are unavailable on `1D`
(`distance_from_true_daily_open_points`, `trading_day_age_minutes`). Every other feature
is available, and the daily open remains fully available on **1H and 4H**, which is where
the production system will actually read it — a 00:00 NY boundary lands exactly on a 1H
bar open all year, and on a 4H bar open during EDT.

### The decision this needs

Resolving the discrepancy properly means choosing a daily convention for the project, and
that is an **architectural decision, not an implementation detail**. The three candidates
are recorded here so the choice is made deliberately:

| Option | Effect | Cost |
|---|---|---|
| **A. Leave `1D` as UTC-midnight** (current) | Two daily-open features unavailable on `1D`; every other feature works | The `1D` bar does not match how the instrument is conventionally quoted |
| **B. Add a distinct NY-anchored daily timeframe** | Daily open aligns; matches ICT's frame of reference | A new `Timeframe` member and a resampler origin; two daily series to keep straight |
| **C. Re-anchor `1D` to 17:00 New York** (broker convention) | Matches the feed's own daily candles | Still does not align with the 00:00 NY True Daily Open; changes the meaning of existing `1D` data |

**Option A is what is implemented**, because it is the only one that changes nothing.
B and C both change what a `1D` bar *is*, and that is not a change to make silently while
validating a pipeline.

## 3. Same-bar TP/SL stays unresolved

A production `TP_BEFORE_SL` label whose bar touches both barriers is `UNRESOLVED`, and it
may **not** be resolved by inspecting 15m, 5m or 1m bars. That would be a research
timeframe entering a production label through the back door — precisely what §1 forbids —
and the resulting label would encode information the production system, which trades
completed 1H/4H/Daily candles, does not have.

If ambiguity is high, it is reported. See [targets.md §3.4 and §3.5](targets.md).

## 4. Target parameters are per instrument × timeframe

`ProductionTargetParameters` exists because a single global threshold measures two
different questions on two instruments:

* **A point is not a unit of volatility.** 50 points is 0.0005 on EURUSD and five cents
  on gold.
* **Bar range grows with timeframe.** A barrier a 1H bar rarely spans is one a Daily bar
  almost always spans, and a barrier inside the typical bar range turns almost every
  label into `SAME_BAR_AMBIGUITY`.

Parameters are derived from the **measured bar range** of each pair — never from which
value produced the most resolved labels. Maximising resolution is how a label gets tuned
into meaninglessness. Every value carries its `rationale`.
