# Tasks — index and conventions

Per-story task breakdown. Convention ported from Learnexia (see [docs/dev/WORK_INTAKE.md](../docs/dev/WORK_INTAKE.md)); files are ours, in our repository.

## Conventions

- One task file per story: `tasks/<Phase>/<StoryID>-TASKS.md`.
- Task IDs are `<StoryID>-<n>`, e.g. `R2-01-3`.
- **Not split by stack.** Learnexia splits `Backend/` vs `Frontend/` because it has two stacks; ICT-Kronos is one Python codebase until Phase 10, so a stack split here would be empty ceremony. It is introduced only if Phase 10 adds a UI.
- Every story's task list ends with a **documentation task** and a **leakage-test task**. Neither is optional.

## Phase 2 — ICT Engine

| Story | Tasks | Status |
|---|---|---|
| [R2-01](Phase-2-ICT-Engine/R2-01-TASKS.md) | Session detector | ✅ Done |
| [R2-02](Phase-2-ICT-Engine/R2-02-TASKS.md) | Swing detection | ✅ Done |
| [R2-03](Phase-2-ICT-Engine/R2-03-TASKS.md) | Market structure | ✅ Done |
| [R2-04](Phase-2-ICT-Engine/R2-04-TASKS.md) | Liquidity | ✅ Done |
| [R2-05](Phase-2-ICT-Engine/R2-05-TASKS.md) | Fair Value Gap | ✅ Done |
| [R2-05.1](Phase-2-ICT-Engine/R2-05.1-TASKS.md) | True Daily Open | ✅ Done |
| [R2-05.2](Phase-2-ICT-Engine/R2-05.2-TASKS.md) | IFVG | ✅ Done |
| [R2-05.3](Phase-2-ICT-Engine/R2-05.3-TASKS.md) | Order Block | ✅ Done |
| [R2-05.4](Phase-2-ICT-Engine/R2-05.4-TASKS.md) | Breaker Block | ✅ Done |
| [R2-05.5](Phase-2-ICT-Engine/R2-05.5-TASKS.md) | Balanced Price Range | ✅ Done |
| [R2-05.6](Phase-2-ICT-Engine/R2-05.6-TASKS.md) | RDRB | ✅ Done |
| [R2-05.7](Phase-2-ICT-Engine/R2-05.7-TASKS.md) | CISD | ✅ Done |
| [R2-05.8](Phase-2-ICT-Engine/R2-05.8-TASKS.md) | CHoCH revision | ✅ Reviewed — no change |
| [R2-05.9](Phase-2-ICT-Engine/R2-05.9-TASKS.md) | Unicorn Model | ✅ Done |
| [R2-06](Phase-2-ICT-Engine/R2-06-TASKS.md) | Premium / Discount | ✅ Done |
| [R2-07](Phase-2-ICT-Engine/R2-07-TASKS.md) | ICT feature integration | ✅ Done |
