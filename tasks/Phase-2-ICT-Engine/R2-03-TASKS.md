# R2-03 — Market structure — tasks

Story: [R2-03](../../user-stories/Phase-2-ICT-Engine/R2-03-market-structure.md)

| ID | Task | Notes | Status |
|---|---|---|---|
| R2-03-1 | **Document the definitions FIRST** — `docs/ict/structure.md` | Written before any code | ✅ |
| R2-03-2 | `StructureState` / `BreakMode` / `ChochPolicy` / `StructureConfig` | All contested choices are config | ✅ |
| R2-03-3 | HH/HL/LH/LL from the PREVIOUS same-type confirmed swing only | Never a later swing | ✅ |
| R2-03-4 | Reference-level tracking; consumed on break | One level cannot break twice | ✅ |
| R2-03-5 | BOS/MSS as one detection distinguished by prior state | Same break, different label | ✅ |
| R2-03-6 | CHoCH policy — SYNONYM (default) vs DISTINCT_BY_DISPLACEMENT | Stated, not faked | ✅ |
| R2-03-7 | Bar-forward walk admitting swings only at their confirmation | The R2-02 dependency, structural | ✅ |
| R2-03-8 | `StructureBreak` with the full transition for R2-07 | Not an opaque recogniser | ✅ |
| R2-03-9 | `state_at()` / `observable_at()` point-in-time API | | ✅ |
| R2-03-10 | Significance filter — `min_swing_strength_points` | Simplest deterministic knob | ✅ |
| R2-03-11 | Config wiring — `StructureDetectionConfig`, env-overridable | CLAUDE.md rule 4 | ✅ |
| R2-03-12 | Unit tests: labels, breaks, modes, ties, boundaries, state machine | | ✅ |
| R2-03-13 | **Leakage tests**, incl. a direct proof the filter constrains output | Mandatory | ✅ |
| R2-03-14 | **Immutability tests** — a future candle cannot revise a confirmed event | | ✅ |
| R2-03-15 | **Batch vs streaming**, prefix + candle-by-candle | | ✅ |
| R2-03-16 | **Real-data acceptance** — EURUSD + XAUUSD on 1M/5M/15M | | ✅ |
| R2-03-17 | Weekend-gap, session and DST tests | | ✅ |
