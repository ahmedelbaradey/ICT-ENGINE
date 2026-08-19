# IMPLEMENTATION_ROADMAP — ICT-Kronos

**Date:** 2026-08-19
**Depends on:** `ARCHITECTURE_ANALYSIS.md`, `AGENT_INVENTORY.md`, `INTEGRATION_PLAN.md`
**Decided (2026-08-19):** Placement **Option A** — separate `ICT-ENGINE` repository reusing Learnexia's patterns ([ADR-0001](../dev/adr/0001-repo-placement.md)). Market data source: **Dukascopy** free historical tick data.

---

## Standing rules for every phase

Applied to all phases, per Master Plan §35 and §36:

- **Exit gate (§36):** run tests → run static analysis (`ruff` + `black --check`) → report changed files → report test results → report known limitations → identify the next phase. **Never silently skip a failing test.**
- **Leakage (§19, §35.10):** every feature computed only from information available at prediction time. Every detector carries an explicit `confirmation_timestamp` separate from the event timestamp. Each phase that adds features adds a matching leakage test.
- **Configuration (§35.11):** no hardcoded trading constants. Thresholds, weights, session times, costs — all env/config.
- **Determinism (§35.8):** ICT detection is pure algorithm. No LLM in the detection path.
- **Reproducibility (§28, §29, §35.9):** every experiment records `experiment_id`, dataset version, feature version, model version, parameters, train/validation/test periods, metrics, backtest metrics, git commit.
- **Commits (§35.15):** small, logically separated, on a `feat/<StoryID>` branch, PR per batch. `reviewer` PASS before `committer`.
- **Language (§35.12):** never claim predictive certainty in code comments, reports, or LLM narration.

---

## Phase 0 — Repository reconnaissance ✅ COMPLETE

**Delivered:** `ARCHITECTURE_ANALYSIS.md`, `AGENT_INVENTORY.md`, `INTEGRATION_PLAN.md`, `IMPLEMENTATION_ROADMAP.md` (this file), all in `docs/financial-ai/`.

**Key outcomes:** no runtime agent framework exists (reinterprets §25); the `curriculum_intelligence` service is the reuse template; no ML stack exists anywhere.

**Blocking before Phase 1:** the six open questions in `INTEGRATION_PLAN.md` §8 — critically Q1 (placement) and Q3 (market data source).

---

## Phase 0.5 — Foundation (new; ~0.5 day, mostly file copies)

Not in the original plan, but Option A requires the skeleton to exist before Phase 1 code has a home.

**Deliverables:**
- `git init`; `.gitignore`, `.gitattributes`.
- `CLAUDE.md` for this repo — non-negotiables: LLM boundary (§26 / `INTEGRATION_PLAN.md` §4), leakage rule, no-hardcoded-constants, no-certainty-claims, deterministic ICT.
- `pyproject.toml` — lean core + `[ml]` / `[kronos]` / `[test]` / `[dev]` extras; `ruff` (E,F,I,B,UP, line 110) + `black`.
- `app/{config,db,logging,health}.py` ported from `curriculum_intelligence`.
- `docker/docker-compose.yaml` (postgres + worker), `Dockerfile`.
- `.github/workflows/ci.yml` — `ruff check .` + `pytest -v`, mock backends, heavy extras excluded.
- `docs/dev/HANDOFF.md` + `.claude/settings.json` SessionStart hook.
- `docs/dev/adr/0001-repo-placement.md` — records the Option A decision and its provenance from Learnexia's ADR-0004.
- `.claude/agents/` — `analyzer`, `planner`, `reviewer`, `committer` (ported); `quant-python`, `leakage-auditor` (new); `qc-test-designer` (retargeted).

**Gate:** CI green on an empty test suite; `docker compose up` reaches a healthy worker.

---

## Phase 1 — Market data layer (§32 Phase 1) — **the only phase to implement now**

**Scope:** `MarketDataProvider`, `MarketCandle`, `Timeframe`, `Symbol`, `DataNormalizer`.

**Deliverables:**

