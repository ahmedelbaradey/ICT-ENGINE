# HANDOFF — shared dev memory

**Protocol (ported from Learnexia):** read this before starting work; update it before opening your PR, in the same PR. Prune what has gone stale. If it isn't here, assume the next person won't know it.

Last updated: **2026-08-19** — end of Phase 1.

---

## Where we are

| Phase | Status |
|---|---|
| 0 — Reconnaissance | ✅ Complete — `docs/financial-ai/` (4 documents) |
| 0.5 — Foundation | ✅ Complete |
| **1 — Market data layer** | ✅ **Complete — 179 tests passing, ruff + black clean** |
| 2 — ICT engine | ⬜ Next |
| 3–10 | ⬜ Not started |

---

## Decisions taken (do not re-litigate without an ADR)

1. **Separate repository**, reusing Learnexia's patterns rather than its runtime — [ADR-0001](adr/0001-repo-placement.md). Learnexia is **read-only** to this project; nothing here deploys with it or shares its database.
2. **Dukascopy free tick data** is the live market-data source (EURUSD, XAUUSD).
3. **The six "agents" of the master plan are deterministic services on job lanes**, not LLM agents. Revisit at Phase 9 only with evidence of need.
4. **Kronos claims are UNVERIFIED.** The "512-candle context" figure, the checkpoint names, the output shape, and the license all come from the master plan and have not been checked against the upstream repo. Phase 5 starts with a verification task, and `KronosConfig.max_context_bars` stays configurable until then.

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

## Gotchas found in Phase 1

- **Dukascopy months are zero-indexed in the URL** (`January = 00`) but the local cache path uses real months for human readability. `bi5_url()` centralises this; `test_bi5_url` pins it. Getting it wrong fetches *the wrong month*, which is far worse than a 404.
- **Dukascopy prices are integers scaled by `10 ** price_precision`** — 5 for EURUSD, 3 for XAUUSD. Treating XAUUSD like a 5-decimal FX pair scales every price by 100×.
- **An empty/404 hourly tick file is a closed market, not a failure.** Conflating them makes the gap report meaningless.
- **pandas 3.0 rejects `pd.NA` in a `float64` Series.** Use `np.nan` for unmatched numeric context (this bit the empty-HTF path in `align_htf_context`).
- **`resample(require_complete=True)` is the default and should stay that way.** A 1H bar built from 5 of its 12 source bars has a real open and a meaningless high/low/close.

---

## Open items for Phase 2

1. **Session calendar** is not built yet. The normalizer reports *all* gaps without judging which are routine weekend/holiday breaks — that judgement belongs to the Phase 2 session layer, deliberately.
2. **`StorageConfig.raw_root` is defined but unused.** Phase 1 writes only `normalized/`. Decide in Phase 2 whether raw provider output is archived separately before normalization.
3. **The outbox lane is not wired.** `app/db.py` is not ported yet — Phase 1 runs file-only via `IngestPipeline`. Port it when the first long-running lane (features or forecast) actually needs it.
4. **No real market data has been ingested.** Everything so far runs on the 288-bar fixture. Someone must run a live Dukascopy backfill and confirm the 2021–2025 split in Master Plan §20 is actually achievable for both symbols.
5. **Sibling projects not yet inspected**: `e:/Wrokspace/{ForexQuant,NNForTrading,TradingBot,TradingBotV2}` may contain prior work worth reusing or avoiding.
