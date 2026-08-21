# HANDOFF — shared dev memory

**Protocol (ported from Learnexia):** read this before starting work; update it before opening your PR, in the same PR. Prune what has gone stale. If it isn't here, assume the next person won't know it.

Last updated: **2026-08-21** — end of R2-08.2. Production data is provider-native 1H/1D with 4H from four native 1H bars; ticks and minute data are not a production dependency.

Previous: **2026-08-20** — end of R2-08. **Phase 2 is COMPLETE** (R2-01 … R2-07). **R2-08 — the prediction target and dataset engine — is ready for review**: `ict_kronos/features/`, the hard gate before any model training.

---

## Where we are

| Phase | Status |
|---|---|
| 0 — Reconnaissance | ✅ Complete — `docs/financial-ai/` (4 documents) |
| 0.5 — Foundation | ✅ Complete |
| 1 — Market data layer | ✅ Complete |
| 1.5 — Real-data proof | ✅ APPROVED |
| **2 — ICT engine** | ✅ **Complete — R2-01..R2-07** |
| **3 — Feature dataset** | 🟨 **R2-08 ready for review** — targets, dataset rows, chronological splits, quality audit |
| 4–10 | ⬜ Not started (5 blocked on GPU) |

### Phase 2 stories

| Story | Status |
|---|---|
| R2-01 SessionDetector | ✅ Done — 136 tests |
| R2-02 SwingDetector | ✅ Done — 139 tests |
| R2-03 StructureDetector | ✅ Done — 169 tests |
| R2-04 LiquidityDetector | ✅ Done — 210 tests |
| R2-05 FVGDetector | ✅ Done — 225 tests |
| R2-05.1 TrueDailyOpen | ✅ Done — 250 tests |
| R2-05.2 IFVG | ✅ Done |
| R2-05.3 Order Block | ✅ Done |
| R2-05.4 Breaker Block | ✅ Done |
| R2-05.5 BPR | ✅ Done |
| R2-05.6 RDRB | ✅ Done — FOUR-candle definition |
| R2-05.7 CISD | ✅ Done |
| R2-05.8 CHoCH revision | ✅ Reviewed — no behaviour change |
| R2-05.9 Unicorn | ✅ Done — Breaker ∩ same-polarity FVG, 56 tests |
| R2-06 PremiumDiscount | ✅ Done — dealing range + premium/discount, 310 tests |
| R2-07 ICT feature integration | ✅ Done — ICTMarketState + ICTFeatureVector (56 features) |
| R2-08 Target & dataset engine | 🟨 Ready for review — `ict_kronos/features/`, the second half of the temporal contract |

Stories in [user-stories/](../../user-stories/README.md), tasks in [tasks/](../../tasks/README.md).
**Strict order** — one story completed and validated before the next begins.

---

## Decisions taken (do not re-litigate without an ADR)