1. **Canonical schema (§12):** `MarketCandle` = `timestamp, symbol, timeframe, open, high, low, close, volume`. Timestamps **UTC, tz-aware**, stored as the bar's **open time**, with the convention documented explicitly. `Symbol` and `Timeframe` as enums with a defined ordering.
2. **`MarketDataProvider` protocol** + factory: `MARKET_DATA_BACKEND=fixture` (default, deterministic CSV/Parquet fixtures, no network) | `<vendor>` (live, lazily imported, degrades to fixture with a warning). Mirrors `parsers/factory.py`.
3. **`DataNormalizer`:** deduplicate, sort, validate OHLC invariants (`low <= min(open,close)`, `high >= max(open,close)`), detect and **record** gaps rather than silently filling them, normalize timezone/DST, enforce a monotonic index.
4. **Storage:** immutable raw Parquet partitioned `symbol/timeframe/year`. Raw data is **never overwritten** (§12). A `dataset_version` manifest with per-file checksums (§29).
5. **Resampling:** lower→higher timeframe aggregation (5M→15M→1H→4H→D) with an explicit, tested bar-boundary convention — this is the foundation of Phase 3's multi-timeframe alignment and a prime leakage site.

**Tests (§27):**
`test_timestamp_alignment`, `test_ohlc_invariants`, `test_gap_detection`, `test_dst_transition_handling`, `test_resample_boundaries`, `test_no_lookahead_in_resample` (an aggregated higher-TF bar must never be visible before its close), `test_dedup_and_sort`, `test_raw_data_immutable`, and a Testcontainers contract test for the outbox `ingest` lane.

**Exit gate:** all tests pass; `ruff`/`black` clean; EURUSD + XAUUSD at 1H/15M/5M ingested and normalized; a dataset-version manifest exists; changed files, test results, and limitations reported.

**Explicitly NOT in Phase 1:** any ICT detector, any feature, any model, any Kronos code.

---

## Phase 1.5 — Real-data proof ✅ COMPLETE

Not in the original plan; inserted before Phase 2 to prove the pipeline on genuine data rather than fixtures.

**Delivered:** [DATA_PROOF.md](DATA_PROOF.md) — **APPROVED for Phase 2**. Real Dukascopy ticks for EURUSD + XAUUSD over `2024-03-08 → 2024-03-12` (a window containing a full weekend closure and the US DST transition): 436,332 ticks, **0 rejected**, **0 download failures**, 7,355 bars across 1M/5M/15M/1H, **0 resample mismatches**, **0 leakage violations**, manifest hash verification PASS.

Also delivered: [COMPUTE_ENVIRONMENT.md](COMPUTE_ENVIRONMENT.md) (no CUDA GPU — Phase 5 deferred, Phases 2–4 unaffected) and [LEGACY_RESEARCH.md](LEGACY_RESEARCH.md) (prior projects inspected read-only; no code reused).

**Carried into Phase 2:**
- **Multi-year data availability is still unconfirmed.** The §20 split (2021–2025) has not been validated against the provider. Settle this early with a metered background backfill.
- Dukascopy serves **one connection at a time** at ~0.12–0.44 s/file warm; concurrency is refused outright. A full-year backfill is a long, resumable background job.

---

## Phase 2 — ICT engine (§5–§11)

**MVP scope only (§31):** Swing structure, BOS, MSS, FVG, Liquidity, Premium/Discount, Sessions. Order Blocks and the rest of §8 are deferred.

**Development order (fixed):**

1. **`SessionDetector` first** — Asian / London / New York, then London and NY Kill Zones. Timezone- and DST-aware, all boundaries configurable. Everything else depends on session context.
   - **First acceptance case, from real data:** after the 2024-03-08 weekend closure, EURUSD reopened at **2024-03-10 21:00 UTC** while XAUUSD reopened at **22:00 UTC** — the US DST transition shifting the session open in UTC terms, differently per instrument. The detector must reproduce this. See [DATA_PROOF.md §3.1](DATA_PROOF.md).
   - It also owns the judgement the normalizer deliberately withholds: which absences are weekends/holidays and which are data faults.
