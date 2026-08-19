# AGENT_INVENTORY — Learnexia

**Date:** 2026-08-19
**Scope:** Master Plan §2 Step 2 / §36.3–36.4 — inventory every existing agent, identify the orchestration mechanism, and mark what ICT-Kronos can reuse.

---

## 0. The critical distinction

The Master Plan assumes Learnexia contains a **runtime agent framework** whose agents could be extended with a Market Data Agent, ICT Analysis Agent, Kronos Agent, etc. (§25). **It does not.**

Learnexia has two entirely separate things that the word "agent" could refer to:

| | **A. Dev-time agents** | **B. Runtime AI** |
|---|---|---|
| What | 12 Claude Code subagent definitions in `.claude/agents/*.md` | The `Ai` module — an LLM call gateway |
| Purpose | Build the product (analysis, planning, coding, review, commit) | Serve AI Tutor features to students at runtime |
| Form | Markdown prompt + YAML frontmatter | C# services behind `IAiGateway` |
| Orchestration | **Documented convention in `CLAUDE.md`, dispatched manually by the lead. No executing code.** | MediatR CQRS request/response. No multi-step agent loop, no tool use, no planning. |
| Reusable for ICT-Kronos? | **Yes — to build it** | **Yes — for §26 LLM roles only** |

There is **no** third thing: no `IAgent`, no orchestrator, no task graph, no agent memory, no tool registry, no agent tracing. Verified by exhaustive file listing of `backend/src/Modules/Ai/**` and `python/curriculum_intelligence/**`.

---

## 1. Dev-time agents (`.claude/agents/`)

Frontmatter schema: `name`, `model` (`opus` / `haiku`), `description`, optional `tools` allowlist.

| Agent | Purpose | Location | Inputs | Outputs | Can Reuse? |
|---|---|---|---|---|---|
| `analyzer` | FIRST in every cycle. Builds business + technical understanding, writes a Pipeline Brief. Read-only except the brief. | `.claude/agents/analyzer.md` (model: opus; tools: Read, Grep, Glob, Write, WebSearch, WebFetch) | User story `.md`, per-stack task files, `CLAUDE.md`, arch docs | `docs/briefs/<story>.md` — traceability, acceptance criteria, per-agent handoffs, open questions | **Yes, direct.** Retarget inputs from `user-stories/` to ICT-Kronos research stories. Its WebSearch/WebFetch tools are needed for the Kronos verification task. |
| `planner` | SECOND. Turns brief + tasks into a dependency-ordered Execution Plan with parallel/sequential batches and review gates. | `.claude/agents/planner.md` (model: opus) | Pipeline Brief, story, task files, conventions docs | `docs/plans/<story>.md` — task inventory, batches, gates, blockers | **Yes, direct.** Phase-based batching maps cleanly onto §32. |
| `designer` | Turns a story with a UI surface into a Design Spec grounded in `design-system/`. | `.claude/agents/designer.md` | Story, design-system kit | `design-system/ui_kits/<surface>/<story>.md` | **Deferred.** Only relevant at Phase 10 (dashboard), and only if a UI is built inside Learnexia's design system. |
| `db-migration` | EF Core migrations / schema changes. | `.claude/agents/db-migration.md` | Plan batch | Migration + snapshot files | **Conditional.** Only if the financial context lives in the .NET solution (see `INTEGRATION_PLAN.md` §2). Useless for a Python/Postgres-native or Parquet-based store. |
| `backend-feature` | Implements .NET module features (MediatR CQRS, `BaseResponse<T>`, `ILoggerManager`). | `.claude/agents/backend-feature.md` | Plan batch, brief, conventions | C# code + unit tests | **Conditional**, same as above. Strongly .NET/Learnexia-convention-specific. |
| `api-tester` | Validates running HTTP endpoints (integration tests) after `backend-feature`. | `.claude/agents/api-tester.md` | Running API, `docs/qc/<id>/backend-test-cases.md` | Integration tests + `execution-report.md` | **Conditional.** Relevant at Phase 10 if an HTTP API is exposed. |
| `frontend` | Expo/Tamagui student-app implementation. | `.claude/agents/frontend.md` | Design Spec, plan batch | TSX components/hooks | **No** for the research platform. Possibly at Phase 10. |
| `frontend-e2e-tester` | Playwright E2E over the running web PWA (RTL ar/en, auth/role routing). | `.claude/agents/frontend-e2e-tester.md` | Running PWA, `frontend-test-cases.md` | E2E specs + report | **No** for the research platform. |
| `qc-test-designer` | On-demand. Designs comprehensive BE + FE test cases and a coverage report; designs only, does not implement. | `.claude/agents/qc-test-designer.md` (model: opus) | Story + brief | `docs/qc/<StoryID>/` test-case docs + coverage report | **Yes — high value.** Master Plan §27 (ICT detector unit tests) and §19 (leakage tests) demand exactly this deliberate, traceable test design. Prompt needs retargeting from BE/FE to *deterministic-detector + leakage + statistical-validity* test classes. |
| `security-auditor` | Audits security-sensitive batches (auth/authz, user data, upload, AI prompts, secrets, payments). Critical/High findings block. | `.claude/agents/security-auditor.md` | Batch diff | Findings report, blocking verdict | **Partial.** Relevant to broker/data-vendor API keys and any deployed API. Its child-data/PII focus does not transfer. |
| `reviewer` | Quality + conventions gate. Runs build/tests, returns PASS/FAIL against the brief's acceptance criteria. Read-only. | `.claude/agents/reviewer.md` (model: opus; tools: Read, Grep, Glob, Bash) | Brief, plan, diff | PASS/FAIL + required fixes | **Yes, direct** — with an ICT-Kronos rule set substituted for `CONVENTIONS.md`. This is the natural enforcement point for the leakage and reproducibility rules (§19, §28, §35.10). |
| `committer` | FINAL stage. Only after `reviewer` PASSes. Commits on `feat/<StoryID>` branch, pushes, opens a PR. Never on `main`, never amends/force-pushes, never merges, refuses to stage secrets or build artifacts. | `.claude/agents/committer.md` (model: haiku; tools: Bash, Read, Grep, Glob) | Approved batch | Commit + branch + PR | **Yes, direct.** Satisfies §35.15 (small, logically separated commits). |

