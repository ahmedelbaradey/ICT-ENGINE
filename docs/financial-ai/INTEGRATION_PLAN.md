# INTEGRATION_PLAN — ICT-Kronos ↔ Learnexia

**Date:** 2026-08-19
**Depends on:** `ARCHITECTURE_ANALYSIS.md`, `AGENT_INVENTORY.md`
**Answers:** Master Plan §2 Step 3 ("determine where the financial system belongs"), §35.1–35.7, §36.6.

---

## 1. What the Master Plan asked vs. what reconnaissance found

| Master Plan instruction | Reality | Resolution |
|---|---|---|
| §35.2–35.3 "Reuse existing agents / orchestration" | No runtime agent framework exists (`AGENT_INVENTORY.md` §0) | Reuse the **dev-time** subagent roster to *build* the system. Do not build a runtime agent framework. |
| §35.4 "Reuse existing configuration patterns" | Excellent, directly applicable (env-only frozen dataclasses) | **Adopt as-is.** |
| §35.5 "Reuse existing logging/testing patterns" | Excellent (`ruff`/`black`/`pytest`/Testcontainers; stdout structured logging) | **Adopt as-is.** |
| §35.6 "Do not duplicate infrastructure" | The DB-outbox worker pattern is the infrastructure worth not duplicating | **Copy the pattern.** Whether the *instance* is shared is the §2 decision. |
| §35.7 "Keep financial domain isolated" | Learnexia is an Arabic K-12 education product — zero domain overlap | The decisive constraint. See §2. |

---

## 2. THE PLACEMENT DECISION (open — blocks Phase 1)

Three viable homes. This is the one question that must be answered before any Phase 1 code is written, because it determines the language, the persistence, the CI, and the deployment target of everything downstream.

### Option A — Separate repository (`ICT-ENGINE`), reusing Learnexia's *patterns* — **RECOMMENDED**

The financial system is its own repo (the current working directory), which **copies** Learnexia's proven skeleton: the `python/curriculum_intelligence` service shape, ADR format, HANDOFF protocol, story/task intake, `.claude/agents/` roster, `ruff`/`black`/`pytest`/Testcontainers, and the DB-outbox pattern (against its *own* Postgres).

**For:**

- **Dependency isolation is decisive.** ICT-Kronos needs PyTorch + numpy/pandas/sklearn/xgboost/lightgbm — a multi-GB footprint. Learnexia's Python service today is 4 lean packages, and its authors *deliberately* quarantined heavy deps behind an unused `[live]` extra. Putting torch in that tree contradicts an explicit, documented design posture and inflates the education product's image, CI time, and attack surface.
- **Zero domain overlap.** Master Plan §35.7 and Learnexia's own module-isolation rule both push the same way. A `Trading` module sitting beside `Gamification` and `Parent` in a children's education monolith is a boundary violation in spirit even if it compiles.
- **No risk to Learnexia** (§35.16, §35.17). No shared `.sln`, `Program.cs`, `Directory.Packages.props`, or CI file to serialize edits against.
- **Independent release cadence, licensing, and deployment.** Kronos weights carry their own license; a research platform iterates on a different rhythm than a production education app.
- **Learnexia's CI would grow a multi-GB Python job** that has nothing to do with the education product, slowing every unrelated PR.

**Against:**

- Patterns are copied, not shared — drift is possible. Mitigated by recording provenance in each ADR.
- Duplicated scaffolding effort (compose, CI, config helpers). Small: roughly a day, and mostly literal file copies.
- Loses direct `IAiGateway` injection for §26. Mitigated: use the Anthropic SDK directly behind a factory — **the precedent Learnexia's own `claude_extractor.py` already sets**.

### Option B — New bounded context inside Learnexia (`backend/src/Modules/Markets` + `python/ict_kronos/`)

A `Markets` module (Api/Application/Domain/Infrastructure, own `markets` schema) plus a sibling Python service, meeting at a `markets."PipelineJobs"` outbox — the exact ADR-0004 shape.