2. **Swing detection** — swing high, swing low.
3. **Market structure** — HH / HL / LH / LL, BOS, MSS (and CHoCH, displacement).
4. **Liquidity** — equal highs/lows, PDH/PDL, PWH/PWL, session high/low, internal vs external, sweep detection with `sweep_timestamp`.
5. **FVG** — bullish/bearish, size, age, fill %, invalidation, validity.
6. **Premium/Discount** — dealing range, equilibrium, premium/discount, position within range.

### Detector contract (§14) — binding for every detector

Every detection emits at minimum:

```
event_timestamp         confirmation_timestamp   timeframe    symbol
direction               event_type               price_level  strength
reference_level
```

`confirmation_timestamp` is **the first moment the event was actually knowable** — never the open of a candle whose close the event depends on. A 3-candle FVG is confirmed at `candle3.close_time`, not `candle3.timestamp`.

This is not theoretical: `ForexQuant/…/FvgDetectionService.cs` carries a comment reading *"FIXED: Changed from candle2 to candle3 to exclude formation candles"* — someone hit this exact bug in production, and the fix is **still one bar early**. Recorded in [LEGACY_RESEARCH.md §5.1](LEGACY_RESEARCH.md).

**Naming note:** ForexQuant's `SessionManagementService` / `UserSession` / `SessionSettings` are **login-session** code with no trading-session logic anywhere. Use unambiguous names (`TradingSession`, `KillZone`) so the collision cannot recur.

**Tests (§16 — coverage must not drop):** every detector needs normal, edge, malformed-input, boundary, timeframe, timestamp and leakage cases. Specifically: session boundaries, DST, candle close, HTF alignment, swing confirmation, MSS confirmation, liquidity-sweep confirmation, FVG confirmation.

**Streaming-replay requirement (§15):** every detector must eventually satisfy `batch(history) == incremental(bar-by-bar)`, because the same engine serves historical research, backtesting and live inference. The property already holds for resampling and MTF alignment on real data.

**Exit gate:** every detector tested; leakage audit PASS; detector outputs versioned as `feature_version`.

---

## Phase 3 — Feature dataset (§11, §32 Phase 3)

`ICTFeatureVector`, `FeaturePipeline`, `DatasetBuilder`. Multi-timeframe alignment (D/4H/1H/15M/5M/1M) exposing HTF context to every LTF observation — **strictly point-in-time**. Target definition (§17): primary `TP 2R before SL 1R`, plus secondary targets (future return, MFE, MAE, time-to-TP/SL).

**Tests:** `test_multi_timeframe_alignment`, plus a dedicated suite proving HTF context at bar *t* uses only HTF bars **closed at or before** *t*. Target labelling gets its own leakage test (labels legitimately use future data; **features must not**).

**Exit gate:** a versioned training dataset for EURUSD + XAUUSD; class balance and feature distributions reported; chronological split boundaries fixed and recorded (§20).

---

## Phase 4 — Baseline models (§32 Phase 4)

Logistic Regression → Random Forest → XGBoost → LightGBM, on **chronological** splits with expanding-window/walk-forward validation. Never shuffle (§20).

**This is where the §22 ablation harness is built** — Models A (OHLCV) and B (OHLCV + technical) land here, so that C/D/E have a real baseline to beat. Experiment tracking (§28) becomes operational.

**Exit gate:** Model A and B results on identical out-of-sample periods, with confidence intervals — not point estimates.

---

## Phase 5 — Kronos (§32 Phase 5)

**Blocked on compute.** [COMPUTE_ENVIRONMENT.md](COMPUTE_ENVIRONMENT.md) establishes there is no CUDA-capable GPU on this machine (AMD + Intel integrated only; `nvidia-smi` and `nvcc` absent). Research-scale batch forecasting is not viable on this CPU, so Phase 5 is deferred pending a CUDA environment. **Phases 2, 3, 4, 6 (fusion mechanics), 7 and 8 are unaffected**, and Models A/B/C — which answer the core ICT question — need no GPU at all.

