# Phase 2 — Market Intelligence — MASTER STORY (R2-09 → R2-13)

**Specification document. Written before any of R2-09 … R2-13 exists.**
No production code, no test and no schema in this repository was changed to produce it.

> **Status: awaiting approval. Nothing may be implemented until R2-09 is approved
> explicitly, and each story stops for approval again when it completes (§12).**

| Story | Name | Story doc | Concept map | Tasks |
|---|---|---|---|---|
| R2-09 | Liquidity Model | [story](features/R2-09-LIQUIDITY-STORY.md) | [map](features/R2-09-CONCEPT-MAP.md) | [tasks](../tasks/Phase-2-ICT-Engine/R2-09-TASKS.md) |
| R2-10 | COT Positioning Model | [story](features/R2-10-COT-STORY.md) | [map](features/R2-10-CONCEPT-MAP.md) | [tasks](../tasks/Phase-2-ICT-Engine/R2-10-TASKS.md) |
| R2-11 | Multi-Timeframe Context | [story](features/R2-11-MTF-STORY.md) | [map](features/R2-11-CONCEPT-MAP.md) | [tasks](../tasks/Phase-2-ICT-Engine/R2-11-TASKS.md) |
| R2-12 | Market State v2 | [story](features/R2-12-MARKET-STATE-V2-STORY.md) | [map](features/R2-12-CONCEPT-MAP.md) | [tasks](../tasks/Phase-2-ICT-Engine/R2-12-TASKS.md) |
| R2-13 | Feature Vector v2 | [story](features/R2-13-FEATURE-VECTOR-V2-STORY.md) | [map](features/R2-13-CONCEPT-MAP.md) | [tasks](../tasks/Phase-2-ICT-Engine/R2-13-TASKS.md) |

---

## 1. Objective

R2-01 … R2-08 built an engine that can answer, for one instrument on one timeframe:

> *What could a decision made at instant `T` have known about ICT structure, and what
> happened afterwards?*

Three things a real trading decision uses are **absent** from that answer, and all three
are already named as gaps in [HANDOFF.md](dev/HANDOFF.md) "Open items for Phase 2":

| Gap | Where it is recorded today |
|---|---|
| Liquidity is a flat list of levels — no pools, no internal/external, no asymmetry | [liquidity.md](ict/liquidity.md) §14 limitations **2** and **3** |
| No multi-timeframe context at all — a 1H state knows nothing about 4H or 1D | HANDOFF open item **2**; [market_state.md](ict/market_state.md) §10 |
| No positioning data of any kind | *nothing in the repository mentions COT* |

R2-09 → R2-13 closes those three gaps and re-projects the result into the dataset the
model layer will consume. It adds **no new ICT detector** and **re-derives nothing**.

**This phase does not answer whether any of it is predictive.** That is Phase 4's job.
A rigorous negative result is a successful outcome (CLAUDE.md preamble). Nothing in these
five stories may claim, imply or encode predictive value.

---

## 2. Architecture — one responsibility per layer

```
     Dukascopy native 1H + native 1D            CFTC Commitments of Traders
                      |                                    |
        4H = four native 1H bars                    weekly COT reports
                      |                                    |
   +------------------+------------------+                 |
   |  data/production -- hashed,         |                 |
   |  manifest-backed:  1H   4H   1D     |                 |
   |  no tick or minute dependency       |                 |
   +------------------+------------------+                 |
                      |                                    |
        R2-01 .. R2-06 ICT detectors  (UNCHANGED)          |
                      |                                    |
        R2-07 ICTEngineView.state_at(T)  (UNCHANGED)       |
                      |                                    |
   +------------------+------------------------------------+-------------+
   |                  v                                    v             |
   |        R2-09 LiquidityModel                 R2-10 CotModel          |
   |        pools . internal/external            point-in-time report    |
   |        . interaction states                 selection . derived     |
   |        . asymmetry                          positioning series      |
   |                  |                                    |             |
   |                  v                                    |             |
   |        R2-11 MtfContextBuilder                        |             |
   |        1H <- 4H <- 1D, completed bars only            |             |
   |                  |                                    |             |
   +------------------+------------------------------------+-------------+
                      +----------------+-------------------+
                                       v
                        R2-12 ICTMarketState v2   (additive)
                                       |
                                       v
                        R2-13 ICTFeatureVector v2 (append-only)
                                       |
                                       v
                   R2-08 DatasetRow / targets / splits  (semantics UNCHANGED)
                                       |
                                       v
                          Phase 4 model training (NOT THIS PHASE)
```

### 2.1 What each layer may and may not do

| Layer | May | Must not |
|---|---|---|
| **R2-09** | Read R2-04 levels/sweeps and R2-06 ranges through their point-in-time APIs; cluster; measure distances | Re-detect a swing, session, day boundary or sweep; mutate an R2-04 record; redefine `SWEPT` |
| **R2-10** | Read a COT dataset; select the report observable at `T`; compute derived positioning series | Read market bars; know what an ICT event is; fit any statistic using a report not yet released |
| **R2-11** | Build/borrow an `ICTEngineView` per timeframe and ask it for a state at an HTF bar close ≤ `T` | Re-run a detector with different config per timeframe; join on `timestamp`; fabricate an HTF bar; expose a forming HTF bar |
| **R2-12** | Compose the three layers into new, additive context records | Change any existing R2-07 context, field, value, or the existing `bias` rule |
| **R2-13** | Append new columns after the existing 56 | Reorder, rename, remove or change the meaning of any existing column |

**The layering rule, stated once:** if a value can be computed by layer *N*, layer *N+1*
reads it rather than recomputing it, and stores its **id**, never its geometry. This is
the R2-05.x rule ([R2-05x-CONCEPT-MAP.md](ict/R2-05x-CONCEPT-MAP.md) §3.2) applied
unchanged.

