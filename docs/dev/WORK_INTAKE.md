# WORK_INTAKE — how scope becomes work

**Decision:** ICT-Kronos **reuses Learnexia's work-intake mechanism** rather than inventing a second one, while keeping the financial domain fully isolated from the educational one.

---

## What Learnexia actually has

Inspected 2026-08-19. Learnexia's intake is **a file-and-convention system, not a tool**:

| Element | Learnexia | Scale |
|---|---|---|
| User stories | `user-stories/<Phase>/<ID>-<slug>.md` + `README.md` index | 128 story files |
| Per-stack tasks | `tasks/Backend/<Phase>/<ID>-BE.md`, `tasks/Frontend/.../<ID>-FE.md` + `README.md` | 189 task files |
| Task IDs | `<StoryID>-BE-n` / `<StoryID>-FE-n` | — |
| Pipeline briefs | `docs/briefs/<story>.md` (written by the `analyzer` agent) | — |
| Execution plans | `docs/plans/<story>.md` (written by the `planner` agent) | — |
| QC test plans | `docs/qc/<StoryID>/` (written by `qc-test-designer`) | — |
| Shared memory | `docs/dev/HANDOFF.md`, auto-loaded by a `SessionStart` hook | — |

There is **no database, no Jira, no issue tracker, no runtime** — just markdown in git, plus a hard rule in `CLAUDE.md`:

> Any scope agreed with the lead MUST become user story file(s) **and** per-stack task files, listed in the READMEs, **before** it is implemented — but **ask the lead first** and get explicit approval on the breakdown. Decisions do not live only in chat.

A story file carries: title, phase/epic, story points, labels, requirement refs, description, **acceptance criteria**, and notes/overrides.

## Why reuse it

- It is **portable by construction.** Markdown in git needs no service, no licence and no migration — copying the convention costs nothing and creates no coupling.
- It already satisfies the reproducibility and traceability requirements (§28/§29): acceptance criteria are versioned beside the code, and every phase gate can point at them.
- The ported agent roster (`analyzer`, `planner`, `reviewer`, `committer`) **already expects this layout** — they read stories and task files as their spec. Inventing a different one would mean rewriting all four.
- Building a second task system would violate the standing instruction not to duplicate infrastructure.

## How isolation is preserved

- ICT-Kronos keeps **its own** `user-stories/` and `tasks/` trees in **this** repository. Nothing is written into Learnexia.
- **No educational workflow is modified.** Learnexia was opened read-only throughout Phase 0 and Phase 1.5; its `git status` is clean and its HEAD unchanged.
- Only the *convention* is shared. There is no shared file, no shared index, no cross-repo reference, and no dependency in either direction.
- The phase vocabulary differs deliberately: Learnexia phases are product phases (`P1-01`, Identity & Onboarding); ours are research phases (`R2-01`, ICT Engine). The IDs cannot collide or be confused.

## The convention as adopted here

```
user-stories/
  Phase-2-ICT-Engine/
    R2-01-session-detector.md
    R2-02-swing-detection.md
    ...
  README.md                     # index + conventions

tasks/
  Phase-2-ICT-Engine/
    R2-01-TASKS.md              # R2-01-1, R2-01-2, ...
  README.md
```

**Single-stack simplification.** Learnexia splits tasks into `Backend/` and `Frontend/` because it has two stacks. ICT-Kronos is one Python codebase until Phase 10, so tasks are **not** split by stack — a `Backend/`+`Frontend/` split here would be empty ceremony. If Phase 10 adds a UI, the split is introduced then.

**The ask-first rule carries over, unchanged.** Scope agreed in conversation must become story + task files before implementation, and the breakdown (IDs, titles, scope split) is proposed to the lead for approval **before** the files are authored. No story generation happens unilaterally.

**Additional binding rule for this repo:** every story touching feature computation must state, in its acceptance criteria, **what the leakage test is**. A detector story without a leakage criterion is not ready to implement.

## Status

The convention is recorded here and the directories will be created **when the lead approves the Phase 2 story breakdown** — per the ask-first rule, not pre-emptively.

> **Superseded, kept for provenance.** The breakdown below was the *proposal*. The
> approved and implemented numbering differs (R2-01 sessions, R2-02 swings, R2-03
> structure, R2-04 liquidity, R2-05 FVG, R2-05.1 True Daily Open, R2-05.2 … R2-05.9 the
> composite layer, R2-06 premium/discount). **R2-01 → R2-05.9 are complete**; R2-06 is
> the next story. The live indexes are [user-stories/](../../user-stories/README.md) and
> [tasks/](../../tasks/README.md) — read those, not this table.

Proposed Phase 2 breakdown, for approval:

| ID | Title | Notes |
|---|---|---|
| `R2-01` | Session detector (Asian / London / New York) | Blocks everything else; DST-aware; owns the weekend/holiday judgement the normalizer withholds |
| `R2-02` | Kill zones (London, New York) | Depends on R2-01 |
| `R2-03` | Swing high / swing low detection | First detector needing `confirmation_timestamp` |
| `R2-04` | Market structure (HH/HL/LH/LL, BOS, MSS) | Depends on R2-03 |
| `R2-05` | Liquidity (equal highs/lows, PDH/PDL, PWH/PWL, session H/L, sweeps) | Depends on R2-01, R2-03 |
| `R2-06` | Fair Value Gaps (size, age, fill %, invalidation) | Independent of R2-03 |
| `R2-07` | Premium / Discount (dealing range, equilibrium, position) | Depends on R2-03 |

Order and dependencies follow [IMPLEMENTATION_ROADMAP.md](../financial-ai/IMPLEMENTATION_ROADMAP.md) Phase 2.
