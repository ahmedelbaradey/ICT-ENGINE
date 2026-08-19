# ARCHITECTURE_ANALYSIS — Learnexia (Phase 0 Reconnaissance)

**Date:** 2026-08-19
**Subject repo:** `e:/Wrokspace/Learnexia` (`https://github.com/ahmedelbaradey/Learnexia.git`, branch `main`, clean, HEAD `6888e824`)
**Purpose:** Establish what exists before any ICT-Kronos code is written, per Master Plan §2 / §32 Phase 0.

---

## 0. Headline findings (read these first)

Three findings materially change the Master Plan's assumptions. They are stated up front because §25 and §35.2–35.4 are written against a premise that does not hold.

### F1 — Learnexia has **no runtime agent framework**. The "agents" are dev-time Claude Code subagents.

The Master Plan (§25, §35.2, §36.3) instructs "reuse Learnexia's existing agents / orchestration / agent abstractions". What actually exists:

- **12 markdown agent definitions** in `.claude/agents/` (`analyzer`, `planner`, `designer`, `db-migration`, `backend-feature`, `api-tester`, `frontend`, `frontend-e2e-tester`, `qc-test-designer`, `security-auditor`, `reviewer`, `committer`). These are **Claude Code subagent prompts** — YAML frontmatter (`name`, `model`, `description`, `tools`) plus a prompt body. They orchestrate *humans and Claude sessions building the product*.
- The "orchestration mechanism" is a **documented fixed pipeline order** in `CLAUDE.md` (analyzer → planner → designer → implementers → api-tester / e2e-tester → security-auditor → reviewer → committer), dispatched manually by the lead. There is **no code** that executes it — no scheduler, no state machine, no agent runtime, no tool registry, no agent memory store.
- There is **no `IAgent`, no orchestrator, no task graph, no agent tracing** anywhere in the .NET or Python source.

**Consequence:** there is no runtime agent infrastructure to reuse for a Market Data Agent / ICT Analysis Agent / Kronos Agent. Master Plan §25 must be re-read as: *the ICT-Kronos pipeline components are deterministic services*, and Phase 9 "agent integration" means **either** (a) building the first runtime orchestration in this project, **or** (b) reusing the dev-time subagent roster to *build* the system. This document recommends (b) for construction and defers (a) to a deliberate, separately-justified decision. See `INTEGRATION_PLAN.md` §5.

### F2 — What *is* runtime AI infrastructure is an **LLM gateway**, and it maps cleanly to Master Plan §26.

`backend/src/Modules/Ai/` is a production-grade **LLM call gateway**, not an agent system:

| Component | File | What it does |
|---|---|---|
| `IAiGateway` / `AiGateway` | `Ai.Infrastructure/Gateway/AiGateway.cs` | Single facade. Routes to a provider, hard timeout, bounded exponential-backoff retry (429/5xx/timeout), converts **all** provider exceptions to typed `AiError` (never throws), captures `AiUsage` + estimated cost, fire-and-forget usage logging. Never logs prompt/response text or keys. |
| `IAiProvider` | `Ai.Infrastructure/Providers/IAiProvider.cs` | Per-provider adapter (`ClaudeProvider`, `OpenAiProvider`). `CompleteAsync` + `StreamAsync`. |
| `IAiModelRouter` | `Ai.Application/Services/IAiModelRouter.cs` | Pure config-driven mapping `(AiTaskKind, AiModelTier?)` to `(provider, modelId)`. No I/O. |
| `SafetyLayer` | `Ai.Application/Safety/SafetyLayer.cs` | Fail-closed toxicity / age-appropriateness / hallucination checks + `SafetyEvent` store. |
| `PromptBuilder` | `Ai.Application/PromptBuilder/` | Template selection (subject/language), `PromptContext`. |
| `AiUsageLog`, `AiResponseCache` | `Ai.Domain/Entities/` | Usage/cost audit trail; response cache keyed by `AiCacheKeyBuilder`. |
| `AiTutorRateLimiter` / `RedisAiRateLimiter` | `Ai.Application` / `Ai.Infrastructure` | Redis-backed per-user rate limiting. |