---

## 3. Dependency graph

```
                              R2-07 ICTMarketState / ICTEngineView
                                     (hard, unchanged)
                                            |
                    +-----------------------+-----------------------+
                    |                       |                       |
              R2-04 liquidity          R2-06 range            R2-01 sessions
                    |                       |                       |
                    +----------+------------+                       |
                               v                                    |
                          +---------+                               |
                          |  R2-09  |<------------------------------+
                          +----+----+        (conceptual: pools of
                               |              session extremes)
      external CFTC data       |
             |                 |
             v                 v
        +---------+       +---------+
        |  R2-10  |       |  R2-11  |
        +----+----+       +----+----+
             |                 |
             +--------+--------+
                      v
                 +---------+
                 |  R2-12  |
                 +----+----+
                      v
                 +---------+
                 |  R2-13  |
                 +----+----+
                      v
              R2-08 dataset  (consumes; two call sites change -- §7.3)
```

### 3.1 Edge classification

| Edge | Kind | Why |
|---|---|---|
| R2-04, R2-06 → R2-09 | **hard** | A pool is a clustering of R2-04 levels; internal/external needs R2-06's range. R2-09 cannot exist without both |
| R2-01 → R2-09 | **conceptual** | Session extremes are already R2-04 levels; R2-09 reaches sessions only through R2-04 |
| CFTC dataset → R2-10 | **data** | R2-10 is meaningless without a COT dataset, but depends on **no repository module** except the observability contract |
| R2-09 → R2-11 | **hard, by scope choice** | R2-11 exposes HTF *pool* proximity. Drop that feature group and the edge becomes optional — recorded in [R2-11](features/R2-11-MTF-STORY.md) §3 |
| R2-07 → R2-11 | **hard** | An HTF context *is* an `ICTMarketState` built at an HTF bar close |
| R2-10 → R2-11 | **none** | COT is not a timeframe. It aligns by release timestamp, not by bar close, and R2-11 never touches it |
| R2-09, R2-10, R2-11 → R2-12 | **hard** | R2-12 is the composition |
| R2-12 → R2-13 | **hard** | The vector is a projection of the state |

### 3.2 The proposed order was challenged, and it survives — with one change

The brief proposes a strict chain `R2-09 → R2-10 → R2-11 → R2-12 → R2-13`. The repository
shows that **R2-10 is independent of R2-09 and R2-11**: it shares only the observability
predicate. It could run in parallel.

**We still recommend the sequential order**, for one reason that outweighs the
parallelism: R2-10 is the only story in this phase that introduces an **external data
dependency, a new provider, a new factory and a network path**. Everything else is pure
computation over data the repository already holds. Interleaving a network-shaped story
with a computation-shaped story is how a "the fixture is stale" failure gets misdiagnosed
as a leakage failure.

**The one change:** R2-10 is split so that its data-acquisition half (`R2-10-A`) may begin
as soon as R2-09 is approved, because it has a long lead time — the COT history backfill
should be running while R2-09 is reviewed (§8.2). Its model half (`R2-10-B`) still runs in
sequence and still hard-stops.

---

## 4. Non-negotiable timeframes

```
TRADING / DECISION TIMEFRAMES     1H   4H   1D            <- the only ones
ANALYSIS TIMEFRAMES               1H   4H   1D            <- identical, by decision
AGGREGATION SOURCE                native 1H  (for 4H only)
SOURCE DATA                       Dukascopy native 1H and 1D candle files
                                  ticks / 1M / 5M / 15M   <- NOT a production dependency
```

[`ict_kronos/features/production.py`](../ict_kronos/features/production.py) already
enforces the universe and **raises** rather than filtering. R2-09 → R2-13 inherit that
guard unchanged; every new production entry point calls `assert_production_pair` first.

### 4.0 "Remove 1m / 5m / 15m from the production project" — the operative reading

The revised brief directs that 1m, 5m and 15m be removed from the production project.
**Two readings are possible and they differ enormously in blast radius**, so the
interpretation is fixed here rather than discovered during implementation.

| Reading | Effect | Verdict |
|---|---|---|
| **A — narrow: no lower timeframe is a production dependency, and none is downloaded, persisted or ingested for production** | Nothing to do beyond what R2-08.2 already achieved (`tick_dependency: false`, `minute_dependency: false`) plus the guards below | ✅ **ADOPTED** |
| B — literal: delete `Timeframe.M1/M5/M15` from the codebase | **Breaks 49 test files.** The deterministic fixtures are 5m; every R2-01…R2-08 regression suite, every leakage proof at every cut, and the entire `real-2024-03-08_12` fixture are lower-timeframe | ⛔ **NOT ADOPTED — see the conflict below** |

**Reading A is what the brief's own Real Data section asks for** — *"Do not download or
introduce 1m/5m/15m production data"* — and it is satisfied today.

What R2-09 → R2-13 add to enforce it:

1. Every new production entry point calls `assert_production_pair` **first**, which raises
   on `M1`/`M5`/`M15` rather than filtering.
2. A new guard test asserts **no module in this phase reads a lower-timeframe partition**,
   and that no new code path can reach `data/normalized/` (the tick-derived research store)
   on a production call.
3. The TP/SL same-bar ambiguity may **not** be resolved by inspecting a finer timeframe.
   Already true (production_universe.md §3); restated because it is the one place a lower
   timeframe could re-enter a production label through the back door.

