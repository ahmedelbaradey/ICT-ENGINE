# R2-07 — ICT feature integration — tasks

Story: [R2-07](../../user-stories/Phase-2-ICT-Engine/R2-07-ict-feature-integration.md) ·
State layer: [market_state.md](../../docs/ict/market_state.md) ·
Feature catalogue: [features.md](../../docs/ict/features.md)

**Status: complete.** `ict_kronos/ict/market_state.py` + `ict_kronos/ict/feature_vector.py`,
`state_version` / `feature_version` = `r2-07.1`, 56 features.

| ID | Task | Notes | Status |
|---|---|---|---|
| R2-07-a | Implementation against the shared detector contract | Aggregation only — every value comes from a detector's own point-in-time API | ✅ |
| R2-07-b | Configuration wiring — no hardcoded trading constants | `MarketStateConfig`, one setting; detector configs injectable for reproducibility | ✅ |
| R2-07-c | Unit tests: normal, edge, malformed, boundary, timeframe, timestamp | 62 state + 59 vector | ✅ |
| R2-07-d | **Batch vs streaming-replay equivalence** | prefix at every cut, true bar-by-bar, states AND vectors | ✅ |
| R2-07-e | **Leakage tests** | future / confirming-bar / control / naive, plus a per-component sweep | ✅ |
| R2-07-f | **Real-data acceptance** — EURUSD + XAUUSD | 1m/5m/15m/1H/4H | ✅ |
| R2-07-g | **Documentation** | `market_state.md` (13 §) + `features.md` (13 §, every feature's unit / range / missing semantics) | ✅ |

## Story-specific tasks

| ID | Task | Notes | Status |
|---|---|---|---|
| R2-07-1 | Immutable timestamped observation model | `ObservationBar`; tuples not lists; no detector internals | ✅ |
| R2-07-2 | `ICTMarketState` covering all ten detector families | 9 frozen context records | ✅ |
| R2-07-3 | CHoCH via the existing `ChochPolicy` — no second algorithm | `choch_count` is 0 under the default, and that is correct | ✅ |
| R2-07-4 | A sweep never makes an unobservable level observable | Asserted on synthetic and real bars | ✅ |
| R2-07-5 | Mitigation never reinterpreted as inversion | IFVG counts genuine inversions only | ✅ |
| R2-07-6 | Unicorn provenance inherited whole, no duplicated geometry | id + FVG + Breaker + transitive Order Block | ✅ |
| R2-07-7 | True Daily Open = 00:00 America/New_York, no second DST rule | Guard test asserts the module defines no timezone | ✅ |
| R2-07-8 | R2-06 premium/discount consumed, `percentage_position` unclamped | Real data leaves `[0, 1]` as the common case | ✅ |
| R2-07-9 | Bias exposes evidence independently; never forces a direction | Counting, not scoring; UNKNOWN ≠ NEUTRAL | ✅ |
| R2-07-10 | Immutable, versioned, serializable `ICTFeatureVector` | 56 features, `FEATURE_NAMES` is the schema | ✅ |
| R2-07-11 | Every distance declares its unit | All `*_points`; prices and points never mixed | ✅ |
| R2-07-12 | Explicit missing-value representation, never silent zero | `None` in dicts, `nan` in rows; counts stay real zeros | ✅ |
| R2-07-13 | ID-based provenance for every component | `source_ids()` + resolution tests on both symbols | ✅ |
| R2-07-14 | Detector lifecycles preserved, no universal lifecycle invented | Each read through its own API | ✅ |
| R2-07-15 | Deterministic event selection and ordering | `(confirmation, event, id)`; sorted id tuples | ✅ |
| R2-07-16 | Timeframe-local; no fabricated timeframes | Guard tests ban HTF joins and `D1`/`W1` | ✅ |
| R2-07-17 | Deterministic serialization + round-trip | `from_dict(as_dict()) == v` on every real vector | ✅ |
| R2-07-18 | Performance measured, not prematurely optimised | Two genuine design fixes, then measured; see HANDOFF | ✅ |
| R2-07-19 | Regression: R2-01 → R2-06 untouched | No approved detector source changed | ✅ |
| R2-07-20 | Full suite + ruff + black; one local commit; STOP | No push | ✅ |

## Divergence from the story text

The in-repo story anticipated multi-timeframe assembly via `align_htf_context()`. The
R2-07 execution brief directed that this story remain **timeframe-local** and that no
HTF projection be implemented. The brief was followed; the divergence is recorded in
[market_state.md](../../docs/ict/market_state.md) §10 rather than silently resolved.
`align_htf_context()` remains the only sanctioned join when HTF context is authorised,
and nothing in this layer needs restructuring to accept it.
