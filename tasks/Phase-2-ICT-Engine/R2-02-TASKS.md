# R2-02 — Swing detection — tasks

Story: [R2-02](../../user-stories/Phase-2-ICT-Engine/R2-02-swing-detection.md)

| ID | Task | Notes | Status |
|---|---|---|---|
| R2-02-1 | `SwingConfig` — left/right/tie_policy, with `right >= 1` enforced | A zero-lag pivot is not a swing | ✅ |
| R2-02-2 | `TiePolicy` — FIRST / LAST / STRICT / ALL for plateaus | Not left to `>` vs `>=` accident | ✅ |
| R2-02-3 | Vectorised fractal detection via rolling windows | Millions of bars; loop is too slow | ✅ |
| R2-02-4 | `reference_pivots()` naive reference implementation | Test-only; proves the rolling path | ✅ |
| R2-02-5 | `SwingPoint` + `IctEvent` emission with both timestamps | confirmation = close of bar `i+right` | ✅ |
| R2-02-6 | `observable_at()` + `filter_observable()` / `assert_observable()` in the contract | The one downstream gate | ✅ |
| R2-02-7 | Config wiring — `SwingDetectionConfig`, env-overridable | CLAUDE.md rule 4 | ✅ |
| R2-02-8 | Unit tests: detection, ties, boundaries, insufficient history, config | | ✅ |
| R2-02-9 | Vectorised-vs-naive equivalence across all policies and window shapes | | ✅ |
| R2-02-10 | **Immutability tests** — a confirmed swing is never revised | | ✅ |
| R2-02-11 | **Batch vs streaming-replay tests**, incl. bar-by-bar | | ✅ |
| R2-02-12 | **Leakage tests** — nothing observable before confirmation | Mandatory | ✅ |
| R2-02-13 | **Real-data acceptance** — EURUSD + XAUUSD, 1m/5m/15m, gap/weekend | | ✅ |
| R2-02-14 | Interaction tests with the R2-01 session detector | Shared contract | ✅ |
| R2-02-15 | **Documentation** — `docs/ict/swings.md` incl. immutability semantics | Mandatory | ✅ |