1. **Separate repository**, reusing Learnexia's patterns rather than its runtime — [ADR-0001](adr/0001-repo-placement.md). Learnexia is **read-only** to this project; nothing here deploys with it or shares its database.
2. **Dukascopy free tick data** is the live market-data source (EURUSD, XAUUSD).
3. **The six "agents" of the master plan are deterministic services on job lanes**, not LLM agents. Revisit at Phase 9 only with evidence of need.
4. **Kronos claims are UNVERIFIED.** The "512-candle context" figure, the checkpoint names, the output shape, and the license all come from the master plan and have not been checked against the upstream repo. Phase 5 starts with a verification task, and `KronosConfig.max_context_bars` stays configurable until then.
5. **No CUDA GPU on this machine** — see [COMPUTE_ENVIRONMENT.md](../financial-ai/COMPUTE_ENVIRONMENT.md). Phase 5 is deferred; Phases 2-4 and 6-8 are unaffected. Do NOT install `[kronos]`/`[ml]` extras until the phase that needs them.
6. **No legacy code is reused.** `ForexQuant`, `NNForTrading`, `TradingBot` were inspected read-only — see [LEGACY_RESEARCH.md](../financial-ai/LEGACY_RESEARCH.md). Concepts transfer; code does not.
7. **Work intake adopts Learnexia's convention**, not its files: `user-stories/` + `tasks/` markdown with the ask-the-lead-first rule. No second task system, and Learnexia is untouched.
8. **The Phase 2 detector contract is fixed** — [`ict/contract.py`](../../ict_kronos/ict/contract.py). `confirmation_timestamp` is the earliest instant an event was knowable, and the constructor refuses any event confirmed before it occurred. Every detector uses it; do not invent a per-detector variant.
9. **Sessions are defined in LOCAL time, never UTC.** That is what makes DST automatic. Overridable via `ICT_SESSIONS_JSON`.
10. **Swings use the n-bar fractal definition**, chosen because its confirmation lag is BOUNDED and streamable (ZigZag/ATR variants are not). `right >= 1` is enforced — a zero-lag pivot is not a swing.
11. **`filter_observable()` / `assert_observable()` / `is_observable_at()` are THE one observability gate.** They accept any record carrying `confirmation_timestamp` (events, liquidity levels, sweeps) via the `Confirmable` protocol. **Never hand-roll `x.confirmation_timestamp <= t`** — a source-level test enforces this for liquidity, and the same rule binds every future detector.
12. **BOS and MSS are ONE detection distinguished by prior state**, not two algorithms. **CHoCH is a synonym for MSS by default and is not emitted** — see `docs/ict/structure.md` §5. The `DISTINCT_BY_DISPLACEMENT` policy is the only alternative offered, and it is off by default.
13. **Structure break mode defaults to CLOSE.** In WICK mode the bar that prints a higher swing high necessarily breaks the previous one, so nearly every HH also emits a BOS.
14. **A liquidity LEVEL is not a liquidity SWEEP** — separate types, separate timestamps, never collapsed.
15. **The trading day is 17:00 America/New_York**, not the UTC calendar day. It matches the observed instrument reopen times and is expressed as a `SessionDefinition` so DST is automatic. The trading week is the Sunday…Thursday day windows.
16. **Liquidity side is fixed at creation and never flips** as price moves. Only the status changes.
17. **FVG confirmation is candle 3's CLOSE, never its open.** The condition reads C3's low/high, which is not final until close. `formation_timestamp` and `confirmation_timestamp` are two required fields — the legacy ForexQuant detector has one, set to C3's open, and is a full bar early.
18. **FVG candles must be CONTIGUOUS in time** (`require_contiguous_bars=True`). Across a weekend or data gap the price jump would manufacture a large, entirely fictitious imbalance.
19. **True Daily Open (00:00 NY) and the trading-day boundary (17:00 NY) are DIFFERENT CONCEPTS.** One is a price level, the other a period delimiter, and they disagree about which date an instant belongs to between 17:00 and midnight. R2-05.1 changed nothing about R2-04's 17:00 logic and they have no shared default.
20. **True Daily Open confirms with ZERO lag, and that is correct.** It reads a bar's `open`, which is fixed at the bar's first print — unlike every other Phase 2 detector, which reads a price that is not final until the bar closes. Waiting for the close would publish known information a bar late.

---

## Load-bearing conventions (getting these wrong breaks results silently)

### Timestamps

- Everything is **UTC and timezone-aware**. Naive datetimes are rejected at every boundary (`require_utc`, `MarketCandle.__post_init__`, `validate_frame`).
- A candle's `timestamp` is its **OPEN time**. The bar covers `[timestamp, timestamp + duration)`.
- **`close_time` is the observability anchor** — the first instant a bar's data may be used. `resample()` and `with_close_time()` attach it; nothing else may.

### The leakage rule

`align_htf_context()` is **the only** multi-timeframe alignment helper. It joins on `close_time`, never on `timestamp`. Anything joining on `timestamp` leaks the future.

`tests/test_leakage.py` includes `test_naive_join_on_open_timestamp_would_leak`, which demonstrates the wrong answer beside the right one — read it before touching alignment.

The strongest guard is the **streaming-replay property**: a value computed from full history must equal the same value computed by replaying bars one at a time. Phase 2 must apply this property to every ICT detector.

### Data handling

- **Gaps are recorded, never filled.** Forward-filling fabricates price action that ICT detectors read as real structure.
- **Invalid bars are quarantined, never repaired.** Repairing means inventing a price.
- **Empty tick periods produce no bar**, rather than a synthetic flat bar.
- **Parquet partitions are immutable.** A second write raises `ImmutableWriteError`; `overwrite=True` is an explicit, loudly-logged opt-in.
- **Dataset manifests are immutable** and carry a SHA-256 per partition plus the git commit. `ManifestStore.verify()` re-hashes and reports `CHANGED:`/`MISSING:`.

### Backends

