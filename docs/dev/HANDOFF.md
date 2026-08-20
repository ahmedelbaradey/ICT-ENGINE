# HANDOFF — shared dev memory

**Protocol (ported from Learnexia):** read this before starting work; update it before opening your PR, in the same PR. Prune what has gone stale. If it isn't here, assume the next person won't know it.

Last updated: **2026-08-20** — R2-05.x specification checkpoint (no code).

---

## Where we are

| Phase | Status |
|---|---|
| 0 — Reconnaissance | ✅ Complete — `docs/financial-ai/` (4 documents) |
| 0.5 — Foundation | ✅ Complete |
| 1 — Market data layer | ✅ Complete |
| 1.5 — Real-data proof | ✅ APPROVED |
| **2 — ICT engine** | 🔵 **In progress — R2-01..R2-05.1 done; R2-05.2..R2-05.9 specified; R2-06 DEFERRED** |
| 3–10 | ⬜ Not started (5 blocked on GPU) |

### Phase 2 stories

| Story | Status |
|---|---|
| R2-01 SessionDetector | ✅ Done — 136 tests |
| R2-02 SwingDetector | ✅ Done — 139 tests |
| R2-03 StructureDetector | ✅ Done — 169 tests |
| R2-04 LiquidityDetector | ✅ Done — 210 tests |
| R2-05 FVGDetector | ✅ Done — 225 tests |
| R2-05.1 TrueDailyOpen | ✅ Done — 250 tests |
| R2-05.2 IFVG | 📋 Spec written — **next to implement** |
| R2-05.3 Order Block | 📋 Spec written |
| R2-05.4 Breaker Block | 📋 Spec written |
| R2-05.5 BPR | 📋 Spec written |
| R2-05.6 RDRB | 📋 Spec written — ⚠ decision required |
| R2-05.7 CISD | 📋 Spec written |
| R2-05.8 CHoCH revision | 📋 Spec written |
| R2-05.9 Unicorn | 📋 Spec written |
| R2-06 PremiumDiscount | ⛔ Deferred until R2-05.9 is approved |
| R2-07 ICT feature integration | ⬜ |

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

## R2-05.x — what the next person needs to know

**Read [`docs/ict/R2-05x-CONCEPT-MAP.md`](../ict/R2-05x-CONCEPT-MAP.md) first.** Eight
composite concepts are specified and none is implemented. The shape of the work changed:
these are mostly *relationships between events that already exist*, so the risk moved
from "did we read the candles right" to "did the composite inherit its sources'
observability". The governing rule:

> a composite's `confirmation_timestamp` is at least the max of its sources'
> confirmations, plus whatever its own trigger requires.

- **Consume upstream detectors, never re-implement them.** Import guards enforce it.
- **Provenance is an id, not a copied geometry** — so "does every source resolve, and is
  it observable no later than the composite?" is mechanically testable.
- **Two shared helpers get built once, in R2-05.2**, and every later story reuses them:
  `assert_provenance_resolves`, `assert_sources_observable_first`.
- **Five decisions are open** (concept map §8). The load-bearing one is **RDRB**: the
  primary source describes a two-candle pattern with the zone drawn from the
  *surrounding* candles, while the wider community describes a three-candle one. They
  are different patterns, not different emphases.
- **CHoCH finding:** the ICT material does not define CHoCH as a distinct algorithm; the
  "early reversal hint" role it gets stretched to fill is, in the source, **CISD's**
  role. R2-05.8 is therefore scheduled after R2-05.7 and may legitimately end with no
  behaviour change.

## Open items for Phase 2

1. **R2-05.2 IFVG is next**, after the specification checkpoint is approved.
2. **R2-06 PremiumDiscount is deferred** until R2-05.9 is independently approved. All the risk is in which dealing range is chosen, so that must be configuration with a documented default.
2. **Swings are unranked.** Many minor pivots at `left=right=2`. `strength` (prominence) is available for filtering, but R2-03/R2-07 will likely need a significance ranking.
3. **No holiday calendar yet.** A bank holiday looks like any other empty window: correctly no occurrence, but the detector cannot say *why*. R2-04 ("previous day") likely forces the issue.
4. **Multi-year data availability is UNCONFIRMED.** Only 4 days are proven. Whether Dukascopy has complete 2021-2025 coverage for both symbols has not been checked, so the Master Plan §20 split is unvalidated. Run a metered background backfill early.
5. **`StorageConfig.raw_root` is defined but unused.** The `.bi5` cache serves as the raw immutable archive. Decide whether decoded ticks are also persisted as Parquet.
6. **The outbox lane is not wired.** `app/db.py` is not ported — everything runs file-only. Port it when a long-running lane actually needs it.
7. **No cross-vendor data comparison.** Zero ticks were rejected, but internal consistency is a weaker claim than agreement with a second source.