> ### ⚠ CONFLICT FLAGGED FOR DECISION — reading B
>
> Deleting the lower timeframes outright would delete the evidence that the engine is
> correct. `tests/fixtures/market_data/*/5m.csv` is the deterministic fixture behind 49
> test files; `test_liquidity_leakage.py`, `test_structure_leakage.py`, `test_fvg_leakage.py`
> and the rest prove prefix-equivalence *at every cut*, which needs many bars in a short
> window — precisely what a lower timeframe provides and what 26 daily bars cannot.
>
> **Per the brief's own instruction — "If provider reality conflicts with the
> specification, STOP and report the conflict. Do not invent data or weaken the rule" — the
> conflict is reported rather than resolved.** Reading A is implemented; reading B is not,
> and no story in this phase deletes a `Timeframe` member. **If reading B is intended, say
> so and it becomes its own story with its own regression plan**, because it is a
> test-infrastructure migration, not a feature.

### 4.1 The provider supplies native 1H and 1D — but no 4H

**R2-08.2 (commit `cb91316`) settled this by probing the live feed rather than assuming**,
and it answers the brief's preferred-data rule almost line for line. See
[production_universe.md](features/production_universe.md) §1a and
[`data/dukascopy_candles.py`](../ict_kronos/data/dukascopy_candles.py).

| Probed file | Result |
|---|---|
| `{SYM}/{YYYY}/{MM}/BID_candles_hour_1.bi5` | **200 — native 1H exists** |
| `{SYM}/{YYYY}/{MM}/BID_candles_day_1.bi5` | **200 — native 1D exists** |
| `BID_candles_hour_4.bi5`, `BID_candles_min_240.bi5` | **404 — there is no native 4H** |
| `BID_candles_min_5.bi5`, `BID_candles_min_15.bi5` | **404** |

The absences are recorded in `KNOWN_ABSENT_NATIVE_FILES` so the finding is **evidence
rather than folklore**. Resolving the brief's rule against it:

| Brief's rule | Resolution here |
|---|---|
| 1. Use provider-supplied 1H | ✅ **Satisfied.** Native `BID_candles_hour_1` is used directly |
| 2. Use provider-supplied 4H | **Genuinely unavailable** — 404, proven by probe, recorded |
| 3. Use provider-supplied 1D | ✅ **Satisfied.** Native `BID_candles_day_1` is used directly |
| 4. Construct 4H from the provider's 1H, only because 4H is unavailable | ✅ **Exactly what is done.** `build_h4_from_native_h1` — four native 1H bars, and nothing else |
| 5. Do not reconstruct 1H from lower timeframes | ✅ **Satisfied.** 1H is native; no minute series is read on this path |
| 6. Do not reconstruct 1D from lower timeframes | ✅ **Satisfied.** 1D is native |

**The tick lane still exists and is not on this path.** It serves the historical research
fixture (`real-2024-03-08_12`, `real-2026-07`) and the 1m/5m/15m regression suites.
`minute_dependency` and `tick_dependency` are both recorded as `false` in the production
manifest.

> **Correction of record.** An earlier draft of this document asserted that Dukascopy
> publishes ticks only and that every production bar is built from a 1M base. That was
> true of `realdata.py`'s research lane and is **no longer true of production**. The
> distinction the brief draws between *source*, *aggregation*, *analysis* and *trading*
> timeframes is now literal rather than approximate.

### 4.2 The binding rule for R2-09 → R2-13

> **Read the persisted production partition. Never resample inside an analysis layer.**

`ParquetCandleStore("data/production").read(symbol, timeframe, ...)` is the only
sanctioned source of 1H/4H/1D bars for any code in these five stories. Calling
`resample()` inside R2-09, R2-11, R2-12 or R2-13 is a defect **even when it produces
identical numbers**, because it makes the analysed series unhashed, unmanifested and
unreproducible — and because on the production path 1H and 1D are *native*, so resampling
them from anything would fabricate a series the provider never published.

`build_h4_from_native_h1` is **the only permitted production aggregation in the
repository**, it lives in the ingest layer, and no analysis layer may call it either.

A guard test asserts that none of the new modules imports `resample` or
`build_h4_from_native_h1`.

### 4.3 The Daily discrepancy is inherited, not resolved

[production_universe.md](features/production_universe.md) §2 records three different
"days": `Timeframe.D1` — now the provider's **native** `BID_candles_day_1`, which is a
00:00-UTC day — the broker day (17:00 NY, used by R2-04 for PDH/PDL) and the True Daily
Open (00:00 NY, R2-05.1). **R2-09 → R2-13 change none of them and reconcile none of
them.** In particular:

- R2-09 pools may contain a `PREVIOUS_DAY_HIGH` (17:00-NY day) and a `1D` bar high from
  the same calendar date at different prices. That is correct, and it is *why* pools
  cluster by price rather than by name.
- On `1D`, `distance_from_true_daily_open_points` stays `None`, exactly as today.

Choosing a project daily convention remains an **open architectural decision**
(production_universe.md §2, options A/B/C) and is explicitly **out of scope**; option A
(leave it) continues to hold.

---

## 5. Global data-quality contract

Real data contains gaps, outages, closures, session breaks, weekend gaps, DST shifts and
genuine price gaps. The repository already separates their causes and **must not grow a
second, competing policy.**

### 5.1 The definitions in force (reused verbatim from R2-08)

From [data_coverage.md](features/data_coverage.md) /
[coverage.py](../ict_kronos/data/coverage.py):

| Concept | Definition of record |
|---|---|
| **Valid bar** | A bar the resampler emitted whose period is fully inside the observed extent. Only `BOUNDARY` disqualifies. A minute with no ticks is a minute with no trades, **not** missing data |
| **Boundary** | `bar.start < observed_start` or `bar.end > observed_end` → `BarQuality.BOUNDARY_INCOMPLETE`, `production_eligible = False` |
| **Market closure** | Every missing source observation falls in a `(weekday, minute_of_day)` slot absent on **every** observed occurrence of that weekday, with ≥ 2 occurrences → `GapCause.MARKET_CLOSED`, `BarQuality.MARKET_GAP` |
| **Unknown** | Missing observations with no established cause → `GapCause.UNDETERMINED`, `BarQuality.DEGRADED_UNKNOWN`. **Retained and flagged** |
| **Degraded data** | Represented by `BarCoverage` (expected/actual/closed/undetermined counts, longest missing run, ratio, cause, quality). Never by a fabricated value |