Every expensive backend is mock-by-default, env-selected in a `factory.py`, lazily imported, and degrades to the mock with a warning when misconfigured. `MARKET_DATA_BACKEND=fixture`, `KRONOS_BACKEND=mock`, `LLM_BACKEND=mock`. CI never installs heavy extras and never touches the network.

---

## How to run

```bash
# One-time
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[test,dev]"     # Windows
# source .venv/bin/activate && pip install -e ".[test,dev]"    # POSIX

# The gate
pytest -q
ruff check .
black --check .

# Live Dukascopy backfill (opt-in — needs the extra + network)
pip install -e ".[dukascopy]"
MARKET_DATA_BACKEND=dukascopy python -m ict_kronos.cli ingest --symbol EURUSD --timeframe 5m \
    --start 2024-01-01 --end 2024-02-01 --version v1
```

**Developed against Python 3.14 / pandas 3.0.5 / pyarrow 25 / numpy 2.5.** `pyproject.toml` declares `>=3.12`; CI runs 3.12 to keep the floor honest. If you see a pandas 3 deprecation, fix it rather than pinning back.

### Regenerating fixtures

`python tests/fixtures/generate_fixtures.py` — deterministic (fixed seed), 288 five-minute bars per symbol for 2024-03-04. **Do not regenerate casually**: several tests hold hand-computed expectations against this exact data.

---

## Real data (Phase 1.5)

`data/` is **gitignored** — reproduce it, never commit it:

```bash
export DATA_ROOT="e:/Wrokspace/ICT-ENGINE/data"
export DUKASCOPY_CACHE="e:/Wrokspace/ICT-ENGINE/data/cache/dukascopy"
export MARKET_DATA_BACKEND=dukascopy
python -m ict_kronos.cli backfill \
    --symbol EURUSD --symbol XAUUSD \
    --start 2024-03-08 --end 2024-03-12 --version real-2024-03-08_12
python -m ict_kronos.cli verify --version real-2024-03-08_12
```

Full results: [DATA_PROOF.md](../financial-ai/DATA_PROOF.md). A re-run against the warm cache takes ~1.3 s and does **no** network I/O.

## Gotchas (Phase 1 + 1.5)

- **Dukascopy prices are integers scaled by `10 ** price_precision`** — 5 for EURUSD, 3 for XAUUSD. Treating XAUUSD like a 5-decimal FX pair scales every price by 100x.
- **pandas 3.0 rejects `pd.NA` in a `float64` Series.** Use `np.nan` for unmatched numeric context (this bit the empty-HTF path in `align_htf_context`).
- **`resample(require_complete=True)` is the default and should stay that way.** A 1H bar built from 5 of its 12 source bars has a real open and a meaningless high/low/close.
- **Dukascopy answers HTTP 503 to a non-browser User-Agent.** A browser UA on the identical URL returns 200. `BROWSER_HEADERS` in `dukascopy.py` is load-bearing; without it every download fails and looks like an outage.
- **Dukascopy months in the URL are ZERO-indexed** (January = `00`). Getting it wrong silently fetches the wrong month rather than 404ing.
- **The feed serves ONE connection at a time.** Measured: sequential + warm session = 0.12-0.44 s/file; 4 or 8 concurrent workers = **0/8 succeeded**, actively refused. `TickBackfill.max_workers` defaults to 1 deliberately — raising it makes the backfill fail, not go faster.
- **A cold TLS connection costs 15-26 s.** The backfill originally created a new thread pool per day, discarding the warm connection every 24 files. Keeping one connection alive across the whole run is worth roughly a 50x speedup. Never wrap the single-worker path in a pool.
- **An empty/404 hourly file is a closed market, not a failure.** Conflating them makes the gap report meaningless.
- **Total volume falls as timeframe rises.** Expected: `require_complete=True` drops higher-TF bars not backed by a full complement of source bars. Volume is conserved exactly *within* retained bars.
- **Aggressive probing gets the IP throttled** (~20 s/file for a while afterwards). Be a polite client.

## Gotchas found in R2-01

- **PEP 495 order matters.** BOTH nonexistent (spring-forward) and ambiguous (fall-back) local times have differing `utcoffset()` between `fold=0` and `fold=1`. A fold comparison alone cannot tell them apart — the **UTC round-trip** is what discriminates. Checking ambiguity first mislabels every spring-forward boundary. (Caught by test.)
- **The London Kill Zone is 2 hours, not 3, on US spring-forward day.** It starts at 02:00 New York, which does not exist on 2024-03-10. Real, flagged `NONEXISTENT`, and pinned by test. Do not "fix" it.
- **A session is only emitted once its window has fully elapsed within the observed data.** Without that rule the still-open session looks complete and batch disagrees with streaming replay.
- **Bar membership is fully-contained**, not "opens inside". Otherwise a bar straddling the close could set a session extreme from out-of-session price action.

