# HANDOFF — shared dev memory

**Protocol (ported from Learnexia):** read this before starting work; update it before opening your PR, in the same PR. Prune what has gone stale. If it isn't here, assume the next person won't know it.

Last updated: **2026-08-19** — end of R2-01 (SessionDetector).

---

## Where we are

| Phase | Status |
|---|---|
| 0 — Reconnaissance | ✅ Complete — `docs/financial-ai/` (4 documents) |
| 0.5 — Foundation | ✅ Complete |
| 1 — Market data layer | ✅ Complete |
| 1.5 — Real-data proof | ✅ APPROVED |
| **2 — ICT engine** | 🔵 **In progress — R2-01 done; R2-02 next** |
| 3–10 | ⬜ Not started (5 blocked on GPU) |

### Phase 2 stories

| Story | Status |
|---|---|
| R2-01 SessionDetector | ✅ Done — 136 tests |
| R2-02 SwingDetector | ⬜ **Next** |
| R2-03 StructureDetector | ⬜ |
| R2-04 LiquidityDetector | ⬜ |
| R2-05 FVGDetector | ⬜ |
| R2-06 PremiumDiscount | ⬜ |
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

## Open items for Phase 2

1. **R2-02 SwingDetector is next.** The confirmation lag (`right` bars) is the whole point — a swing at bar *i* is not knowable until bar `i + right` closes.
2. **No holiday calendar yet.** A bank holiday looks like any other empty window: correctly no occurrence, but the detector cannot say *why*. R2-04 ("previous day") likely forces the issue.
3. **Multi-year data availability is UNCONFIRMED.** Only 4 days are proven. Whether Dukascopy has complete 2021-2025 coverage for both symbols has not been checked, so the Master Plan §20 split is unvalidated. Run a metered background backfill early.
4. **`StorageConfig.raw_root` is defined but unused.** The `.bi5` cache serves as the raw immutable archive. Decide whether decoded ticks are also persisted as Parquet.
5. **The outbox lane is not wired.** `app/db.py` is not ported — everything runs file-only. Port it when a long-running lane actually needs it.
6. **No cross-vendor data comparison.** Zero ticks were rejected, but internal consistency is a weaker claim than agreement with a second source.
