# R2-04 — Liquidity — tasks

Story: [R2-04](../../user-stories/Phase-2-ICT-Engine/R2-04-liquidity.md)

| ID | Task | Notes | Status |
|---|---|---|---|
| R2-04-1 | **Document definitions FIRST** — `docs/ict/liquidity.md` | Written before code | ✅ |
| R2-04-2 | `LiquidityLevel` and `LiquiditySweep` as SEPARATE types | A level is not a sweep | ✅ |
| R2-04-3 | Swing + equal-high/low levels from CONFIRMED swings only | Equal confirms at `max` of the pair | ✅ |
| R2-04-4 | Session levels by reusing R2-01 `SessionDetector` | No boundary reimplementation | ✅ |
| R2-04-5 | Trading day = 17:00 NY window, expressed as a `SessionDefinition` | DST-aware by construction | ✅ |
| R2-04-6 | Trading week = Sunday…Thursday day windows | Fri/Sat excluded (see docs §13) | ✅ |
| R2-04-7 | Buy/sell side fixed at creation, never price-relative | Preserves immutability | ✅ |
| R2-04-8 | Lifecycle: PENDING / ACTIVE / (APPROACHED) / SWEPT | Three essential; approach opt-in | ✅ |
| R2-04-9 | Sweep: wick penetration, confirms at bar close, `closed_beyond` recorded | `require_rejection` deliberately removed | ✅ |
| R2-04-10 | One sweep event per level; identities never collapsed | Multi-level policy | ✅ |
| R2-04-11 | `PendingPeriod` exposure for in-progress periods | Never emitted as levels | ✅ |
| R2-04-12 | `state_of()` — the full ML view of a level | R2-07 data model | ✅ |
| R2-04-13 | Config wiring — `LiquidityDetectionConfig`, env-overridable | CLAUDE.md rule 4 | ✅ |
| R2-04-14 | Unit tests: levels, sweeps, lifecycle, calendar, config | | ✅ |
| R2-04-15 | **Leakage tests** incl. a naive-implementation proof | Mandatory | ✅ |
| R2-04-16 | **Batch vs prefix vs bar-by-bar replay** | | ✅ |
| R2-04-17 | **R2-03 separation** — a wick sweep is not a BOS | | ✅ |
| R2-04-18 | **Real-data acceptance** — EURUSD + XAUUSD on 1M/5M/15M | | ✅ |
| R2-04-19 | Weekend and DST tests on real data | | ✅ |