**Starts with a verification task, not code.** Confirm against `https://github.com/shiyu-coder/Kronos` and its model cards: available checkpoints, the actual maximum context length (the §14 "512 candles" claim is **unverified**), the forecast output shape and sampling parameters, and the license for both code and weights. Record the findings in an ADR before integrating.

Then: integrate pretrained **Kronos-small** behind `KRONOS_BACKEND=mock|local` (mock default; torch imported lazily). Rolling context window respecting the *verified* limit. Persist the full §15 output including multiple sampled paths — do not collapse to a single price.

**Evaluate Kronos independently first** (§32 Phase 5) before any fusion. Model D (OHLCV + Kronos) lands here.

**Exit gate:** Kronos forecasts reproducible from `(context_hash, sampling_params, model_version)`; standalone Kronos value quantified against Models A/B.

---

## Phase 6 — Hybrid model (§16, §32 Phase 6)

Feature fusion → XGBoost. Model C (OHLCV + ICT) and Model E (OHLCV + ICT + Kronos) complete the §22 matrix.

**Exit gate — this is the phase that answers §34.** All five models compared on identical out-of-sample periods, with statistical significance testing and the number of configurations tried reported. A negative result here is a **valid and valuable outcome** and must be reported as such (§34: "do not assume the answer is yes").

---

## Phase 7 — Backtesting (§21)

Event-driven simulator with spread, commission, slippage, and realistic fills. Full metric set (§21). Model accuracy is **never** reported as trading performance.

## Phase 8 — Walk-forward (§20)

Rolling re-fit and re-test. Robustness across periods, instruments, and parameter perturbation (§33). This is the phase that decides whether an apparent edge is real.

## Phase 9 — Orchestration integration (§32 Phase 9)

Only if Phases 1–8 produce a reliable pipeline. Per `INTEGRATION_PLAN.md` §5, this means wiring the deterministic services into job lanes — **not** building an LLM agent framework. Any runtime LLM workflow needs its own ADR with demonstrated need.

## Phase 10 — Dashboard / API (§24, §32 Phase 10)

Expose current market state, ICT state, Kronos forecast, probability, expected move, setup quality, trade decision, historical performance. **The `NO TRADE` output is a first-class response** (§24) and must be tested as such.

---

## Success criteria (§33) — restated as gates

Success is **not** accuracy. The system is successful only if the edge survives:

- positive out-of-sample expectancy on a period touched once
- stable profit factor across walk-forward windows
- controlled maximum drawdown
- robustness across instruments (EURUSD **and** XAUUSD)
- robustness under parameter perturbation
- realistic costs applied
- a clean leakage audit
- statistical significance, corrected for the number of configurations tried

**A rigorous negative answer to §34 is a successful project outcome.** The deliverable is the research infrastructure and the evidence, not a profitable bot.

---

## Sequencing summary

| Phase | Status | Gate to proceed |
|---|---|---|
| 0 — Reconnaissance | ✅ Complete | — |
| 0.5 — Foundation | ✅ Complete | CI green ✅ |
| 1 — Market data | ✅ Complete | All Phase 1 tests pass ✅ |
| **1.5 — Real-data proof** | ✅ **Complete — [DATA_PROOF.md](DATA_PROOF.md), APPROVED** | Real Dukascopy data validated ✅ |
| **2 — ICT engine** | **Next — starts with `SessionDetector`** | Detector tests + leakage audit PASS |
| 3 — Feature dataset | After 2 | Point-in-time proof |
| 4 — Baselines | After 3 | Models A + B measured |
| 5 — Kronos | After 4 — **blocked on a CUDA environment** | Kronos claims **verified**; Model D measured |
| 6 — Hybrid | After 5 | **§34 answered** with significance testing |
| 7 — Backtest | After 6 | Realistic-cost metrics |
| 8 — Walk-forward | After 7 | Robustness across §33 dimensions |
| 9 — Orchestration | After 8 | Pipeline reliable |
| 10 — Dashboard/API | After 9 | `NO TRADE` path tested |
