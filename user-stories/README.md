# User stories — index and conventions

Work intake for ICT-Kronos. The convention is ported from Learnexia (see [docs/dev/WORK_INTAKE.md](../docs/dev/WORK_INTAKE.md)); the files are **ours**, in **our** repository. Nothing here references or modifies Learnexia.

## Conventions

- One story per file: `user-stories/<Phase>/<ID>-<slug>.md`.
- IDs are `R<phase>-<nn>` — `R` for *research* phase, so they can never be confused with Learnexia's product IDs (`P1-01`, …).
- Per-story tasks live in `tasks/<Phase>/<ID>-TASKS.md`, with task IDs `<ID>-<n>`.
- **Ask-first rule:** scope agreed in conversation becomes story + task files *before* implementation, and the breakdown is approved by the lead *before* the files are authored.
- **Leakage rule (this repo only):** every story that computes a feature MUST state its leakage acceptance criterion. A detector story without one is not ready to implement.

## Phase 2 — ICT Engine

Deterministic ICT market-structure detection. **No LLM decides whether a pattern exists** (CLAUDE.md rule 3). Execution order is strict — each story is completed and validated before the next begins.

| ID | Story | Depends on | Status |
|---|---|---|---|
| [R2-01](Phase-2-ICT-Engine/R2-01-session-detector.md) | Session detector (Asian / London / New York + kill zones) | Phase 1.5 | ✅ Done |
| [R2-02](Phase-2-ICT-Engine/R2-02-swing-detection.md) | Swing high / swing low detection | R2-01 | ✅ Done |
| [R2-03](Phase-2-ICT-Engine/R2-03-market-structure.md) | Market structure — HH/HL/LH/LL, BOS, MSS, CHoCH | R2-02 | ⬜ **Next** |
| [R2-04](Phase-2-ICT-Engine/R2-04-liquidity.md) | Liquidity — equal highs/lows, PDH/PDL, PWH/PWL, session H/L, sweeps | R2-01, R2-02 | ⬜ Not started |
| [R2-05](Phase-2-ICT-Engine/R2-05-fair-value-gap.md) | Fair Value Gaps — size, age, fill %, invalidation | R2-01 | ⬜ Not started |
| [R2-06](Phase-2-ICT-Engine/R2-06-premium-discount.md) | Premium / Discount — dealing range, equilibrium, position | R2-02 | ⬜ Not started |
| [R2-07](Phase-2-ICT-Engine/R2-07-ict-feature-integration.md) | `ICTMarketState` / `ICTFeatureVector` integration | R2-01…R2-06 | ⬜ Not started |

## Out of scope for Phase 2

Order Blocks, Breaker/Mitigation blocks, IFVG, BPR, RDRB, volume imbalance, liquidity voids (Master Plan §8 — deferred). Kronos, ML training, backtesting and execution are later phases.