## Gotchas found in R2-02

- **The fractal window is POSITIONAL over bars present, not wall-clock.** Across a market gap the confirmation lag exceeds `(right+1) * bar_duration`. Real example: an EURUSD pivot at Fri 2024-03-08 21:55 confirms Sun 2024-03-10 21:15 — a lag of **1d 23:20**. Correct (the confirming bars did not exist), but do NOT compute expected latency as `right * duration`.
- **`series.rolling(n).max().shift(-right)` is easy to get off by one.** `reference_pivots()` is a deliberately naive reference kept in the module purely so tests can prove the fast path. Keep it.
- **Plateaus are common in real data** — especially XAUUSD, which quantises hard. The tie policy is therefore load-bearing, not a nicety. Default `FIRST` gives exactly one swing per plateau at the earliest confirmable timestamp.

## Gotchas found in R2-03

- **Swings are absorbed BEFORE the break check**, deliberately. If a newer, higher pivot confirms on the same bar that would have broken an older lower level, the newer pivot becomes the reference and there is no break — because price had already exceeded the old level at that pivot. Reversing the order reports breaks of levels price passed long ago. See `docs/ict/structure.md` §3.
- **In WICK mode the bar forming a higher swing high also breaks the previous high** (its high exceeds it by construction). That collapses "formed a pivot" into "broke a level", and is the main reason CLOSE is the default.
- **A reference level is consumed on break** — one level cannot break twice. A second push needs a genuinely new confirmed swing.
- **Equal swing levels get NO HH/HL/LH/LL label** but still become the active reference. Equal highs are liquidity (R2-04), not structure; labelling them here would pre-empt that story.
- **Insufficient history for displacement ⇒ CHoCH** under the distinct policy. Conservative by design: without evidence of displacement we do not claim the stronger label.

## Gotchas found in R2-04

- **The Friday-anchored day window must NOT be in the trading week.** It runs Fri 17:00 -> Sat 17:00, starting exactly when the week ends, so folding it in let a "week high" post-date the week's close. Real FX data hides this (no bars there); synthetic 24/7 data exposed it. Guarded by `_in_trading_week()`.
- **`require_rejection` was removed deliberately.** It created a state where a level was consumed but no event explained it. Every penetration emits a sweep carrying `closed_beyond` / `is_rejection`; filtering belongs downstream.
- **A wick through a level is a SWEEP, not a BOS.** R2-03 defaults to CLOSE breaks precisely so the two do not collide. Tested on real data.
- **One bar can sweep many levels** — 11 in one real EURUSD bar. Each emits its own sweep event; identities are never collapsed even at identical prices.
- **Approach tracking is off by default** (`approach_tolerance_points=None`). Three states carry the meaning; APPROACHED is an optional refinement.

## Gotchas found in R2-05

- **The legacy FVG bug, precisely:** `StartTime = candle3.Timestamp` (the OPEN) while the condition reads `candle3.Low`. One field, one bar early. Our two-field design plus the contract's `confirmation >= event` invariant make it unrepresentable — proved by `TestTheLegacyOffByOne` (5 tests), which runs the legacy filter beside ours.
- **Contiguity is load-bearing, not decoration.** Without it every data gap manufactures a phantom FVG. On real EURUSD 5m the guard suppresses several zones, each verified to span an actual time gap.
- **This weekend leaves no naive phantom.** EURUSD reopened within a few points of the Friday close, so *that* boundary produces no gap either way; the phantoms appear at the shorter intra-week data gaps. Assert the general claim, not the weekend-specific one.
- **A genuine absence of FVGs is a valid result.** XAUUSD's nine 4H bars overlap throughout and yield zero zones. Do not assert non-empty on sparse timeframes.
- **C3 cannot fill its own gap** — it defines the zone. Filling starts from the next bar.

## Gotchas found in R2-05.1

