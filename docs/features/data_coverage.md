# Data coverage — why a bar is missing observations, and whether that disqualifies it

**Module:** [`ict_kronos/data/coverage.py`](../../ict_kronos/data/coverage.py)
· **Resampler:** [`ict_kronos/data/resampler.py`](../../ict_kronos/data/resampler.py)
· **Production universe:** [production_universe.md](production_universe.md)

## 1. The defect a fresh month exposed

The resampler kept a target bar only when it had a **full** complement of source bars —
1440 of 1440 one-minute bars for a Daily. Real FX never delivers that. The best EURUSD
day in July 2026 had 1438 minutes, missing 21:03 and 23:39.

Measured on EURUSD, 2026-07:

| Timeframe | Target bars dropped |
|---|---:|
| 5m | 1.7% |
| 15m | 4.3% |
| **1H** | **10.7%** |
| **4H** | **22.5%** |
| **1D** | **100%** |

The engine had been validated on the timeframes that lose almost nothing, while
production trades the three that lose the most. Daily was structurally impossible.

**The rule was answering the wrong question.** *"Did every constituent minute trade?"*
and *"is this bar a valid aggregation of what traded?"* are different questions. A minute
with no ticks is a minute with no trades — not missing data.

## 2. Three causes, not one category

| Cause | Status | Determined from |
|---|---|---|
| `BOUNDARY` | **proven** | The bar's period is not fully inside the observed data extent |
| `MARKET_CLOSED` | **proven** | The dataset's own recurring closure profile (§3) |
| `UNDETERMINED` | **not proven** | Everything else — retained, and flagged as such |

And a separate fitness-for-use label, because "the market was shut" and "we do not know
why this is missing" must not be indistinguishable to a consumer:

| Quality | Meaning | Production-eligible |
|---|---|---|
| `COMPLETE` | Every expected observation present | yes |
| `MARKET_GAP` | Missing, all of it in a proven closure | yes |
| `DEGRADED_UNKNOWN` | Missing, cause not established | yes — **flagged** |
| `BOUNDARY_INCOMPLETE` | Period not fully covered by the dataset | **no** |

**Only `BOUNDARY_INCOMPLETE` rejects a bar.** There is no 95%, 98% or 99% cut-off,
because no evidence supports one — a guard test asserts no such constant appears in the
module. A coverage ratio is a **quality signal**, never a validity rule.

Nothing is fabricated, forward-filled or interpolated on any path. A gap stays a gap;
what changed is only whether the surrounding bar is thrown away.

## 3. The session profile — proven, never assumed

A `(weekday, minute_of_day)` slot is treated as closed when it is absent on **every**
observed occurrence of that weekday. One tick, on one day, anywhere in the window,
disqualifies it.

That is the most conservative form of the rule: it can only ever **under-claim** closure,
so a genuine provider outage is never quietly relabelled as "the market was shut". The
error, when it occurs, is in the safe direction — an unexplained gap stays unexplained.

Per **weekday**, because the FX week is not uniform: a minute that is shut on Friday
evening is wide open on Tuesday.

`min_occurrences = 2` is a **sample-size guard, not a coverage threshold**. "Recurring"
cannot be concluded from a single observation of a weekday, and two is the smallest
number for which the word means anything.

This is an inference about *this dataset*, and it is labelled as one. It is **not** a
holiday calendar and does not pretend to be. A real session/holiday calendar — already a
known gap in [HANDOFF.md](../dev/HANDOFF.md) — would supersede it.

### What it discovered on July 2026, unprompted

| Symbol | Weekday | Closed slots | Window |
|---|---|---:|---|
| EURUSD | Fri | 180 | 21:00–23:59 |
| EURUSD | Sat | 1440 | all day |
| EURUSD | Sun | 1260 | 00:00–20:59 |
| XAUUSD | Mon–Thu | 60 each | **21:00–21:59** |
| XAUUSD | Fri | 180 | 21:00–23:59 |
| XAUUSD | Sat | 1440 | all day |
| XAUUSD | Sun | 1320 | 00:00–21:59 |

The XAUUSD 21:00–21:59 block is the metals daily settlement break, and the profile found
it from the data alone — no calendar, no assumption, no hard-coded hour. EURUSD shows no
such daily break because EURUSD *does* trade in that hour on some days, and one traded
minute is enough to withhold the claim.

## 4. Per-bar metadata

Every resampled bar gets a `BarCoverage` record, and the dataset manifest carries the
per-timeframe summary:

```
expected_source_observations    actual_source_observations
missing_observations            coverage_ratio
market_closed_observations      undetermined_observations
longest_missing_run             boundary_incomplete
cause                           quality               production_eligible
```

`longest_missing_run` is reported separately from the count because a structured outage
looks nothing like scattered quiet minutes, and pooling them would hide exactly that.
Three missing minutes in a row and three scattered across an hour have identical coverage
ratios and completely different explanations.

## 5. The regression case: EURUSD 2026-07-07 Daily

| | |
|---|---|
| Coverage | 1438 / 1440 = **0.9986** |
| Missing | 21:03 and 23:39 |
| Longest run | 1 |
| `market_closed_observations` | 0 |
| `undetermined_observations` | 2 |
| Cause | `UNDETERMINED` |
| Quality | `DEGRADED_UNKNOWN` |
| Production-eligible | **yes** |

**Cause analysis.** Two isolated single minutes, late in the session, on one day. Across
July the median weekday missing-run for EURUSD is **1 minute**, over 113 separate runs —
scattered, not structured. That pattern is consistent with low-liquidity minutes in which
no tick printed, and inconsistent with an outage, which would show as contiguous runs at
correlated times. But it is **not proven**, so it is not claimed: the bar is retained and
marked `DEGRADED_UNKNOWN` rather than being called complete.

The bar itself is a real aggregation of everything that traded that day:
`O 1.14413 H 1.14473 L 1.13999 C 1.14013`, volume 52129. Under the old rule it did not
exist.

## 6. What is deliberately not done

* **No threshold.** Not 95%, not 99%, not any number. Nothing in the data supports one.
* **No fabrication.** No bar is invented, forward-filled, interpolated or carried forward.
* **No lower-timeframe reconstruction.** 1m data may be used to *diagnose* whether a
  higher-timeframe bar was observable — that is what the session profile does — but never
  to invent OHLC the production source did not publish.
* **No holiday calendar.** Bank holidays currently land in `DEGRADED_UNKNOWN`, which is
  the honest answer for a system that has no calendar to check.

## 7. Effect on the production timeframes

After the change, on 2026-07:

| Series | Bars | Complete | Market gap | Degraded | Boundary (rejected) |
|---|---:|---:|---:|---:|---:|
| EURUSD 1H | 549 | 490 | 0 | 59 | 0 |
| EURUSD 4H | 141 | 110 | 1 | 30 | 1 |
| EURUSD 1D | 26 | 0 | 1 | 25 | 1 |
| XAUUSD 1H | 523 | 522 | 0 | 1 | 0 |
| XAUUSD 4H | 140 | 114 | 25 | 1 | 1 |
| XAUUSD 1D | 26 | 0 | 25 | 1 | 1 |

Exactly one bar per aggregated series is rejected: the final period, truncated because
the month's data ends at 20:59 on 31 July. That is the rule working as intended.

No EURUSD Daily bar is `COMPLETE`, and that is the correct description rather than a
failure — a 24-hour day in which every one of 1440 minutes prints a tick does not occur.
