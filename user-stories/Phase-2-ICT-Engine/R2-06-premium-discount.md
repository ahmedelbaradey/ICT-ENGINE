# R2-06 — Premium / Discount (dealing range, equilibrium, position)

- **Project:** ICT-Kronos
- **Phase:** Phase 2 — ICT Engine
- **Epic:** Deterministic ICT representation
- **Issue type:** Story
- **Story points:** 3
- **Labels:** `ict`, `premium-discount`, `leakage`
- **Depends on:** R2-02 (swings define the range)
- **Blocks:** R2-07

## Description

As a quantitative researcher, I want a deterministic dealing range with premium/discount classification, so that "price is in discount" is a reproducible number rather than a chart impression.

## Scope

Dealing range, `range_high`, `range_low`, equilibrium, premium, discount, position within range.

## Acceptance criteria

1. **The dealing-range definition is explicit and configurable.** Candidate definitions — last confirmed swing high to last confirmed swing low; the highest high / lowest low over a lookback; the current structural leg — are documented in `docs/ict/premium_discount.md`, with one stated default. **We do not silently assume one ICT interpretation.**
2. The range is built **only from confirmed swings** (R2-02) or from bars closed at or before the evaluation instant. Never from a swing whose confirmation bar has not closed.
3. `equilibrium = (range_high + range_low) / 2` — the 50% level, documented.
4. `position_ratio = (price - range_low) / (range_high - range_low)`, so `0.0` = range low, `0.5` = equilibrium, `1.0` = range high. Values outside `[0, 1]` are legal (price beyond the range) and are **not** clamped silently; the behaviour is documented.
5. `zone` is `premium` when `position_ratio > 0.5`, `discount` when `< 0.5`, `equilibrium` at exactly `0.5` — with the boundary convention documented and the threshold configurable.
6. A degenerate range (`range_high == range_low`) is handled explicitly — no division by zero, a documented sentinel — and tested.
7. Output includes: `range_high`, `range_low`, `equilibrium`, `current_price`, `position_ratio`, `zone`, plus the observation timestamp.
8. **LEAKAGE CRITERION (mandatory):** tests prove the range at time *t* uses only information observable at *t*, and that batch equals streaming replay.

## Test coverage required

- Price at range low, equilibrium, range high
- Price above and below the range (ratio outside [0, 1])
- Degenerate zero-width range
- Range definition variants under configuration
- Batch vs streaming replay
- Leakage: no unconfirmed swing enters the range
- Real data: EURUSD + XAUUSD 2024-03-08 → 2024-03-12

## Notes and decisions

- Premium/discount is arithmetically trivial; **all the risk is in which range you choose**, which is exactly why the definition is configuration with a documented default rather than a buried constant.

## Out of scope

Optimal Trade Entry (OTE) zones and Fibonacci sub-levels.
