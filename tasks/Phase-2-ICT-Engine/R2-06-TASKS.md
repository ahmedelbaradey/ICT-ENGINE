# R2-06 — Premium / Discount — tasks

Story: [R2-06](../../user-stories/Phase-2-ICT-Engine/R2-06-premium-discount.md) ·
Candidates: [R2-06-CONCEPT-MAP.md](../../docs/ict/R2-06-CONCEPT-MAP.md) ·
Semantics: [dealing_range.md](../../docs/ict/dealing_range.md)

**Status: complete.** Implemented in `ict_kronos/ict/dealing_range.py` with 91 unit
tests and 219 real-data tests.

| ID | Task | Notes | Status |
|---|---|---|---|
| R2-06-a | Implementation against the shared detector contract | `DealingRange` + `RangeObservation`; ranges emit `EventType.DEALING_RANGE` | ✅ |
| R2-06-b | Configuration wiring — no hardcoded trading constants | `DealingRangeConfig`, two settings, two env vars | ✅ |
| R2-06-c | Unit tests: normal, edge, malformed, boundary, timeframe, timestamp | 91 tests | ✅ |
| R2-06-d | **Batch vs streaming-replay equivalence** | every cut, prefix + true bar-by-bar, plus the observation stream | ✅ |
| R2-06-e | **Leakage tests** | future / boundary / control / naive-hindsight, each with a control | ✅ |
| R2-06-f | **Real-data acceptance** — EURUSD + XAUUSD | 1m/5m/15m/1H/4H; 4H is a genuine zero and is reported as one | ✅ |
| R2-06-g | **Documentation** | concept map (5 candidates ranked) + `dealing_range.md` (15 sections) | ✅ |

## Story-specific tasks

| ID | Task | Notes | Status |
|---|---|---|---|
| R2-06-1 | Evaluate and rank all five range candidates BEFORE code | Concept map §3; B selected, A/C/D/E documented only | ✅ |
| R2-06-2 | Exactly ONE deterministic production default | No range-definition knob exists; asserted by a test | ✅ |
| R2-06-3 | Anchors are confirmed swings only | Routed through the shared gate | ✅ |
| R2-06-4 | `confirmation >= max(anchors, trigger)` | `composite_confirmation`; verified `==` on real data | ✅ |
| R2-06-5 | Immutability — a later break never rewrites an earlier range | Frozen records; supersession is a separate stream | ✅ |
| R2-06-6 | Identity from causal source identity, not price/bar | `range_id` carries both anchor ids | ✅ |
| R2-06-7 | Direction fixed at creation, never inferred from price | Inherited from the break | ✅ |
| R2-06-8 | Classification is a SEPARATE record | `RangeObservation`; the range is never mutated | ✅ |
| R2-06-9 | Unclamped normalised position | 42–81% of real observations fall outside `[0, 1]` | ✅ |
| R2-06-10 | Degenerate range deterministic, no division by zero | `position = NaN`, `zone` still defined | ✅ |
| R2-06-11 | No second timezone/DST/session implementation | Guard test asserts the module contains none | ✅ |
| R2-06-12 | Weekend / DST / incomplete HTF bar coverage | Real 2024-03-10 transition and the real closure | ✅ |
| R2-06-13 | Source-level guard against hand-rolled observability | Docstring-stripping guard, plus a test that the stripper works | ✅ |
| R2-06-14 | Timeframe-local only — no HTF projection | Documented as deferred to the feature layer | ✅ |
| R2-06-15 | Performance measured, not optimised | 190 ms / 2933 1m bars; no hotspot worth acting on | ✅ |
| R2-06-16 | Full suite + ruff + black; one local commit; STOP | No push | ✅ |