- **Never assert a constant UTC hour for a local-time boundary.** `00:00 NY` is `05:00Z` under EST and `04:00Z` under EDT. An assertion on the UTC hour passes for eight months a year and encodes exactly the bug the local-time definition prevents. The invariant is `local_time().time() == 00:00`.
- **Landing on the timeframe grid is necessary but NOT sufficient.** The bar must also survive the resampler. `2024-03-11 04:00Z` is a valid 4H grid point for both instruments, but EURUSD is missing one of the sixty 1m bars in that hour, so `require_complete=True` drops the 1H and 4H bars upstream and no level follows. XAUUSD, with all sixty, produces one. Same rule, one layer earlier.
- **4H under EST can never carry a True Daily Open** — 05:00 is not on the 00/04/08/12/16/20 grid — and 1D never can, being anchored to UTC midnight.
- **`latest_at` means "most recent", not "today's".** On a date with no boundary bar it returns the previous date's level, correctly labelled with its own `trading_date`. Detection carries nothing forward; the query is a convenience over what was found, and the caller must check the date.
- **The Phase 1.5 window happens to contain both DST cases and a weekend**, so the spring transition and closure behaviour are validated on real bars. The autumn transition has no real-data coverage and is synthetic only.

## Gotchas found in R2-05.2

- **Composite identity must include provenance.** Two real defects, both caught by a
  uniqueness test on real data, not by reasoning: several FVGs can invert on the SAME
  bar (121 zones → 105 ids), and several Order Blocks can fail on the same bar
  (215 breakers → 110 ids). A timestamp alone is not an identity for a composite.
- **CISD's state machine must run in TRIGGER order, not leg order.** A short later leg
  can trigger before a long earlier one resolves; walking the machine in leg order
  makes batch disagree with replay.
- **`confirmed_within` / `later_confirmed` are NOT observability checks** but look
  identical in source. They live in `composites.py` so the source-level guard over the
  six detector modules can stay strict.
- **Mitigation is not inversion, and mitigation is not invalidation.** Fills come from
  bar *extremes*; inversion and OB failure need a *close*. Keeping them separate is
  what makes IFVG and Breaker meaningful rather than restatements of a fill.
- **An Order Block confirms LATER than its own candle** (2+ bars on real 5m data). It
  is the only detector in the engine so far whose event and confirmation are separated
  by an unbounded number of bars.
- **A doji belongs to no run.** Stated explicitly in both `order_blocks.py` and
  `cisd.py`, because leaving it implicit turns "contiguous group" into a judgement call.
- **Test fixtures need dojis as leading context.** An ordinary up/down candle before
  the pattern under test forms its own valid Order Block and quietly doubles the
  expected count.

## Gotchas found in R2-05.9 (Unicorn)

- **The Unicorn's cardinality rule makes it OUTNUMBER its own inputs.** 849 Breakers on
  EURUSD 1m produce 3081 Unicorns, because several gaps routinely overlap one Breaker
  and none of them is collapsed. That is the specified behaviour, not a bug — but
  anyone reading counts should expect it, and `max_bars_from_breaker` (default 50) is
  the knob that governs it. The source calls the overlap "rare"; on 1m bars it is not.
- **`event_timestamp` is `max(component event timestamps)`, NOT "whichever confirmed
  second".** A component can form later and still confirm first, so the two readings
  disagree. The relationship is not on the chart until both halves of it are drawn.
- **A Unicorn's death is its Breaker's death, read out of `BreakerAnalysis.fills`.**
  `unicorn.py` evaluates no price condition to decide invalidation. Recomputing it
  would be a second implementation of a rule that is already written down once.
- **A Unicorn can be born INVALIDATED** when its Breaker died before the second
  component confirmed. It is still emitted — suppressing it would hide a real event
  behind a lifecycle state — and consumers filter on status.
- **INVALIDATED outranks MITIGATED in reporting**, following `OrderBlockAnalysis`.
  Both are terminal; the second names the cause.
- **The `require_structure_break` default makes the Unicorn's inputs sparse.** With the
  gate ON (the default) EURUSD 1H yields 3 Unicorns and XAUUSD 1H yields 0. The
  real-data suite runs the ungated Breaker so the geometry is exercised everywhere.

## Gotchas found in R2-08.2 (production data architecture)

- **Production candles are provider-NATIVE, not tick-derived.** Dukascopy publishes
  native 1H (`BID_candles_hour_1.bi5`) and native 1D (`BID_candles_day_1.bi5`), one file
  per month. It publishes **no** native 4H — `hour_4` and `min_240` both 404 — so 4H is
  built from exactly four native 1H bars and from nothing else. Ticks and 1M/5M/15M are
  not a production dependency. The tick lane still exists for the 2024-03 research
  fixture and is not on the production path.