**There is no coverage threshold anywhere, and none may be introduced.** No 95 %, no
98 %, no 99 %. `coverage_ratio` is a quality *signal*; nothing is rejected on it.

### 5.1a Two production-path facts R2-08.2 established, which every later layer inherits

| Fact | Consequence for R2-09 → R2-13 |
|---|---|
| **The provider pads closed periods.** A shut hour is not absent from the native file — it is a flat zero-volume candle carrying the prior close forward. Measured: **195 of 744** EURUSD hourly records in July 2026, every one `O==H==L==C` with `volume==0`, including all of Saturday; **1 224** dropped for EURUSD and **1 389** for XAUUSD across the six-month window | The padding is identified, dropped and **counted** at decode, before any detector sees it. **Dropping it is not a repair — it restores the absence the market actually had.** No layer in this phase may reintroduce it, and none may treat the resulting absence as a data defect: it is a market closure |
| **A 4H bar needs its four 1H bars.** A window missing an hour is **withheld, never compressed** — even when the absence is a *proven* market closure, because proving why an hour is absent explains the gap without restoring the hour, and three traded hours labelled `4h` would be a different candle wearing the same name | Four dispositions, every candidate window in exactly one: `EMITTED`, `WITHHELD_BOUNDARY`, `WITHHELD_MARKET_CLOSED`, `WITHHELD_UNDETERMINED`. R2-09 and R2-11 therefore see a 4H series with **legitimate, explained holes**, and must treat a missing 4H bar as ordinary (R2-11 §4.7) rather than as an error |

The second fact matters most to R2-11: a withheld 4H window is a real gap in the aligned
series, so `staleness_bars` growing across it is the correct behaviour, not a symptom.

### 5.2 What R2-09 → R2-13 add

1. **Data quality becomes a feature, not a filter.** R2-13 projects `bar_quality_code`,
   `bar_coverage_ratio` and `bar_longest_missing_run` as ordinary columns
   ([R2-13](features/R2-13-FEATURE-VECTOR-V2-STORY.md) §6.6). A degraded observation
   becomes *visible* to the model instead of being silently dropped or silently included.
2. **Staleness becomes measurable.** R2-11 exposes how old the aligned HTF bar is; R2-10
   exposes how old the applicable COT report is. Both are the honest representation of
   "the market moved on and this input did not".
3. **Availability is tri-state, never bi-state.** For every new context, three answers are
   distinguishable and never collapsed:
   `present` · `absent because it genuinely does not exist` · `absent because data is
   missing`. Encoded per story, always with an explicit reason code.

### 5.3 Absence rules that carry through unchanged

- Never fill. Never interpolate. Never forward-fill a **price**.
- Never invent a candle, a level, a pool, a report or an HTF bar.
- A missing candle is not evidence of a market closure until the recurring-slot rule
  proves it.
- Carrying the **last released COT report** forward and the **last closed HTF bar**
  forward is *not* forward-filling: the last known value genuinely is the last known value
  at `T`. Its age is always published beside it, so a consumer can tell "fresh" from
  "eleven days old".

---

## 6. Global leakage contract

### 6.1 The invariant

> For every observation at `as_of = T`, no feature, state value, COT figure, liquidity
> object, pool, HTF context or normalisation statistic may depend on any information whose
> earliest availability instant is later than `T`.

### 6.2 The one gate

[`ict_kronos/ict/contract.py::is_observable_at`](../ict_kronos/ict/contract.py) remains
**the only** observability predicate in the codebase. Every new record type —
`LiquidityPool`, `CotReport`, `MtfContext` — carries a `confirmation_timestamp` and
satisfies the existing `Confirmable` protocol, so `filter_observable` accepts it with no
new code.

For COT that field is populated from the **release timestamp**, not the report date. That
single mapping is what makes COT non-leaky, and it is stated in exactly one place
([R2-10](features/R2-10-COT-STORY.md) §4).

**No module in this phase may hand-roll `x.confirmation_timestamp <= t`.** A source-level
guard test per module — the shape already used by
`test_liquidity_module_hand_rolls_no_observability_comparison` — enforces it. The guard
strips docstrings *and* comments first (HANDOFF, R2-06 gotchas).

### 6.3 The eight required proofs

**This is the canonical numbering. Every story R2-09 → R2-13 uses these eight IDs, these
definitions, and no others.** Older drafts used a different L4/L5/L6 assignment; that
numbering is **void** and must not survive anywhere.

| # | Proof | Form |
|---|---|---|
| **L1** | **No future bars** | Truncating or appending bars after `as_of` must not change any output at `as_of` |
| **L2** | **Future OHLC mutation** | Violently modifying future OHLC must not change historical outputs |
| **L3** | **Wick dependency declared** | Each output declares whether it depends on **close**, **high/low wick**, a **structural event**, or a **confirmed event**. Legitimate wick dependence is **not** leakage; undeclared wick dependence is a defect |
| **L4** | **Point-in-time lifecycle** | Lifecycle state reflects only information available at `as_of` — a level swept later is still `ACTIVE` now |
| **L5** | **Prefix equivalence** | A prefix ending at `T` reproduces the state at `T`. **Targets are exempt** — they are future-dependent by definition |
| **L6** | **Identity stability** | An id must not change merely because future data became available |
| **L7** | **External inputs** | External data (COT) obeys the same point-in-time discipline; future releases cannot alter historical rows |
| **L8** | **Non-vacuous control** | Every leakage suite includes a control mutation that **must change** the result. A test that passes because the implementation computes nothing is invalid |