This is **exactly** the right seam for Master Plan §26 (LLMs for explanation / reporting / research orchestration, never for price prediction). The `AiTaskKind` routing table is extensible, and usage/cost logging is already built.

**Caveat:** it is a .NET-side seam. A Python research service cannot inject `IAiGateway`; it would have to call the Host over HTTP, or call Anthropic directly (as `python/curriculum_intelligence/ingestion/claude_extractor.py` already does, gated behind `EXTRACTOR_BACKEND=claude`). The existing Python precedent is **direct SDK use behind a factory**, not a call back into .NET.

### F3 — There is **no ML/numerical stack anywhere** in the repo.

`grep -rniE "numpy|pandas|scikit|sklearn|xgboost|lightgbm|torch|scipy"` across `python/` returns **zero hits**. The Python service's entire runtime dependency set is:

```
psycopg[binary]==3.2.3, boto3==1.35.71, fastapi==0.115.5, uvicorn[standard]==0.32.1
```

Heavy AI deps are deliberately quarantined in an optional `[live]` extra and are *devops-gated*, never installed by default. ICT-Kronos needs `numpy`, `pandas`, `scikit-learn`, `xgboost`, `lightgbm`, and — for Kronos — **PyTorch**. That is a multi-GB image and a materially different dependency posture from anything in the repo today. This is the strongest technical argument in the placement decision (`INTEGRATION_PLAN.md` §2).

---

## 1. Repository structure

```
Learnexia/
├── CLAUDE.md                  # Project rulebook — binding for all agents/sessions
├── .claude/
│   ├── agents/                # 12 dev-time Claude Code subagent definitions
│   ├── skills/                # enhance, user-stories
│   └── settings.json          # SessionStart hook: cats docs/dev/HANDOFF.md into context
├── backend/                   # .NET 10 modular monolith (Learnexia.Modular.sln)
│   ├── Directory.Build.props
│   ├── Directory.Packages.props   # Central package management
│   ├── src/
│   │   ├── Host/Learnexia.Host/   # Composition root, middleware, health checks
│   │   ├── Modules/               # 10 modules x 4 projects each
│   │   └── Shared/                # Kernel, Contracts, Resources
│   └── tests/                     # 12 test projects
├── python/
│   └── curriculum_intelligence/   # The ONLY Python service. THE reuse template.
├── apps/ · packages/ · design-system/   # Turborepo frontend (Expo/Tamagui) — not started
├── docker/                    # compose + Dockerfile
├── docs/                      # architecture, dev/, adr/, briefs/, plans/, qc/, security/
├── user-stories/ · tasks/     # Work intake — source of truth for scope
└── .github/workflows/         # ci.yml, deploy-contabo.yml, deploy-staging.yml
```

---

## 2. Backend — .NET 10 modular monolith

**Modules** (each = `.Api` + `.Application` + `.Domain` + `.Infrastructure`):
`Ai`, `Analytics`, `Billing`, `Curriculum`, `Gamification`, `Identity`, `Learning`, `Moderation`, `Notifications`, `Parent`.

**Patterns in force (from `CLAUDE.md` — non-negotiable):**