**For:** maximum literal reuse (Host, DI, `ILoggerManager`, `BaseResponse<T>`, migrations, `IAiGateway` injection, existing compose/CI, the full agent roster unmodified). Strictly follows §35.1–35.6 as written.

**Against:** every "Against" in Option A's "For" list. Also forces .NET on a domain whose entire toolchain is Python, meaning the .NET module would be a thin API/persistence shell over Python that does all the real work — carrying the monolith's constraints for little benefit.

**When B wins:** if ICT-Kronos is meant to become a *feature of the Learnexia product* (e.g. a financial-literacy learning module) rather than an independent research platform. Nothing in the Master Plan suggests that — §37 describes a standalone quantitative research platform.

### Option C — Separate repo, shared Postgres instance

Option A's code layout, but pointing at Learnexia's Postgres in a `markets` schema.

**Against:** couples two products' runtime availability and backup/restore for no gain, and puts a heavy analytical workload (bulk OHLCV scans, feature builds) on the database serving a live education app. **Not recommended.**

### Recommendation

**Option A**, with the pattern-provenance rule below. The working directory is already `e:/Wrokspace/ICT-ENGINE`, which is consistent with A.

> **This decision needs the lead's explicit sign-off before Phase 1 begins.** It is exactly the kind of decision `CLAUDE.md` requires to be recorded rather than left in chat, and it changes the language, persistence, CI, and deployment of everything downstream.

---

## 3. What is reused, and how (assuming Option A)

### 3.1 Copied structurally (high-fidelity port)

| From Learnexia | To ICT-Kronos | Change |
|---|---|---|
| `python/curriculum_intelligence/app/config.py` | `app/config.py` | Same frozen-dataclass + `from_env()` + typed getters. New aggregates: `MarketDataConfig`, `IctConfig`, `KronosConfig`, `ModelConfig`, `BacktestConfig`. Satisfies §35.11. |
| `app/db.py` (`PipelineJobRepository`, `claim_next`) | `app/db.py` | Near-verbatim. `FOR UPDATE SKIP LOCKED` claim; string `JobType`/`Status`; retry policy owned by the orchestrator, not the worker. |
| `app/logging.py`, `app/health.py` | same | Verbatim. |
| `{parsers,ingestion,inference}/factory.py` | `kronos/factory.py`, `data/factory.py` | **The key pattern.** `KRONOS_BACKEND=mock\|local` — mock by default in dev + CI; torch imported lazily inside the live branch; missing weights degrade to mock with a loud warning. Same for `MARKET_DATA_BACKEND=fixture\|dukascopy\|...`. |
| `main.py` multi-lane threading | `main.py` | Lanes: `ingest`, `features`, `forecast`, `train`, `backtest`. One psycopg connection per thread; uvicorn `/health` on main. |
| `pyproject.toml` | same | Same layout. Lean core (`psycopg`, `fastapi`, `uvicorn`, `numpy`, `pandas`); `[ml]` extra (`scikit-learn`, `xgboost`, `lightgbm`); `[kronos]` extra (`torch`, `huggingface-hub`); `[test]`, `[dev]`. `ruff` (E,F,I,B,UP; line 110) + `black`. |
| `tests/` split + `@pytest.mark.contract` | same | Unit tests (mock backends, no Docker) + Testcontainers contract test for the outbox claim. Plus §19 leakage tests and §27 detector tests. |
| `docker/docker-compose.yaml` service block | same | Own compose. Postgres (plain `postgres:15` — pgvector not needed initially), the worker service, health check on `/health`. |
| `.github/workflows/ci.yml` python job | same | `ruff check .` + `pytest -v` with `KRONOS_BACKEND=mock`, `MARKET_DATA_BACKEND=fixture`. Heavy extras **not** installed in the default gate. |
| `docs/dev/adr/NNNN-*.md` format | same | ADR-0001 of this repo records the Option A decision itself. |
| `docs/dev/HANDOFF.md` + `SessionStart` hook | same | Shared dev memory, mandatory read-first/update-before-PR. |
| `docs/briefs/`, `docs/plans/`, `docs/qc/`, `user-stories/`, `tasks/` | same | Work-intake and traceability structure — directly serves §28/§29. |
| `.claude/agents/` roster | same, with the §5 deltas from `AGENT_INVENTORY.md` | |