### Orchestration mechanism (§36.4)

**Convention, not code.** `CLAUDE.md` specifies a fixed order:

```
analyzer → planner → (designer, if UI) → implementers (db-migration | backend-feature | frontend)
        → api-tester (if HTTP) / frontend-e2e-tester (if UI) → security-auditor (if sensitive)
        → reviewer (gate) → committer (branch + PR)
```

Supporting mechanisms:

- **Batching** — the `planner` marks batches parallel vs sequential; the lead dispatches them.
- **Parallel pipelines** — `docs/dev/PARALLELISM.md`: independent stories only, each in its own `feat/<StoryID>` git worktree; edits to shared files (`Program.cs`, `.sln`, Claims, `Directory.Packages.props`) are serialized.
- **Shared memory** — `docs/dev/HANDOFF.md`, auto-injected by a `SessionStart` hook in `.claude/settings.json`. Mandatory read-first / update-before-PR.
- **Work intake gate** — no build starts on an agreement that is not first recorded as a user story + per-stack task files, and **no story/task generation starts without the lead's explicit go-ahead**.

**Nothing executes this.** There is no runtime that reads the plan and dispatches agents. The lead does it.

---

## 2. Runtime AI components (`backend/src/Modules/Ai/`)

Not agents. A gateway. Listed here because Master Plan §26 needs exactly this and nothing more.