1. **Module isolation** — a module never references another module's projects. Cross-module traffic goes through `Learnexia.Shared.Contracts` only (integration events / interface seams). **No cross-module FKs.** Schema-per-module inside one Postgres database (`Learnexia`).
2. **CQRS via MediatR** — `Features/<Area>/Commands|Queries/<Name>/{Command,Handler,Validator}.cs`.
3. **Response envelope** — handlers return `BaseResponse<T>` via `BaseResponseHandler`; controllers use `NewResult(...)`. The success flag is spelled **`Successed`** (deliberate; do not "fix" it).
4. **No Unit of Work** (ADR-0001) — `GenericRepository` commits per call; explicit transactions for atomic multi-writes.
5. **Validation** — `ValidationBehavior` runs for `ICommand<>` only; queries are not auto-validated.
6. **Logging** — inject `ILoggerManager` (`Shared.Kernel/Abstractions/ILoggerManager.cs`), **never** `ILogger<T>`.
7. **Module registration** — `public static IServiceCollection Add<X>Module(this IServiceCollection, IConfiguration)` plus an optional `InitializeAsync(IServiceProvider)` startup hook. `AiModule.cs` is a clean example: it logs *presence/absence* of provider keys, never values.
8. **Design patterns — ask first.** Mirror existing shapes; never introduce Strategy/Factory/Decorator unilaterally.

**Shared kernel** (`Shared.Kernel/`): `Abstractions` (`IGenericRepository`, `ILoggerManager`, `ICurrentUserService`, `ISystemClock`, `AuthorizationPolicies`), `Behaviors` (MediatR pipeline), `DomainEvents` (ADR-0002), `Responses`, `Results`, `Pagination`, `Storage`, `Settings`, `Entities` (`AggregateRoot`, `CreationAuditedEntity`).

**Auth:** ASP.NET Identity + JWT. Permission policies `{Module}.{Action}` exist but are **not enforced by default** — `[Authorize(policy)]` is applied deliberately per endpoint.

---

## 3. The Python service — `python/curriculum_intelligence/`

**This is the most directly reusable asset in the repository.** It is a mature, well-documented worker whose shape maps almost 1:1 onto what ICT-Kronos needs.

### 3.1 Integration pattern: DB-outbox + polling worker (ADR-0004)

Decided in ADR-0004 and `docs/briefs/curriculum-system-of-record.md` §4b. **No service-to-service calls.** .NET and Python meet *only* at rows in `curriculum."PipelineJobs"`.

- .NET writes `Status='Pending'` rows.
- Python **atomically claims** one: `SELECT "Id" ... WHERE "Status"='Pending' AND "JobType"=... ORDER BY "Id" FOR UPDATE SKIP LOCKED LIMIT 1`, then `UPDATE ... SET "Status"='Processing'` **inside the same transaction** (`app/db.py`, `PipelineJobRepository.claim_next`).
- Python writes `ResultJson` and transitions the row to `Done`/`Failed`.
- A .NET `BackgroundService` (`ParseJobAdvanceService`, `IngestJobAdvanceService`, `EdgeInferenceAdvanceService`) claims `Done`/`Failed` rows (also `FOR UPDATE SKIP LOCKED`), applies the result, and archives the row.

**Contract rules worth copying verbatim:**

- `JobType` / `Status` are **strings**, never int enums — an explicit cross-process contract.
- **Retry policy is owned by .NET** (ADR-0004 §3 / Q7). Python reports terminal `Failed` with diagnostics and does *not* increment `RetryCount` or re-enqueue.
- `ResultJson` is treated as **untrusted cross-process input** on the .NET side — every derived string is validated and length-bounded before assignment.
- **No-stranding guarantee** — any exception during job processing writes a terminal state in a fresh transaction, so no job can sit at `Processing` indefinitely.
- Three independent lanes (`parse`, `ingest`, `infer_edges`) run as **daemon threads in one process**, one psycopg connection each (connections are not thread-safe), with uvicorn serving `/health` on the main thread (`main.py`).

### 3.2 Configuration posture (`app/config.py`)

Env-only, frozen `@dataclass` aggregates with `from_env()` classmethods, typed `_get_bool` / `_get_int` / `_get_float` helpers, and a top-level `Settings` aggregate built by `get_settings()` at startup. **No secret is ever hardcoded or committed**; compose injects `${VAR}` references.

### 3.3 The mock-by-default / devops-gated-live pattern

The single most valuable pattern for ICT-Kronos. Every expensive external backend is:

1. behind an interface (`ParserBackend`, `Extractor`, `Inferer`),
2. selected by an env enum (`PARSER_BACKEND`, `EXTRACTOR_BACKEND`, `INFERER_BACKEND`) inside a `factory.py`,
3. **mock by default** in dev + CI — deterministic, fixture-driven, zero network, zero heavy deps,
4. live backend **imported lazily inside the branch** so the SDK is only required when actually selected,
5. misconfigured live (flag set, credentials absent) → **falls back to mock with a loud warning**, degrading rather than crashing.

This is exactly how Kronos (PyTorch, GPU, multi-GB weights) should be gated so CI and dev stay fast and dependency-light.

### 3.4 Testing

`pytest` + `pytest-asyncio` + **`testcontainers[postgres]`**. Tests split into unit (mocked backends) and a `@pytest.mark.contract` **outbox contract test** that exercises the atomic claim against a real Postgres in Docker. Lint is `ruff` (select `E,F,I,B,UP`, line-length 110) plus `black`.

---

## 4. Infrastructure

**`docker/docker-compose.yaml`** on network `learnexia-network`:

| Service | Image / build | Notes |
|---|---|---|
| `postgres` | `pgvector/pgvector:pg15` | **pgvector already available** — relevant if embeddings are ever wanted |
| `redis` | `redis:latest` | Used by `RedisAiRateLimiter` |
| `minio` + `minio-setup` | `quay.io/minio/minio`, `mc` | S3-compatible object store; `minio-setup` creates buckets (`avatars`, `curriculum`) |
| `learnexia-api` | build from repo root | The .NET Host |
| `ai-gateway` | `alpine:3.20` | Placeholder |
| `curriculum-intelligence` | build from `../python/curriculum_intelligence` | Poller + FastAPI health on `8091`; `depends_on: postgres(healthy), minio` |

**CI (`.github/workflows/ci.yml`)** — three jobs, all on `ubuntu-latest` (Docker daemon required by Testcontainers):

1. `build-and-test` — `dotnet restore` / `build -c Release` / `test` on `Learnexia.Modular.sln`, uploads `.trx`.
2. `docker-build` — validates `docker/Dockerfile` builds.
3. `python-curriculum-intelligence` — Python 3.12, `pip install ".[test]"`, `ruff check .`, `pytest -v` with `PARSER_BACKEND=mock`.

**Deploy:** `deploy-contabo.yml`, `deploy-staging.yml`.

**Secrets:** env-only, `${VAR}` in compose, never committed. `.env.example` documents keys. `AiModule.InitializeAsync` logs only presence/absence.

---

## 5. Documentation and process conventions

| Artifact | Location | Role |
|---|---|---|
| Project rulebook | `CLAUDE.md` | Binding for every session/agent |
| Shared dev memory | `docs/dev/HANDOFF.md` | **Mandatory read-first / update-before-PR.** Auto-loaded by a `SessionStart` hook |
| ADRs | `docs/dev/adr/0001..0004` | Decision records. ADR-0004 is the template for a new Python service |
| Pipeline Briefs | `docs/briefs/<story>.md` | Written by `analyzer` |
| Execution Plans | `docs/plans/<story>.md` | Written by `planner` |
| QC test plans | `docs/qc/<StoryID>/` | Written by `qc-test-designer` |
| Work intake | `user-stories/`, `tasks/` | **Source of truth for scope.** Hard rule: agreed decisions become stories + tasks *before* implementation — **but ask the lead first** |
| Conventions | `docs/dev/CONVENTIONS.md`, `FEATURE_PLAYBOOK.md`, `CODE_TEMPLATES.md` | |
| Parallelism | `docs/dev/PARALLELISM.md` | Git-worktree-per-story rules |

---

## 6. Suitability assessment for ICT-Kronos

### Strongly reusable (adopt as-is)