**Provenance integrity is contracted separately**, in each story's Provenance section, and is
tested by marker substitution rather than value matching. It is not an L-number under this
contract, and it is **not** thereby optional — §7.1 and each story's provenance matrix carry
it.

**L6 is about stability, not just collision.** Two questions, both required: do two distinct
objects ever share an id (collision), and does one object's id change when later data
arrives (instability)? The second is the one a derived, point-in-time object like a
liquidity pool can fail.

**L8 is what makes L1, L2, L5 and L7 mean anything.** HANDOFF, R2-08 gotchas: *"A leakage
test with no control proves nothing — 'features unchanged when the future is mutated' passes
just as happily against a layer that computes nothing."*

### 6.3a Nothing from the R2-05.x set is silently dropped

The R2-05.x set ([R2-05x-CONCEPT-MAP.md](ict/R2-05x-CONCEPT-MAP.md) §4) had two members
that are not L-numbered above. **Both are still required**, as named testing obligations:

| R2-05.x proof | Where it now lives |
|---|---|
| Timestamp invariant (`confirmation >= event`) | Enforced by the `IctEvent` constructor and re-asserted per story; it is a **constructor guarantee**, not a test that can be skipped |
| Naive divergence | §6.4 and §15 of the brief — the **deliberately incorrect implementation**, which is the mechanism by which **L8** is satisfied |

Renumbering must not quietly delete a proof, which is the only reason this table exists.

### 6.4 The deliberately incorrect implementation per story (§15, proves L8 non-vacuous)

| Story | The deliberately incorrect implementation that must be built and proven to FAIL |
|---|---|
| **R2-09** | **Count future touches after `as_of`** — walk the whole frame instead of the observable prefix |
| **R2-10** | **Join COT by report week** instead of by publication timestamp |
| **R2-11** | **Use an unfinished 4H candle** — read the forming HTF bar |
| **R2-12** | **Silently alter the existing `bias`** — extend its sources without renaming it |
| **R2-13** | **Insert new features before the existing 56 columns** |

Each is written into the story's leakage suite, run, and asserted to **fail** the
corresponding L-proof. That is what makes the audit non-vacuous.

### 6.5 Normalisation

**No scaler is fitted anywhere in this phase.** Not globally, not per split, not at all.
Every normalised value in R2-13 is either dimensionless by construction (a ratio of two
quantities both known at `T`) or an **expanding-window statistic over observations
available at `T` only**, with an explicit warm-up during which it is `None`.

Fitting a scaler over train + validation + test is forbidden. Fitting one over train alone
belongs to Phase 4, where it can be recorded in an experiment.

---

## 7. Global contracts

### 7.1 Identity

**Identity is never price + timestamp alone.** Every new object id is built from its
**source ids**, following the R2-05.x convention.

| Object | Id | Collision stress case that must be tested |
|---|---|---|
| `LiquidityPool` | `pool:{side}:{sha1 of sorted member level ids}[:12]` | Two pools at the same price on the same bar with different members |
| `CotReport` | `cot:{market_code}:{report_type}:{report_date}:{release_timestamp}` | The same report re-released as a revision |
| `MtfContext` | `mtf:{base_tf}:{htf}:{aligned_htf_close_time}` | Two base bars sharing one aligned HTF bar; two HTF timeframes with identical values |

Required collision stress cases (brief §13):

1. Multiple liquidity levels created on the same bar
2. Multiple pools whose *price* is identical but whose membership differs
3. Multiple sweeps confirmed on the same bar
4. One COT report applied to hundreds of market bars — must yield **one** id, referenced
   many times, never many ids
5. Identical HTF values arising from different source objects
6. Multiple MTF contexts sharing a base timestamp (a 1H row carries one 4H context and one
   1D context)

Positional dataframe indexes remain **diagnostics only** and are never identity or join
keys (the R2-05.1 rule).

### 7.2 Streaming

Every new component declares its behaviour under three regimes, and **no new asymmetry may
be introduced without a documented, tested exception**:

| Regime | Meaning |
|---|---|
| **Batch** | `analyse(all bars)` then query at `T` |
| **Prefix** | `analyse(bars up to T)` then query at `T` |
| **Bar-by-bar** | Feed one bar at a time, query after each |

The rule is `batch == prefix == bar-by-bar` for every component.

The engine has exactly **one** permitted asymmetry today — the True Daily Open's zero-lag
confirmation ([market_state.md](ict/market_state.md) §10a), where a prefix sees *less*
(staler), never *more*. This phase raises **two candidates**, both of which must be either
eliminated or documented and pinned with the same rigour:

| Candidate | Story | Disposition |
|---|---|---|
| An HTF prefix cannot contain an HTF bar that has opened but not closed, so an HTF True Daily Open is invisible to the prefix | R2-11 | **Inherited** from R2-05.1: same shape, same direction (prefix sees staler). Documented, pinned, and must fail loudly if it ever points the other way |
| A COT provider queried at `T` may return a report set a later query does not (a revision replaces it) | R2-10 | **Eliminated by design.** Revisions are additional immutable records with their own release timestamps; nothing is ever replaced. See [R2-10](features/R2-10-COT-STORY.md) §7 |

### 7.3 Serialisation

Every new record follows the conventions already in force and adds none:

- `as_dict()` — stable field order, enums by `.value`, timestamps ISO-8601 UTC, tuples as
  lists, **`None` for missing, never `0`, never `NaN`**.
- `from_dict(as_dict()) == value` **exactly**, including `None`.
- `NaN` exists in exactly one place in the codebase: `ICTFeatureVector.as_row()`. A `NaN`
  reaching a record breaks equality and would report a spurious streaming difference
  (HANDOFF, R2-07 gotchas). Any `NaN` produced by an upstream layer is translated to
  `None` at the boundary that consumes it.