| Component | Purpose | Location | Inputs | Outputs | Can Reuse? |
|---|---|---|---|---|---|
| `IAiGateway` / `AiGateway` | Central LLM facade: route, timeout, bounded retry, typed `AiError` (never throws), usage + cost capture, fire-and-forget usage logging. Never logs prompt text or keys. | `Ai.Infrastructure/Gateway/AiGateway.cs` | `AiRequest` (`AiTaskKind`, `AiModelTier?`) | `AiResult` / `IAsyncEnumerable<AiChunk>` | **Yes — for §26 only.** The correct seam for narration, result interpretation, report generation. **Never** for price prediction. Reachable from .NET directly; from Python only via HTTP. |
| `IAiProvider` (`ClaudeProvider`, `OpenAiProvider`) | Per-provider adapter. `CompleteAsync` + `StreamAsync`. Internal to `Ai.Infrastructure`. | `Ai.Infrastructure/Providers/` | `AiRequest`, `modelId` | `AiResult` / `AiChunk` | **Yes**, unchanged. Adding a provider is a known-shape task. |
| `IAiModelRouter` / `AiModelRouter` | Pure config-driven `(AiTaskKind, AiModelTier?)` → `(provider, modelId)`. No I/O. | `Ai.Application/Services/` | Task kind + tier hint + config | `RouteResult` | **Yes** — extend `AiTaskKind` with research/reporting kinds. |
| `SafetyLayer` + checks | Fail-closed toxicity / age-appropriateness / hallucination gates; `SafetyEvent` store; `ReasonCodes`. | `Ai.Application/Safety/`, `Ai.Domain/Safety/`, `Ai.Infrastructure/Safety/` | Prompt + model output | `CheckVerdict`, safety events | **No.** Tuned for child-appropriate educational content. A financial system needs a *different* guardrail: "never state a prediction as certainty" (§35.12), which is a new check, not a reuse. |
| `PromptBuilder` + `TemplateSelector` + subject templates | Builds prompts from `PromptContext`; per-subject and per-language (Arabic/English) templates + `ToneFrame`. | `Ai.Application/PromptBuilder/` | `PromptContext` | Prompt string | **Pattern only.** The template-selection shape is worth copying; the Math/Science/Arabic/English templates are irrelevant. |
| `AiUsageLog` + `IAiUsageRecorder` + `AiUsageLogStore` | Per-call usage/cost audit trail written off the hot path. | `Ai.Domain/Entities/`, `Ai.Infrastructure/Gateway/`, `.../Safety/` | Gateway calls | `ai.AiUsageLogs` rows | **Yes — pattern and table shape.** Directly informs §30 (observability audit trail) and §28 (experiment tracking). |
| `AiResponseCache` + `AiCacheKeyBuilder` | Response cache keyed by a deterministic builder. | `Ai.Domain/Entities/`, `Ai.Application/Cache/` | Request signature | Cached response | **Yes — pattern.** Same shape suits caching Kronos forecasts keyed by `(symbol, timeframe, context_window_hash, sampling_params)`. |
| `IAiTutorRateLimiter` / `RedisAiRateLimiter` | Redis-backed per-user rate limiting. | `Ai.Application/Services/`, `Ai.Infrastructure/Services/` | User + task | Allow/deny | **Yes — pattern.** Directly applicable to market-data vendor rate limits. |
| `AiReadinessProbe` | Startup readiness for AI dependencies. | `Ai.Infrastructure/Readiness/` | Config | Health status | **Yes — pattern** for a Kronos model-load readiness probe. |
| `AiSafetyDashboardService`, `AiTutorUsageService` + admin queries | Admin dashboards over safety signals and tutor usage (trends, breakdowns, flagged outputs). | `Ai.Infrastructure/Services/`, `Ai.Application/Features/Admin*` | Logs/events | DTOs | **Pattern only** — a good template for the §32 Phase 10 research dashboard's query shape. |

---

## 3. Background workers / job processing

Not agents, but the closest thing to autonomous runtime execution — and the most important reuse target.

