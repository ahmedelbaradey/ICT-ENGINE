# ADR 0001 — ICT-Kronos lives in its own repository, reusing Learnexia's patterns

- **Status:** Accepted (2026-08-19)
- **Deciders:** Lead
- **Context source:** Phase 0 reconnaissance — [ARCHITECTURE_ANALYSIS.md](../../financial-ai/ARCHITECTURE_ANALYSIS.md), [AGENT_INVENTORY.md](../../financial-ai/AGENT_INVENTORY.md), [INTEGRATION_PLAN.md](../../financial-ai/INTEGRATION_PLAN.md) §2.
- **Related:** Learnexia `docs/dev/adr/0004-python-curriculum-pipeline-service.md` — the ADR whose decisions this repo ports.

## Context

ICT-Kronos is a quantitative research platform testing whether deterministic ICT market-structure features add predictive value over simpler baselines, in combination with the Kronos financial foundation model and classical ML.

The original master plan directed us to build it inside `ahmedelbaradey/Learnexia`, reusing that platform's "existing agent infrastructure and orchestration". Phase 0 reconnaissance established three facts that change the decision:

1. **Learnexia has no runtime agent framework.** Its 12 "agents" are Claude Code *dev-time* subagent prompts in `.claude/agents/`, orchestrated by a documented convention in `CLAUDE.md` and dispatched manually by the lead. No `IAgent`, no orchestrator, no task graph, no agent memory, no agent tracing exists in any source file. There is no runtime agent infrastructure to reuse.

2. **Learnexia's runtime AI is an LLM gateway, not an agent system.** `backend/src/Modules/Ai/` provides provider routing, timeouts, bounded retry, typed errors, safety checks, usage/cost logging, response caching, and rate limiting. It is well built and maps precisely onto the LLM role this project needs (reporting and interpretation) — and nothing beyond it.

3. **Learnexia contains no ML or numerical stack at all.** `numpy`, `pandas`, `scikit-learn`, `xgboost`, `torch`, and `scipy` return zero hits repo-wide. The Python service's entire runtime dependency set is four lean packages, and its authors *deliberately* quarantined heavy AI dependencies behind an optional `[live]` extra that is never installed by default.

Meanwhile Learnexia is an **Arabic-language K-12 education product** for school students, built around parents, children, grades, XP, badges, and curriculum, with a safety layer tuned for age-appropriate content. It shares no domain concepts with financial market research.

## Decision

**ICT-Kronos is a separate repository (`ICT-ENGINE`) that copies Learnexia's proven patterns rather than sharing its runtime.**

1. **Own repository, own Postgres, own compose stack, own CI.** No shared database, no shared deployment, no project reference in either direction.

2. **Patterns are ported with attribution, not invented.** Specifically, from `Learnexia/python/curriculum_intelligence`:
   - env-only frozen-dataclass config with `from_env()` classmethods and typed getters (`app/config.py`)
   - the DB-outbox worker with `FOR UPDATE SKIP LOCKED` atomic claim, string `JobType`/`Status` as a cross-process contract, and retry policy owned by the orchestrator rather than the worker (`app/db.py`)
   - `app/logging.py`, ported verbatim
   - the **mock-by-default / gated-live factory** (`parsers/factory.py`): interface + env enum + deterministic default + lazy import of the heavy backend + degrade-with-warning on missing credentials
   - `pytest` + `ruff` + `black` + Testcontainers contract-test posture
   - the ADR format, the `HANDOFF.md` shared-memory protocol, and the brief/plan/QC documentation structure

3. **LLMs are reached via the Anthropic SDK behind a factory**, following the precedent already set by Learnexia's own `ingestion/claude_extractor.py` — not by calling back into the .NET `IAiGateway`.

4. **The `.claude/agents/` roster is ported and adapted**: `analyzer`, `planner`, `reviewer`, `committer` largely unchanged; `qc-test-designer` retargeted at detector/leakage/statistical-validity test design; two new agents added — `quant-python` (implementer) and `leakage-auditor` (blocking gate). This is the explicit revisit that Learnexia's ADR-0004 §6 anticipated when it deferred a Python implementer subagent "if volume warrants".

5. **The six components the master plan called "agents"** (Market Data, ICT Analysis, Kronos, Quant Research, Backtesting, Orchestrator) are implemented as **deterministic services on outbox job lanes**, not as LLM-driven agents. Building a runtime agent framework would be new infrastructure, and it is not needed to answer the research question.

## Rationale

- **Dependency isolation is decisive.** ICT-Kronos requires PyTorch plus the scientific Python stack — a multi-GB footprint. Adding that to Learnexia would contradict an explicit, documented design posture, inflate the education product's image and attack surface, and slow CI on every unrelated PR.
- **Zero domain overlap.** A `Markets` module beside `Gamification` and `Parent` in a children's education monolith violates module isolation in spirit even where it compiles.
- **No risk to Learnexia.** No shared `.sln`, `Program.cs`, `Directory.Packages.props`, or CI file to serialize edits against, and no way for this work to break a live product.
- **Independent cadence and licensing.** Kronos model weights carry their own license; a research platform iterates differently from a production education app.

## Consequences

- **Patterns are copied, not shared** — drift between the two repositories is possible. Mitigated by recording provenance in module docstrings and in this ADR.
- **Scaffolding is duplicated** (compose, CI, config helpers, logging). Accepted: roughly a day of mostly literal file copies, paid once.
- **No direct `IAiGateway` injection.** Accepted: the Python-side SDK-behind-a-factory precedent already exists in Learnexia and is simpler.
- **This repo owns its own operational burden** — its own Postgres, its own deploy, its own secret management.
- **If ICT-Kronos ever needs to become a Learnexia product feature**, the seam is the DB-outbox pattern both sides already speak, so the migration path is a known one rather than a rewrite.

## Alternatives considered

- **New bounded context inside Learnexia** (`backend/src/Modules/Markets` + `python/ict_kronos/`, meeting at a `markets."PipelineJobs"` outbox). Rejected on dependency footprint, domain mismatch, and CI cost. Would be the right answer if ICT-Kronos were meant to be a *feature of the education product* — it is not; it is an independent research platform.
- **Separate repo sharing Learnexia's Postgres instance.** Rejected: couples two products' uptime and backup/restore for no gain, and puts heavy analytical scans on the database serving a live application.
- **Building a runtime agent framework to satisfy the master plan's §25 literally.** Rejected: it is new infrastructure that does not advance the research question, and the plan's own rule ("do not duplicate infrastructure") argues against it. Revisitable at Phase 9 with evidence of need.