- The schema version is carried on the record.

**Two existing R2-08 call sites must change in R2-13**, and this is the only edit to R2-08
that this phase requires:

| File | Lines | Change |
|---|---|---|
| [`features/dataset.py`](../ict_kronos/features/dataset.py) | 30, 269, 282, 305 | `from ..ict import FEATURE_NAMES` → `ICTFeatureVector.column_names()` |
| [`features/audit.py`](../ict_kronos/features/audit.py) | 21, 147, 148, 157 | same |

Both already have the accessor available (`ICTFeatureVector.column_names()`), so the edit
is mechanical. **Left undone, `rows_to_frame` and `audit_dataset` silently drop every new
column** — a defect that produces a smaller dataset rather than an error.

### 7.4 Configuration

Every threshold, tolerance, lookback and window is configuration (CLAUDE.md rule 4),
following the existing shape exactly:

- a frozen dataclass config per module, validated in `__post_init__`, with `as_dict()`;
- an env-backed `*Config.from_env()` in [`app/config.py`](../ict_kronos/app/config.py)
  wired into `Settings`;
- documented in `.env.example`;
- **no literal in logic.**

One deliberate exception, inherited: `PRODUCTION_TIMEFRAMES` / `PRODUCTION_SYMBOLS` stay
module constants, because a configurable production universe is one environment variable
away from training on 1-minute bars.

### 7.5 The LLM boundary

Unchanged and absolute (CLAUDE.md rule 3). No LLM output enters any record, feature, state
or dataset produced by these five stories. Every rule here is a deterministic, testable
function of observed data.

---

## 8. Global validation strategy

### 8.1 The suites every story ships

Nine categories, all mandatory where applicable:

| Suite | Purpose |
|---|---|
| **unit** | Normal, edge, malformed, empty, single-element, degenerate |
| **boundary** | Exactly-at, one-unit-before, one-unit-after, first, last, warm-up ±1 |
| **leakage** | **L1 … L8** (§6.3), plus the deliberately broken implementation (§6.4) |
| **provenance** | Every emitted id resolves; every source observable no later than the composite; **marker substitution**, never value matching |
| **identity / collision** | The stress cases of §7.1 |
| **real-data** | EURUSD + XAUUSD × **1H/4H/1D only**, on `production-native-2026-02_08` |
| **serialisation** | `from_dict(as_dict()) == value` exactly, including `None` |
| **guard / contract** | No hand-rolled observability comparison · no re-import of a detector this layer must consume · no `resample` · no lower-timeframe partition read · no model-training import |
| **streaming / point-in-time** | `batch == prefix == bar-by-bar`; prefix equivalence at every cut |
| **performance** | Measured and reported; never optimised unless correctness requires it |

### 8.1a Two matrices every completion report must contain

Both are **tables, not prose**, so a reviewer can check coverage by eye rather than by
reading the suite.

**Leakage matrix** — one row per L-ID × component, no blank cells:

```
              L1    L2    L3    L4    L5    L6    L7    L8
component A   test  test  test  test  test  test  n/a*  test
component B   ...
                                            * every "n/a" carries a reason
```

**Provenance matrix** — one row per emitted id field:

```
field                 id kind        resolves against        observable-by check
nearest_buy_side_pool_id   pool      LiquidityPicture        pool.confirmation <= as_of
cot.report_id              cot       CotReport registry      release_timestamp <= as_of
h4_context_id              mtf       per-HTF analysis        aligned_htf_close <= as_of
```

A field that appears in `source_ids()` but not in the matrix, or vice versa, is a defect —
that asymmetry is exactly the R2-07 audit's `source_break_id` gap.

### 8.2 Real-data requirements

**The March-2024 fixture is insufficient for this phase and may not be the primary
evidence.** It holds four days, no 4H partition and no 1D partition.

**R2-08.2 already acquired far better evidence than this phase was going to ask for.**

| Dataset | Status | Covers |
|---|---|---|
| **`production-native-2026-02_08`** | ✅ **ON DISK — the primary evidence.** Six contiguous months, 2026-02-01 → 2026-08-01, both symbols × 1H/4H/1D, native-sourced, hashed, manifested, zero download failures | Normal days · weekends · session breaks · provider padding · withheld 4H windows · daily boundaries · **and both DST transitions** |
| `real-2026-07` | ✅ on disk (tick-derived) | Retained; the R2-08 validation month. **Superseded as primary evidence** by the six-month native set |
| `real-2024-03-08_12` | ✅ on disk | Regression fixture only. No 4H, no 1D |
| COT history | ❌ **must be acquired — blocking prerequisite for R2-10-B** | ≥ 3 years before the earliest market observation, for the index/percentile warm-up ([R2-10](features/R2-10-COT-STORY.md) §8) |

Measured contents of the production set:

```
              1H     4H     1D        March-2026 bars (1H / 4H / 1D)
EURUSD      3120    754    156              531 / 128 / 27
XAUUSD      2955    642    155              508 / 110 / 27
```

**The DST prerequisite is already satisfied.** An earlier draft of this document made a
fresh `real-2026-03` backfill a *blocking* prerequisite for R2-11, because 2026-03 is the
only month exercising a New York DST shift (2026-03-08) **and** a European one
(2026-03-29) together — exactly where a day boundary, a session window and an HTF
alignment can all be wrong at once. That month is **inside**
`production-native-2026-02_08`, with 531 EURUSD and 508 XAUUSD hourly bars. **No backfill
is required, and R2-11 is unblocked on data.**

Six contiguous months also removes a limitation this phase would otherwise have inherited:
R2-08's per-pair target parameters were derived from the single month they were validated
on, and the completion reports flag that they *"should be re-derived from a longer history
before any modelling claim rests on them"*. The same applies to R2-09's pool tolerances
(§13 **G1**), which are derived by the same method — and six months is now available to
derive them from. **R2-09 must derive its tolerances from the full six-month set, not from
July alone**, and say so.