- **Dukascopy month numbering is ZERO-BASED in the URL.** July is `06`. Get it wrong and
  you silently fetch the previous month; every downstream number still looks plausible.
- **The provider pads closed periods with fabricated candles.** A shut hour is present in
  the native file as a flat zero-volume bar carrying the prior close forward — 195 of 744
  EURUSD hourly records in July 2026, including every hour of Saturday. Using native
  candles "as-is" would feed forward-filled prices into the feature pipeline. They are
  identified (`volume == 0` AND `O == H == L == C`), dropped and counted: 1224 for EURUSD
  and 1389 for XAUUSD over six months. Dropping them restores the absence the market had.
- **A 4H bar needs four 1H bars — a proven closure does not change that.** Proving *why*
  an hour is absent explains the gap; it does not restore the hour. Three traded hours
  labelled `4h` would be a different candle wearing the same name, so a proven closure
  gets its own withheld disposition rather than being emitted. Four dispositions, each
  window in exactly one: EMITTED / WITHHELD_BOUNDARY / WITHHELD_MARKET_CLOSED /
  WITHHELD_UNDETERMINED.
- **The XAUUSD 21:00 hour is the dominant withheld cause and stays UNDETERMINED.** Gold
  traded at 21:00 only 24 times in six months, but it *did* trade, so the conservative
  session profile cannot prove a closure — and 128 XAUUSD 4H windows are withheld as a
  result. That is the rule working, not failing. Do not add a heuristic to "fix" it.
- **State construction, not detection, is the production bottleneck.** EURUSD 1H, 3120
  bars: detect 79 s, **state 661 s**, features 0.09 s, targets 0.57 s — 237 ms/row. Cost
  grows with accumulated events, so exhaustive prefix replay over a 3120-bar 1H series is
  quadratic and infeasible; 1H streaming is sampled deterministically and reported as
  sampled.
- **A quadratic scan is easy to write and invisible until real volume.** The first 4H
  builder answered "was this hour observed?" by rescanning the whole 1H index per window
  — 806 x 3120. A cached six-month ingest took >90 s; answering from a set built once
  takes 0.3 s.

## Gotchas found in R2-08 (targets + dataset)

- **The temporal boundary has to be a MODULE boundary, not a convention.** `FEATURES(T)`
  may only see up to `T`; `TARGET(T)` must see past it. Written as two functions in one
  file, the next edit merges them by accident. `ict_kronos/features/targets.py` imports
  nothing from the feature layer, the feature layer imports nothing from it, and guard
  tests assert both directions. `DatasetBuilder` is the only place they meet.
- **A leakage test with no control proves nothing.** "Features unchanged when the future
  is mutated" passes just as happily against a layer that computes nothing. Every
  inertness assertion is paired with a history mutation that MUST change the features,
  and with a target that MUST move.
- **The exact-threshold rule was quietly false before rounding.** `(1.0002 - 1.0) / 1e-5`
  is `19.999999999997797`, so a move of exactly 20 points classified NEUTRAL under a
  20-point threshold — the one place the `>=` rule is stated is the one place it broke.
  Points are rounded to 6 decimals (a million times finer than any instrument can
  express) before comparison.
- **20 points on EURUSD is 0.0002, not 0.002.** Half the first TP/SL fixtures were off by
  a decade and produced same-bar double touches instead of the clean races they were
  meant to describe. When a barrier test fails, suspect the fixture before the engine.
- **Same-bar TP + SL is genuinely unanswerable.** An OHLC bar records four prices and no
  sequence. "Use the close to break the tie" is a fabrication that a model then learns
  as if it were the market. It is `UNRESOLVED`, with the offending bar's timestamp kept
  so the ambiguity is countable.
- **The split embargo cannot be left to the caller.** A split with `embargo_bars=0` beside
  an 8-bar target leaks the next period into training with no error and no NaN — only a
  validation score that is too good. `DatasetSpec` refuses the combination and names the
  value to use, rather than silently widening it.
- **Building rows for every instant of a real 1m frame is not a test, it is a timeout.**
  The real-data suite caches one dataset AND its engine per (symbol, timeframe); prefix
  replay, which is quadratic, runs on 1H/4H only. Feature-side prefix equivalence at 15m
  and finer is R2-07's guarantee and is covered there.
