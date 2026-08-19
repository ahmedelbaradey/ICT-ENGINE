# ICT-Kronos

A **scientifically testable quantitative research platform**, built to answer one question:

> Can deterministic ICT (Inner Circle Trader) market-structure information provide incremental predictive power when combined with a financial foundation model (Kronos) and classical machine learning, over simpler baselines?

**The answer is not assumed to be yes.** The system is designed to discover it. A rigorous negative result is a successful outcome — the deliverable is the research infrastructure and the evidence, not a trading bot.

This project makes no claim to predict markets with certainty. All outputs are probabilistic and are reported with their validation period and sample size.

---

## Status

| Phase | Status |
|---|---|
| 0 — Reconnaissance | ✅ Complete |
| 0.5 — Foundation | ✅ Complete |
| 1 — Market data layer | ✅ Complete |
| **1.5 — Real-data proof** | ✅ **APPROVED — 254 tests, ruff + black clean** |
| 2 — ICT engine | ⬜ Next — `SessionDetector` |
| 3 — Feature dataset | ⬜ |
| 4 — Baseline models | ⬜ |
| 5 — Kronos integration | ⬜ Blocked — no CUDA GPU |
| 6 — Hybrid model | ⬜ |
| 7 — Backtesting | ⬜ |
| 8 — Walk-forward | ⬜ |
| 9 — Orchestration | ⬜ |
| 10 — Dashboard / API | ⬜ |

Roadmap and exit gates: [docs/financial-ai/IMPLEMENTATION_ROADMAP.md](docs/financial-ai/IMPLEMENTATION_ROADMAP.md).
Real-data proof: [docs/financial-ai/DATA_PROOF.md](docs/financial-ai/DATA_PROOF.md) — **approved for Phase 2**.

---

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -e ".[test,dev]"

pytest -q          # the full gate
ruff check .
black --check .
```

Ingest and verify a dataset (offline, from checked-in fixtures):

```bash
python -m ict_kronos.cli ingest \
    --symbol EURUSD --symbol XAUUSD --timeframe 5m \
    --start 2024-03-04 --end 2024-03-05 --version v1

python -m ict_kronos.cli verify --version v1
```

For a live Dukascopy backfill, install the extra and flip the backend:

```bash
pip install -e ".[dukascopy]"
MARKET_DATA_BACKEND=dukascopy python -m ict_kronos.cli ingest \
    --symbol EURUSD --timeframe 5m --start 2024-01-01 --end 2024-02-01 --version v2
```

---

## Design commitments

These are enforced by tests and CI, not merely documented. The full set is in [CLAUDE.md](CLAUDE.md).

**No look-ahead leakage.** Every feature is computable from information available at prediction time and nothing else. A 4H bar timestamped 08:00 is not knowable until 12:00, and `align_htf_context()` — the only multi-timeframe join in the codebase — enforces that by joining on `close_time`, never on the open timestamp. `tests/test_leakage.py` runs as its own CI job and includes a test that demonstrates the wrong answer beside the right one.

**ICT detection is deterministic.** Swing structure, BOS, MSS, FVG, liquidity, premium/discount and sessions are pure, testable algorithms. No LLM ever decides whether a candle contains a pattern.

**LLMs never touch the prediction path.** They interpret results, generate reports and assist developers. No LLM output enters a feature vector, a model input, or a decision.

**Data is immutable and provenanced.** Raw bars are never overwritten; gaps are recorded rather than filled; invalid bars are quarantined rather than repaired. Every dataset version carries a SHA-256 per partition plus the git commit, so `verify` can prove later that a published result still corresponds to the data that produced it.

**Mock by default.** Every expensive backend (Kronos/PyTorch, live data vendors, LLM providers) sits behind an env-selected factory that defaults to a deterministic mock, imports the heavy dependency lazily, and degrades with a warning when misconfigured. CI installs no heavy extras and touches no network.

**Chronological splits only.** Financial time series are never shuffled. Validation is walk-forward; the out-of-sample period is touched once.

---

## Layout

```
ict_kronos/
├── app/        config, logging (health, outbox to follow)
├── domain/     Symbol, Timeframe, MarketCandle — the canonical schema
├── data/       providers (fixture | dukascopy), tick validation, normalizer,
│            resampler, tick→1M backfill, ingest
├── storage/    immutable Parquet store + dataset manifests
└── cli.py      ingest / backfill / verify

docs/
├── financial-ai/   reconnaissance, roadmap, DATA_PROOF, COMPUTE_ENVIRONMENT,
│                LEGACY_RESEARCH
└── dev/            HANDOFF.md (shared memory) + ADRs
```

---

## Provenance

Patterns here are ported from the `curriculum_intelligence` service of [Learnexia](https://github.com/ahmedelbaradey/Learnexia) — env-only frozen-dataclass config, the DB-outbox worker with `FOR UPDATE SKIP LOCKED`, the mock-by-default backend factory, and the `pytest`/`ruff`/Testcontainers posture. The two projects share no runtime, no database and no deployment. The reasoning is recorded in [ADR-0001](docs/dev/adr/0001-repo-placement.md).

---

## Contributing

Read [docs/dev/HANDOFF.md](docs/dev/HANDOFF.md) before starting, and update it in the same PR as your change. Every phase ends with the same gate: run tests, run static analysis, report changed files, report test results, report known limitations, identify the next phase. Never silently skip a failing test.