The validation must exercise, each as an explicitly named test: normal days · weekend
closure · **DST transition** · missing bars · provider gaps · withheld 4H windows ·
session breaks · incomplete data at both dataset edges · daily boundaries.

### 8.3 Performance — measure, do not optimise

Each story reports, and none optimises unless correctness requires it:

- analysis time per bar (batch)
- total batch time on one fresh month, per symbol × timeframe
- streaming/prefix replay time
- MTF alignment cost per row
- COT lookup cost per row
- feature-vector construction cost per row

Two figures are already known and are the baseline: **state construction ≈ 2 ms/instant**
and **`UnicornDetector.analyse` ≈ 25 s for 2933 1M bars** (HANDOFF open items 3 and 4). On
the production timeframes the bar counts are two orders of magnitude smaller, which is why
neither has been optimised.

Hotspots are identified in the completion report. Optimising one is a separate story with
a benchmark.

---

## 9. Evidence versus interpretation

This is the governing editorial rule of R2-12 and R2-13, stated here because it applies to
all five stories.

```
EVIDENCE          a measured, reproducible fact about the market
                  "the nearest untaken buy-side pool is 43 points away and holds 3 levels"

INTERPRETATION    a claim about what evidence means
                  "price is likely to run that pool"
```

**Evidence is preserved in full. Interpretation is minimised, named and always
separable.** Concretely:

- Raw counts, distances, prices and ids are always exposed.
- No feature is a "score", "strength", "quality" or "confidence" unless a source defines
  it — none does.
- The existing four-source `bias` count is **frozen exactly as it is**. R2-12 does not
  extend it, reweight it or add a second verdict. Two live bias definitions would force
  every downstream result to name which one produced it, and the first person to forget
  makes the results unreproducible — the R2-06 lesson
  ([R2-06-CONCEPT-MAP.md](ict/R2-06-CONCEPT-MAP.md) §4).
- `UNKNOWN`, `NEUTRAL`, `BULLISH`, `BEARISH` stay four distinct values in the state. The
  vector's documented lossy collapse of `unknown`/`neutral` to `0` is unchanged and is
  **not** extended to any new field.

---

## 10. Machine-learning compatibility

The v2 representation must be directly usable by XGBoost, LightGBM and, later, by Kronos
or another sequence model. **No training happens in this phase.**

| Requirement | How it is met |
|---|---|
| Fixed, ordered, numeric columns | `FEATURE_NAMES_V2`; the first 56 entries are `FEATURE_NAMES` verbatim |
| Stable categorical encodings | Declared module constants, never fitted from observed values |
| Missing distinguishable from zero | `None` in dicts, `NaN` in rows; real zeros stay zeros |
| No target leakage into features | Module boundary, already enforced by R2-08's guard tests |
| No future in normalisation | §6.5 |
| Sequence-model friendly | Rows are per-bar, ordered by `as_of`, one row per bar close; this phase introduces no new gaps |
| Interpretable at Phase 4 | Every column's unit, range, missing meaning and provenance are documented **before** implementation, so no later phase re-interprets the data |

---

## 11. Recommended implementation order

| # | Story | Prerequisites | Hard stop |
|---|---|---|---|
| 1 | **R2-09** Liquidity Model | R2-04, R2-06 approved (they are) | ✋ audit → report → **approval required** |
| 1b | *R2-10-A COT acquisition* | R2-09 approved; runs in the background | folded into R2-10's stop |
| 2 | **R2-10** COT Positioning Model | R2-10-A data on disk; package-placement approval (CLAUDE.md rule 13) | ✋ audit → report → **approval required** |
| 3 | **R2-11** Multi-Timeframe Context | R2-09 complete (data prerequisite already met) | ✋ audit → report → **approval required** |
| 4 | **R2-12** Market State v2 | R2-09, R2-10, R2-11 complete | ✋ audit → report → **approval required** |
| 5 | **R2-13** Feature Vector v2 | R2-12 complete | ✋ audit → report → **approval required** |

Rationale beyond the dependency graph:

- **R2-09 first** — pure computation over data already on disk, no new provider, no new
  data, and it is the layer R2-11 and R2-12 both consume.
- **R2-10 second** — resolve its long-lead external dependency and its approval-gated
  package placement before two composition stories depend on them.
- **R2-11 third** — the highest-leakage-risk story in the phase; it deserves a settled
  foundation and a DST month underneath it.
- **R2-12 then R2-13** — a projection cannot precede the thing it projects.

---

## 12. Hard stops

Each story ends with an explicit, non-negotiable stop:

```
R2-NN implemented -> full suite green -> ruff + black clean
                  -> self-audit -> completion report -> COMMIT (local only)
                  -> STOP. No push. No next story until explicit approval.
```

**No story starts automatically.** A completed story does not authorise the next one; only
the project lead does. This mirrors the strict-order rule already recorded in
[HANDOFF.md](dev/HANDOFF.md): *"one story completed and validated before the next
begins."*

Each stop requires the completion report to state: what changed, what did not, test
counts, static-analysis result, measured performance, every ambiguity encountered, every
assumption made, every alternative rejected, and every known limitation.

---

## 13. Global ambiguity register

Per-story registers live in each story document. This table holds only the ambiguities
that cut across more than one story.