- **Resolve a target as soon as the answer exists.** A TP/SL race decided at bar `i+2`
  is resolved even if the 16-bar horizon runs off the end of the data — bars that do not
  exist cannot change an outcome that already happened. Requiring the full horizon would
  have thrown away correct labels.

## Gotchas found in R2-07 (state + feature vector)

- **`0` and UNKNOWN must never be conflated, and it is easy to.** `buy_side_count == 0`
  means "no resting liquidity"; `nearest_buy_side_points is None` means "nothing to
  measure to". Emitting `0` for both tells a model price is sitting ON a level that
  does not exist. `as_dict()` → `None`, `as_row()` → `nan`, counts stay real zeros.
- **`TrueDailyOpenDetector.boundary_for` takes a `date` and returns a TUPLE**
  `(instant, anomaly)`. Passing a datetime and comparing against the tuple silently
  yields `False` forever — the staleness flag was wrong until caught. The trading date
  comes from `as_of.astimezone(detector.config.zone).date()` instead.
- **Never call a detector's convenience query inside a per-instant loop.**
  `latest_at()` and `session_state_at()` re-run detection on every call; using them per
  bar made state construction 8x slower than needed. Levels and session windows are
  resolved ONCE per frame and filtered through the gate per instant.
- **The real-data suite needs a per-(symbol, timeframe) analysis cache.** Running all
  eleven detectors over a 2933-bar 1m frame is tens of seconds (Unicorn's lifecycle
  pass dominates); re-running it inside every test blew a 15-minute timeout. The
  analyses are pure functions of the frame, so caching the INPUTS changes nothing a
  test observes.
- **A source guard must strip docstrings, not just comments.** Both new modules name
  the things the guards ban in order to warn against them, so a raw-text guard flags
  its own warning. There is a test asserting the stripper still returns code.
- **A "too short for anything" fixture is harder to build than it looks.** Three
  *rising* candles with non-overlapping wicks form a perfectly valid bullish FVG. The
  empty fixture has to be flat.
- **`ICTFeatureVector` collapses `unknown` and `neutral` bias to `0`** — a deliberate,
  documented lossy projection. `ICTMarketState` keeps them apart, and that is the place
  to read if the distinction matters.
- **A NaN inside a record silently breaks equality, and equality is a load-bearing
  assertion here.** R2-06 returns `math.nan` for a degenerate range's position; carried
  into the state it broke `from_dict(as_dict()) == v` and would have made a
  batch-vs-prefix comparison report a streaming difference that has nothing to do with
  the market. NaN now belongs to `as_row()` and nowhere else — one missing-value
  convention, not two. Found by the R2-07 audit, not by the fixture: the detector never
  produces a degenerate range.
- **A provenance enumeration is only as good as its coverage test, and a value-based
  test is not one.** `source_ids()` omitted the dealing range's `source_break_id` for
  the whole story, and no test noticed because that id usually *equals*
  `latest_break_id`. The test that catches it stamps each field with a unique marker
  and asks whether the FIELD is read — not whether the value appears somewhere.

## Gotchas found in R2-06 (dealing range)

- **The range high is the BROKEN structural level, not the highest price traded.** It
  cannot be the running extreme without an unconfirmed pivot. The measured consequence:
  **42–81% of real observations have `percentage_position` outside `[0, 1]`.** That is
  correct and documented, but anyone consuming the number must expect out-of-range
  values as the *common* case, not the exception.
- **The range definition is deliberately NOT configurable.** Five candidates were
  evaluated (`docs/ict/R2-06-CONCEPT-MAP.md`); one is implemented. Two live definitions
  would force every downstream result to name which one produced it. A test asserts
  the config surface has no range-definition knob.
- **EQUILIBRIUM is a band, never `==`.** Default half a tick — finer than the
  instrument can express, so it means "at equilibrium" while staying float-safe.
  Testing *exactly* on the band edge is unreliable (`(x + b) - x != b` in binary
  floating point), which is the reason the band exists; test just inside and outside.
- **A degenerate range returns `position = NaN`, and `zone` is still defined**, because
  classification compares against equilibrium instead of dividing by width. Returning
  0.5 would have been a lie.
- **A source guard that scans raw text will flag the module docstring.** `dealing_range.py`
  names `frame["high"].max()` in order to warn against it, so the guard strips
  docstrings as well as comments — and there is a test asserting the stripper works,
  because a stripper that returned nothing would make every guard vacuous.