| Component | Purpose | Location | Can Reuse? |
|---|---|---|---|
| `PipelineJob` entity | The DB-outbox row. `JobType`/`Status` as **strings** (cross-process contract); `PayloadJson` / `ResultJson`; `ClaimedAt` / `CompletedAt` / `RetryCount`. | `Curriculum.Domain/Entities/PipelineJob.cs` | **Yes — copy the shape** for `ForecastJob` / `FeatureBuildJob` / `BacktestJob` / `TrainingJob`. |
| `ParseJobAdvanceService`, `IngestJobAdvanceService`, `EdgeInferenceAdvanceService` | .NET `BackgroundService` pollers that claim `Done`/`Failed` jobs with `FOR UPDATE SKIP LOCKED`, apply results, own the retry policy, and guarantee no stranding. Treat `ResultJson` as untrusted. | `Curriculum.Infrastructure/Jobs/` | **Yes — pattern.** Especially the untrusted-result validation and no-stranding guarantee. |
| `PipelineJobRepository.claim_next` | Python side of the atomic claim: `SELECT ... FOR UPDATE SKIP LOCKED` then `UPDATE ... SET Status='Processing'` in one transaction. | `python/curriculum_intelligence/app/db.py` | **Yes — copy nearly verbatim.** |
| `PipelinePoller` / `IngestPoller` / `InferPoller` | Three lanes on daemon threads, one psycopg connection each, plus uvicorn `/health` on the main thread. | `python/.../workers/`, `main.py` | **Yes — copy the multi-lane shape** for `ingest` / `features` / `forecast` / `train` / `backtest` lanes. |
| `build_parser` / `build_extractor` / `build_inferer` factories | Mock-by-default, env-selected, lazy live import, degrade-with-warning on missing credentials. | `python/.../{parsers,ingestion,inference}/factory.py` | **Yes — the single most important pattern to copy** (gates PyTorch/Kronos out of CI). |

---

## 4. Gaps — what ICT-Kronos needs and Learnexia does not have

| Need (Master Plan ref) | Exists? | Note |
|---|---|---|
| Runtime agent orchestration (§25) | **No** | Would be greenfield. Recommend *not* building it — see `INTEGRATION_PLAN.md` §5. |
| Market data ingestion / OHLCV store (§12, Phase 1) | No | Greenfield. |
| Numerical/ML stack — numpy, pandas, sklearn, xgboost, lightgbm (§16, Phase 4) | **No** | Zero hits repo-wide. |
| PyTorch / model-weight hosting (§13, Phase 5) | No | Multi-GB dependency; no GPU story in compose or deploy. |
| Experiment tracking / model registry (§28) | No | Greenfield (MLflow or a Postgres-table equivalent). |
| Dataset / feature versioning (§29) | No | Greenfield. |
| Walk-forward + backtest harness (§20, §21) | No | Greenfield. |
| Leakage-detection test tooling (§19) | No | Greenfield — but `qc-test-designer` + `reviewer` are the right enforcement points. |
| Python implementer subagent | **No** | ADR-0004 §6 explicitly decided against one ("the specialized roster is .NET/TS-only"; Python work goes to `general-purpose`; "revisit if volume warrants"). ICT-Kronos is overwhelmingly Python — **this decision should be revisited**. See §5 below. |

---

## 5. Recommended agent changes for ICT-Kronos

Rather than inventing a runtime agent framework (§35.6 — do not duplicate infrastructure), reuse the dev-time roster with these deltas:

1. **Add a `quant-python` implementer subagent** — explicitly the revisit ADR-0004 §6 anticipated. Owns deterministic ICT detectors, feature pipelines, model training, and backtests; bound by rules "no look-ahead", "no hardcoded trading constants", "tests alongside implementation".
2. **Add a `leakage-auditor` subagent** — modelled on `security-auditor` (blocking, Critical/High gate), but auditing for look-ahead bias, shuffled splits, target-derived features, and future-confirmation timestamps. §19 calls leakage a *hard requirement*; a blocking audit gate is the mechanism that enforces it.
3. **Retarget `qc-test-designer`** to design detector-level, leakage, and statistical-validity test suites (§27).
4. **Retarget `reviewer`** with an ICT-Kronos rule set: reproducibility (§28), versioning (§29), no certainty claims (§35.12), chronological splits (§20).
5. **Reuse `analyzer` / `planner` / `committer` unchanged.**
6. **Skip `designer` / `frontend` / `frontend-e2e-tester`** until Phase 10, and only if a UI is built inside Learnexia's design system.

Whether these live in a new repository's `.claude/agents/` or alongside Learnexia's depends on the placement decision in `INTEGRATION_PLAN.md` §2 — which is the one open question blocking Phase 1.