### 3.2 Patterns adopted, code not copied

- **`PipelineJob` entity shape** (`JobType`/`Status` strings, `PayloadJson`/`ResultJson`, `ClaimedAt`/`CompletedAt`/`RetryCount`) — re-expressed in Python/SQL. The .NET C# class is not needed.
- **Untrusted-result validation + no-stranding guarantee** from `ParseJobAdvanceService` — the discipline transfers even though the orchestrator will be Python.
- **`AiUsageLog`** — informs the §30 prediction audit trail (request → dataset version → model version → ICT state → prediction → confidence → decision → eventual outcome).
- **`AiResponseCache` + `AiCacheKeyBuilder`** — informs caching Kronos forecasts keyed by `(symbol, timeframe, context_hash, sampling_params, model_version)`.
- **`AiReadinessProbe`** — informs a Kronos model-load readiness probe.
- **`RedisAiRateLimiter`** — informs market-data vendor rate limiting.
- **`AiModule.InitializeAsync` secret-status logging** (presence/absence only, never values) — adopt verbatim as a rule.

### 3.3 Explicitly NOT reused

- `SafetyLayer` and its checks — child-appropriateness has no financial analogue. A *different* guardrail is needed for §35.12 ("never claim predictive certainty"): a **no-certainty-claims check** on any LLM-generated narration, plus a hard rule that probability outputs are always accompanied by their validation period and sample size.
- `PromptBuilder` subject templates (Math/Science/Arabic/English), `ToneFrame`.
- Everything in `Learning`, `Gamification`, `Parent`, `Billing`, `Identity`, `Moderation`, `Notifications`, `Curriculum`, `Analytics`.
- The Turborepo/Expo/Tamagui frontend (not started; revisit only at Phase 10).

---

## 4. Where LLMs are allowed (§26) — enforced boundary

**Hard rule, to be encoded in the repo's `CLAUDE.md` and enforced by `reviewer`:**

> No LLM output may enter the feature vector, the model input, the prediction, or the decision. LLMs consume results; they never produce them.

| Allowed | Forbidden |
|---|---|
| Research orchestration and experiment planning | Deciding whether a candle contains an FVG / BOS / MSS (§5 — deterministic algorithms only) |
| Interpreting and explaining results | Producing a price, direction, or probability |
| Generating reports and summaries | Scoring setup quality (§23 weights are config, then learned — never LLM-judged) |
| Natural-language querying of the research store | Any path from LLM text into a training feature or a trade decision |
| Assisting developers | |

**Implementation:** Anthropic SDK behind an `llm/factory.py` with `LLM_BACKEND=mock\|claude`, mirroring `claude_extractor.py`. Mock by default; CI never calls the network.

---

## 5. Runtime orchestration — deliberately NOT an agent framework

Master Plan §25 lists a Market Data Agent, ICT Analysis Agent, Kronos Agent, Quant Research Agent, Backtesting Agent, and Orchestrator Agent. Since no runtime agent infrastructure exists to reuse, building one would be **new infrastructure**, which §35.6 warns against and which adds risk before the core research question is even testable.

**Recommendation:** implement these as **deterministic services + outbox job lanes**, not as LLM-driven agents. The mapping:

| §25 "agent" | Actual implementation | Job lane |
|---|---|---|
| Market Data Agent | `data/` package — providers behind a factory, normalizer | `ingest` |
| ICT Analysis Agent | `ict/` package — deterministic detectors (§5–§11). **Explicitly no LLM** (§5). | `features` |
| Kronos Agent | `kronos/` package — model load, context prep, forecast, persist | `forecast` |
| Quant Research Agent | `research/` package — experiments, ablations (§22), feature analysis | `train` |
| Backtesting Agent | `backtest/` package — event-driven simulation (§21) | `backtest` |
| Research/Orchestrator Agent | The job-lane orchestrator + the **dev-time** `analyzer`/`planner` subagents | n/a |