- **`close_time` is timezone-aware; `np.datetime64` silently drops the tz** and then
  refuses to compare. Segment masks stay in pandas. The resulting boolean array from a
  pandas comparison is read-only, so `&=` needs an explicit `.copy()` first.
- **`swing_point_id` joins `structure_break_id` in `composites.py`.** R2-02/R2-03 are
  approved and are not modified to add id fields; consumers derive ids from values the
  records already carry.

## R2-05.x — what the next person needs to know

**Read [`docs/ict/R2-05x-CONCEPT-MAP.md`](../ict/R2-05x-CONCEPT-MAP.md) first.** All
eight composite concepts are now specified AND implemented (CHoCH deliberately as "no
change"). The shape of the work changed at R2-05.2: these are mostly *relationships
between events that already exist*, so the risk moved from "did we read the candles
right" to "did the composite inherit its sources' observability". The governing rule:

> a composite's `confirmation_timestamp` is at least the max of its sources'
> confirmations, plus whatever its own trigger requires.

- **Consume upstream detectors, never re-implement them.** Import guards enforce it,
  and `unicorn.py` is the proof they scale: it reaches three levels down (FVG, Breaker,
  and transitively the Order Block) with no duplicated geometry at all.
- **Provenance is an id, not a copied geometry** — so "does every source resolve, and is
  it observable no later than the composite?" is mechanically testable.
- **Two shared helpers get built once, in R2-05.2**, and every later story reuses them:
  `assert_provenance_resolves`, `assert_sources_observable_first`.
- **Two definitions were overridden by the project lead** and are authoritative:
  **Order Block** = last opposing candle/group **closed through** (an FVG is never
  required), and **RDRB** = **four candles** with C2's wick protected and confirmation
  at C4's close. The concept map's original proposals for both are superseded.
- **CHoCH finding:** the ICT material does not define CHoCH as a distinct algorithm; the
  "early reversal hint" role it gets stretched to fill is, in the source, **CISD's**
  role. R2-05.8 is therefore scheduled after R2-05.7 and may legitimately end with no
  behaviour change.

## Open items for Phase 2

1. **Phase 2 is complete; Phase 3 (feature dataset) is next.** `ICTFeatureVector` is the input it consumes; `feature_version` (`r2-07.1`) ties a dataset to its definitions.
2. **Multi-timeframe assembly is still unbuilt and is now the largest known gap.** The R2-07 brief directed a timeframe-local story, so HTF context was deliberately not implemented. `align_htf_context()` — which joins on `close_time`, never `timestamp` — remains the only sanctioned join, and `ICTMarketState` needs no restructuring to accept it.
3. **State construction costs ~2 ms per instant** (~190 ms for 190 15m bars end to end). Building a state for every bar of a multi-year 1m dataset would be hours; Phase 3 should either sample or push the loop down. Reported, not optimised.
4. **`UnicornDetector.analyse` is now the engine's slowest call** — ~25 s for 2933 1m bars, because `track_zone_fill` runs a Python loop per zone and there are thousands of Unicorns. `detect` alone is ~2.9 s. No correctness impact; it also makes the real-data suite noticeably slower (556 tests, ~7 min). Vectorising the fill scan is the fix, and it belongs to a performance story with a benchmark.
5. **The IFVG detector is the engine's second hotspot** — 3.3 s for 2933 1m bars, via `window.iterrows()` per zone. No correctness impact; reported by the R2-05.2 audit and deliberately not optimised. The fix is a vectorised numpy comparison when it matters.
6. **Swings are unranked.** Many minor pivots at `left=right=2`. `strength` (prominence) is available for filtering, but R2-03/R2-07 will likely need a significance ranking.
7. **No holiday calendar yet.** A bank holiday looks like any other empty window: correctly no occurrence, but the detector cannot say *why*. R2-04 ("previous day") likely forces the issue.
8. **Multi-year data availability is UNCONFIRMED.** Only 4 days are proven. Whether Dukascopy has complete 2021-2025 coverage for both symbols has not been checked, so the Master Plan §20 split is unvalidated. Run a metered background backfill early.
9. **`StorageConfig.raw_root` is defined but unused.** The `.bi5` cache serves as the raw immutable archive. Decide whether decoded ticks are also persisted as Parquet.
10. **The outbox lane is not wired.** `app/db.py` is not ported — everything runs file-only. Port it when a long-running lane actually needs it.
11. **No cross-vendor data comparison.** Zero ticks were rejected, but internal consistency is a weaker claim than agreement with a second source.