| # | Ambiguity | Interpretations | Chosen | Why | Kind |
|---|---|---|---|---|---|
| **G1** | What "liquidity pool tolerance" means numerically — ICT never states a number | absolute points · pips · % of price · ATR multiple · fraction of measured bar range · per-instrument constant | **Per (symbol, timeframe) constant derived from measured median bar range**, in the shape of `PRODUCTION_TARGET_PARAMETERS` | It is the method this repository already uses for the only other magnitude it had to choose (R2-08 target barriers); it is evidence-backed rather than invented; and it refuses ATR, which would smuggle a volatility model into a representation layer — an absence the engine states explicitly | **Engineering** |
| **G2** | Whether "liquidity taken" means wick-through or close-through, and how a *touch* is represented | wick · close · both; new `TOUCHED` state · reuse `APPROACHED` | **Wick-through is `SWEPT`** (R2-04's definition, unchanged); `closed_beyond` still distinguishes break-through from rejection. **`APPROACHED` = TOUCHED** — R2-04's existing state IS "touched"; R2-09 **enables** it (R2-04 ships it disabled) and **counts** it as `approach_count`. **No `TOUCHED` enum is added and `APPROACHED` is not renamed** | Redefining `SWEPT` would change a shipped feature's meaning. An earlier draft proposed a fifth `TOUCHED` state; that is **VOID** — two names for one event is the duplication the engine forbids, and counting an existing state is additive where adding a state is not | **Doctrine + engineering — directed** |
| **G3** | Whether the nearest liquidity is the market's target | it is · it is not · unknown | **Unknown. Exposed as distance features only** | ICT says price *seeks* liquidity; it does not say price seeks the *nearest* liquidity. Encoding "nearest = target" would be inventing doctrine, and it is exactly the kind of claim Phase 4 exists to test | **Doctrine — refused** |
| **G4** | Which daily boundary governs (00:00 UTC / 17:00 NY / 00:00 NY) | three live definitions | **All three stay, unreconciled** | Reconciling them is an architectural decision (production_universe.md §2), not an implementation detail; doing it inside a feature story would silently change what a `1D` bar is | **Engineering — deferred, recorded** |
| **G5** | Whether COT (a futures datum) may describe a spot instrument | yes, directly · yes, as an approximation · no | **Yes, as an explicit approximation**, recorded as an assumption on every normalised value, with the source family preserved in provenance | Spot FX has no reported positioning. Futures positioning is the only publicly reported positioning that exists for these instruments. Pretending the mapping is exact would be the error | **Engineering assumption** |
| **G6** | Whether forming (incomplete) HTF bars may be exposed | yes · no · yes-if-labelled | **No, in v1** | The repository has precedent for labelled running state (`RunningSessionState`, `PendingPeriod`) but has never fed it into `ICTMarketState`, and the brief's default favours completed information. Recorded as the leading v2 candidate | **Engineering** |
| **G7** | Whether `bias` should absorb the new layers | extend it · add a second, separately-named verdict · freeze it | **Freeze the original; add `liquidity_context` / `cot_context` / `htf_context` as evidence; an OPTIONAL, clearly-distinct `extended_bias` is permitted** | §9. The original four-source bias keeps its exact meaning and range, so no shipped feature changes. `extended_bias` is a *different, separately-named* field — **DIRECTED BY THE REVISED BRIEF**, which supersedes this document's earlier refusal of any second verdict | **Engineering — directed** |
| **G8** | Whether v2 replaces or extends v1 | replace · parallel class · extend | **Extend, append-only** | A prefix invariant (`as_row()[:56]` identical to v1) is mechanically testable; a parallel class forces every consumer to branch and doubles the surface that can drift | **Engineering** |
| **G9** | Which COT report families ship | Legacy only · Legacy + others · **modern families only, normalised** | **Legacy is EXCLUDED entirely.** The appropriate modern family per market — **TFF** for currencies, **Disaggregated** for metals — normalised into a common `NormalizedCOTContext` | **DIRECTED BY THE BRIEF**, superseding two earlier drafts (Legacy-only, then Legacy-as-spine). Commonality comes from the **normalisation layer**, not from forcing raw families to be identical. Legacy may not be used as a spine, a fallback, or to fill a missing value. See [R2-10](features/R2-10-COT-STORY.md) §3 | **Engineering — directed** |
| **G10** | Whether a positioning "extreme" flag may exist | no, a threshold is a hypothesis · yes, configurable · yes, fixed | **Yes — `extreme_flag`, configurable, with the raw index and percentile always exposed beside it** | **DIRECTED BY THE REVISED BRIEF**, superseding this document's earlier refusal. The concern stands and is recorded rather than dropped: a threshold *is* a hypothesis. It is mitigated by making the threshold configuration, never a literal; by shipping the continuous `historical_rank` alongside so no information is lost; and by documenting the flag as a **convention, not doctrine** | **Engineering — directed, concern recorded** |
| **G11** | Whether "liquidity significance" can be defined deterministically | it cannot, refuse it · a composite of measurable attributes · a single invented score | **A composite of measurable attributes, every component exposed separately** | **DIRECTED BY THE REVISED BRIEF**, superseding this document's earlier refusal. ICT defines no significance rule, so the components are engineering — but each is independently measurable (pool cardinality, level-type rank, age, internal/external, distance) and the composite is configurable and optional. **No component is a prediction** | **Engineering — directed** |

**Nothing in this phase invents an ICT rule.** Where the source material is silent, the
silence is recorded and the decision is labelled *engineering*, not *doctrine*.

---

## 14. What this phase explicitly does not build

Stated so that each absence is a decision rather than an oversight.

Model training · feature selection · scalers or normalisation fitted on data · class
balancing · imputation · walk-forward or expanding-window validation · a holiday calendar
· a new ICT detector · internal-range liquidity as a *detector* (it is a classification of
existing levels) · order-flow, depth-of-book or volume-profile data · sentiment · news · a
second bias definition · a "setup quality" score · a NY-anchored daily timeframe ·
resolving same-bar TP/SL ambiguity from a lower timeframe · backtesting · execution ·
Kronos · any claim of predictive value.