If, after Phase 8, a genuine runtime multi-step LLM workflow is needed (e.g. "propose the next ablation, run it, interpret it, propose the next"), that becomes a separately-justified ADR at Phase 9 — with real evidence of need, and with Learnexia's `Ai` gateway as the reference for how to do gateway plumbing well.

---

## 6. Data and persistence

Learnexia offers Postgres (pgvector-capable), Redis, and MinIO. Recommended shape for ICT-Kronos:

| Data | Store | Rationale |
|---|---|---|
| Raw OHLCV (`MarketCandle`, §12) | **Parquet on disk/object store**, partitioned by `symbol/timeframe/year` | Bulk columnar scans over millions of bars; the natural pandas/Arrow shape. Postgres row storage is the wrong tool. **Raw data is immutable and never overwritten** (§12). |
| ICT features, forecasts, predictions | Parquet (datasets) + Postgres (metadata/index) | Features are separate from raw OHLCV (§12). |
| Job outbox, experiment registry, model registry, backtest runs | **Postgres** | Transactional, queryable, the outbox claim needs `SKIP LOCKED`. Serves §28/§29. |
| Model artifacts, Kronos weights, backtest reports | **MinIO / S3** (mirrors Learnexia's use) | |
| Vendor rate limiting, caches | Redis (optional) | |

**Version everything** (§29): each dataset, feature set, model, backtest, and experiment carries a version + git commit, so any backtest is reproducible from its identifiers alone.

---

## 7. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| **Look-ahead leakage** (§19) | **Critical** — silently invalidates every result | Dedicated `leakage-auditor` blocking gate; every detector records an explicit `confirmation_timestamp` distinct from the event timestamp; `test_no_future_data_leakage` per detector; point-in-time-only feature assembly. |
| Kronos assumptions unverified (§13–§15) | High | Phase 5 begins with a verification task against the actual repo + model cards; nothing is built on the "512 candles" claim until confirmed. |
| Torch/CUDA footprint blocking CI | High | Mock-by-default factory; heavy extras excluded from the default CI gate; a separate opt-in workflow for live-model tests. |
| Overfitting via repeated evaluation on the same test set | High | Strict chronological splits (§20); a genuinely held-out out-of-sample period touched **once**; walk-forward as the primary evidence. |
| Assuming ICT adds value | High — it is the research question (§34) | Ablation studies (§22) are a **required deliverable**, not an optional extra. Model A must be built before Model E. |
| Multiple-comparisons / p-hacking across ablations | Medium | Pre-register hypotheses in the experiment record; report the number of configurations tried; correct for multiplicity. |
| Survivorship/quality issues in free FX data | Medium | Pin the data vendor and version; checksum raw files; document gaps, holidays, and DST transitions. |
| Copied patterns drifting from Learnexia's | Low | Record provenance in each ADR; periodic diff review. |

---

## 8. Open questions for the lead

1. **Placement — Option A, B, or C?** (§2) *Blocks Phase 1.* Recommendation: **A**.
2. **Repo/branching** — `ICT-ENGINE` is currently not a git repository. Initialize it, and with what remote?
3. **Market data source** — which vendor for EURUSD and XAUUSD 1H/15M/5M, and over what history? (Determines whether the §20 split `2021–2022 / 2023 / 2024 / 2025` is even available.) *Blocks Phase 1 implementation of the provider.*
4. **Compute** — is a GPU available for Kronos, or is CPU inference the plan? (Affects Phase 5 scope and forecast batch sizing.)
5. **Story/task intake** — should this project adopt Learnexia's mandatory `user-stories/` + `tasks/` intake with the ask-first rule? Recommended yes, for §28/§29.
6. **Do the sibling projects** (`ForexQuant/`, `NNForTrading/`, `TradingBot/`, `TradingBotV2/`) contain prior work worth inspecting before Phase 1?
