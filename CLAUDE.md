# ICT-Kronos — Project Context (read first)

A **scientifically testable quantitative research platform**. Its purpose is to answer one question:

> Can deterministic ICT market-structure information provide incremental predictive power when combined with a financial foundation model (Kronos) and classical ML, over simpler baselines?

**Do not assume the answer is yes.** A rigorous negative answer is a successful outcome. The deliverable is the research infrastructure and the evidence — not a trading bot.

## Read first, every cycle

1. **[docs/dev/HANDOFF.md](docs/dev/HANDOFF.md)** — shared dev memory. Read before starting; update before opening the PR, in the same PR. If it isn't in HANDOFF.md, assume the next person won't know it.
2. **[docs/financial-ai/IMPLEMENTATION_ROADMAP.md](docs/financial-ai/IMPLEMENTATION_ROADMAP.md)** — phase order and exit gates.
3. **[docs/dev/adr/](docs/dev/adr/)** — decisions of record.

## Hard facts

- **Language:** Python 3.12+ (developed on 3.14). No .NET, no TypeScript.
- **Origin:** patterns are ported from `Learnexia/python/curriculum_intelligence` (DB-outbox worker, mock-by-default factories, env-only frozen-dataclass config, Testcontainers contract tests). Provenance is recorded in [ADR-0001](docs/dev/adr/0001-repo-placement.md). This repo is **independent** — it does not import from, deploy with, or share a database with Learnexia.
- **Persistence:** immutable Parquet for market data and datasets; Postgres for the job outbox, experiment registry, and model registry.
- **Data source:** Dukascopy free historical tick data (EURUSD, XAUUSD).

## Non-negotiable rules

1. **No look-ahead leakage.** Every feature must be computable from information available at prediction time and nothing else. Any ICT concept needing future candles to confirm it must model the **exact confirmation timestamp** separately from the event timestamp. Every feature-producing change ships a leakage test. This rule outranks every other consideration — a leak silently invalidates all downstream results.

2. **ICT detection is deterministic.** Swing structure, BOS, MSS, FVG, liquidity, premium/discount, sessions — pure, testable algorithms. **Never** an LLM deciding whether a candle contains a pattern.

3. **The LLM boundary.** No LLM output may enter a feature vector, a model input, a prediction, or a decision. LLMs consume results; they never produce them. Allowed: research orchestration, experiment planning, interpreting results, generating reports, natural-language querying, developer assistance. Forbidden: anything on the path to a trade decision.

4. **No hardcoded trading assumptions.** Thresholds, weights, session times, kill zones, spreads, commissions, slippage, R-multiples, setup-quality weights — all configuration, never literals in logic.

5. **Never claim predictive certainty.** In code, comments, reports, or generated narration. Outputs are probabilistic and always carry their validation period and sample size.

6. **Chronological splits only.** Never shuffle a financial time series. Expanding-window / walk-forward validation; the out-of-sample period is touched once.

7. **Raw data is immutable.** Never overwrite raw OHLCV. ICT features live separately from raw candles.

8. **Reproducibility.** Every experiment records `experiment_id`, dataset version, feature version, model version, parameters, train/validation/test periods, metrics, backtest metrics, and git commit. A backtest must be reproducible from its identifiers alone.

9. **Mock by default.** Every expensive backend (Kronos/PyTorch, live data vendors, LLM providers) sits behind an interface selected by an env enum in a `factory.py`, defaults to a deterministic mock in dev + CI, imports the heavy dependency **lazily inside the live branch**, and degrades to the mock with a loud warning when credentials or weights are missing. CI never installs the heavy extras and never touches the network.

10. **Secrets are env-only.** Never hardcoded, never committed. Log presence/absence, never values.

11. **Model accuracy is not trading performance.** Never report one as the other.

12. **Tests alongside implementation.** Never silently skip a failing test.

13. **Design patterns — ask first.** Mirror existing shapes in this repo. If a task genuinely calls for a new abstraction, name it and get approval before implementing.

## Timestamp conventions (load-bearing)

- All timestamps are **UTC and timezone-aware**. There are no naive datetimes anywhere in the codebase.
- A candle's `timestamp` is its **open time**. A bar timestamped `10:00` on the 1H timeframe covers `[10:00, 11:00)`.
- A higher-timeframe bar is **not observable** until its close. At time *t*, the most recent *usable* 4H bar is the last one whose close is `<= t`.
- Session and DST logic converts from UTC at the point of use; it never mutates stored timestamps.

## Exit gate for every phase

Run tests → run static analysis (`ruff check .`, `black --check .`) → report changed files → report test results → report known limitations → identify the next phase.

## Layout

```
ict_kronos/
├── app/        # config, db (outbox), logging, health — ported from curriculum_intelligence
├── domain/     # Symbol, Timeframe, MarketCandle — canonical schema
├── data/       # providers (factory: fixture | dukascopy), normalizer, resampler
├── storage/    # immutable Parquet store + dataset version manifest
├── ict/        # (Phase 2) deterministic detectors
├── features/   # (Phase 3) feature pipeline, dataset builder
├── research/   # (Phase 4+) models, ablations, experiment tracking
├── kronos/     # (Phase 5) Kronos integration behind a factory
├── backtest/   # (Phase 7) event-driven simulator
└── workers/    # outbox poll lanes
```
