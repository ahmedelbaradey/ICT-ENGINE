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
| [R2-03](Phase-2-ICT-Engine/R2-03-market-structure.md) | Market structure — HH/HL/LH/LL, BOS, MSS, CHoCH | R2-02 | ✅ Done |
| [R2-04](Phase-2-ICT-Engine/R2-04-liquidity.md) | Liquidity — equal highs/lows, PDH/PDL, PWH/PWL, session H/L, sweeps | R2-01, R2-02 | ✅ Done |
| [R2-05](Phase-2-ICT-Engine/R2-05-fair-value-gap.md) | Fair Value Gaps — size, age, fill %, invalidation | R2-01 | ✅ Done |
| [R2-05.1](Phase-2-ICT-Engine/R2-05.1-true-daily-open.md) | True Daily Open — 00:00 America/New_York | R2-01 | ✅ Done |
| [R2-05.2](Phase-2-ICT-Engine/R2-05.2-inversion-fair-value-gap.md) | IFVG — inversion of a confirmed FVG | R2-05 | 📋 Spec written |
| [R2-05.3](Phase-2-ICT-Engine/R2-05.3-order-block.md) | Order Block — pattern + qualifying event | R2-03, R2-04, R2-05 | 📋 Spec written |
| [R2-05.4](Phase-2-ICT-Engine/R2-05.4-breaker-block.md) | Breaker Block — a failed Order Block | R2-05.3 | 📋 Spec written |
| [R2-05.5](Phase-2-ICT-Engine/R2-05.5-balanced-price-range.md) | BPR — intersection of opposite FVGs | R2-05 | 📋 Spec written |
| [R2-05.6](Phase-2-ICT-Engine/R2-05.6-rdrb.md) | RDRB — redelivered rebalanced price range | — | 📋 Spec written ⚠ decision required |
| [R2-05.7](Phase-2-ICT-Engine/R2-05.7-cisd.md) | CISD — change in state of delivery | — | 📋 Spec written |
| [R2-05.8](Phase-2-ICT-Engine/R2-05.8-choch-revision.md) | CHoCH semantics revision | R2-03, R2-05.7 | 📋 Spec written |
| [R2-05.9](Phase-2-ICT-Engine/R2-05.9-unicorn-model.md) | Unicorn — Breaker ∩ FVG | R2-05.4, R2-05 | 📋 Spec written |
| [R2-06](Phase-2-ICT-Engine/R2-06-premium-discount.md) | Premium / Discount — dealing range, equilibrium, position | R2-02 | ⛔ Deferred until R2-05.9 |
| [R2-07](Phase-2-ICT-Engine/R2-07-ict-feature-integration.md) | `ICTMarketState` / `ICTFeatureVector` integration | R2-01…R2-06 | ⛔ Deferred |

## Out of scope for Phase 2

Mitigation/Rejection blocks, volume imbalance, liquidity voids, SMT, OTE (deferred). Order Blocks, Breakers, IFVG, BPR, RDRB, CISD and Unicorn were originally deferred here and are now specified as R2-05.2…R2-05.9 — see [R2-05x-CONCEPT-MAP.md](../docs/ict/R2-05x-CONCEPT-MAP.md). Kronos, ML training, backtesting and execution are later phases.