- **DB-outbox + `FOR UPDATE SKIP LOCKED` worker pattern** — maps directly onto long-running ICT feature builds, Kronos forecast batches, model training, and backtest runs. All are exactly the "must not block a request, must survive restarts" shape ADR-0004 was written for.
- **Mock-by-default / devops-gated-live factory** — the answer to "how do we keep CI fast while Kronos needs PyTorch + GPU + model weights".
- **Env-only frozen-dataclass config** — directly satisfies Master Plan §35.11 ("never hardcode trading assumptions without configuration").
- **Testcontainers contract test + `pytest` + `ruff` / `black`** — directly satisfies §27.
- **ADR format, HANDOFF protocol, brief/plan/QC doc structure, story+task intake** — process infrastructure that satisfies §28/§29 (experiment tracking, versioning, reproducibility) far better than starting from scratch.
- **The `.claude/agents/` roster** — reusable to *build* ICT-Kronos (analyzer → planner → implementer → reviewer → committer). Gaps noted in `AGENT_INVENTORY.md`.
- **The Ai module gateway** — the correct home for §26 LLM usage (report generation, result interpretation), *if* the system runs .NET-side or calls the Host over HTTP.

### Not reusable / absent

- **Runtime agent orchestration** — does not exist (F1).
- **Any ML / numerical / time-series infrastructure** — does not exist (F3). Feature stores, experiment tracking (MLflow / W&B), dataset versioning, model registry, walk-forward harness: all greenfield.
- **Market data ingestion, OHLCV storage, timeseries DB** — absent.
- **Frontend** — not started. Master Plan §32 Phase 10 (dashboard) has no existing FE to extend; `design-system/` tokens exist but no app.

### Domain-fit risk

Learnexia is an **Arabic-language K-12 education product** for school students, built around parents, children, grades, XP, badges, and curriculum. Its non-negotiables (parent-driven onboarding, 4 subjects, no teacher role, child-data safety posture, a `SafetyLayer` tuned for age-appropriateness) have **zero overlap** with financial market research. Master Plan §2 Step 3 and §35.7 both call for isolation. Analysed in `INTEGRATION_PLAN.md` §2.

---

## 7. Verification status of external claims

Reconnaissance was scoped to the local Learnexia repository. The following Master Plan statements about **Kronos** were **not verified** in this phase and must be checked against `https://github.com/shiyu-coder/Kronos` and its model cards before Phase 5:

- §13 — availability and naming of `Kronos-mini` / `Kronos-small` / `Kronos-base`.
- §14 — the claim that "for Kronos-small/base, the documented maximum context is 512 candles".
- §15 — the exact forecast output shape and sampling parameters.
- License terms for the model weights and for the code.

These are treated as **assumptions to be validated**, not facts, in `IMPLEMENTATION_ROADMAP.md` Phase 5.

Four sibling directories also exist under `e:/Wrokspace/` and were **not** inspected (outside Phase 0 scope, but worth a look before Phase 1 to avoid duplicating prior work): `ForexQuant/`, `NNForTrading/`, `TradingBot/`, `TradingBotV2/`.

---

## 8. Files read during reconnaissance

`CLAUDE.md` · `.claude/settings.json` · `.claude/agents/{analyzer,planner,reviewer,committer}.md` (frontmatter + intro) · `backend/` tree · `backend/src/Modules/Ai/**` (full file listing; `AiGateway.cs`, `IAiProvider.cs`, `IAiModelRouter.cs`, `AiModule.cs` read) · `backend/src/Modules/Curriculum/.../PipelineJob.cs` · `.../ParseJobAdvanceService.cs` · `backend/src/Shared/` tree · `python/curriculum_intelligence/**` (full tree; `pyproject.toml`, `main.py`, `app/config.py`, `app/db.py`, `app/logging.py`, `parsers/factory.py` read) · `docker/docker-compose.yaml` · `.github/workflows/ci.yml` · `docs/dev/adr/0004-python-curriculum-pipeline-service.md` · `docs/` tree.
