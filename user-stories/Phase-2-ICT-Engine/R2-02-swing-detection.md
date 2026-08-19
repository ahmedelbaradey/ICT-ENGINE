# R2-02 — Swing high / swing low detection

- **Project:** ICT-Kronos
- **Phase:** Phase 2 — ICT Engine
- **Epic:** Deterministic ICT representation
- **Issue type:** Story
- **Story points:** 5 — the confirmation-lag semantics carry the cost, not the comparison
- **Labels:** `ict`, `structure`, `leakage`
- **Depends on:** R2-01
- **Blocks:** R2-03 (market structure), R2-04 (liquidity), R2-06 (premium/discount)

## Description

As a quantitative researcher, I want deterministic swing-high and swing-low detection with explicit confirmation lag, so that market structure can be built on pivots that were genuinely knowable at the time they are used.

Swings are the canonical look-ahead trap in ICT systems: a swing high at bar *i* is only a swing once *n* subsequent bars have failed to exceed it. Charting software draws it at bar *i*, which is exactly the timestamp a naive implementation records — and that timestamp is wrong by *n* bars.

## Scope

- Swing High, Swing Low
- Configurable left/right lookback (confirmation window)
- Explicit `event_timestamp` (the pivot bar) vs `confirmation_timestamp` (when it became knowable)

## Acceptance criteria

1. A swing high at bar *i* requires `high[i]` to be the strict maximum over `[i - left, i + right]`; swing low is the mirror. `left` and `right` are **configuration**, defaulting to a documented value.
2. **`event_timestamp` = the pivot bar's open time.** **`confirmation_timestamp` = the close time of bar `i + right`** — the first instant the pivot could be known. These are never the same value when `right > 0`.
3. A swing is **not emitted at all** until its confirmation bar has closed. Batch detection over a truncated history must produce exactly the prefix that streaming replay produces.
4. Tie/plateau handling (equal highs inside the window) is explicitly defined, documented, and tested — not left to comparison-operator accident.
5. Insufficient history (fewer than `left + right + 1` bars) yields no swing, not an error.
6. Gaps in the bar series do not silently create false pivots — the window is over **bars present**, and the behaviour is documented.
7. Events carry the full Phase 2 detector contract.
8. **LEAKAGE CRITERION (mandatory):** a test proves no swing is observable before its `confirmation_timestamp`, and that `batch(history[:k]) == replay(history[:k])` for every *k*.

## Test coverage required

- Normal case, both directions
- Boundary: pivot at the very start / very end of the series
- Insufficient history
- Plateaus and equal highs/lows
- Configurable `left`/`right` values
- Batch vs streaming replay
- Leakage: confirmation lag is real and enforced
- Real data: EURUSD + XAUUSD 2024-03-08 → 2024-03-12

## Notes and decisions

- **Ambiguity to document, not resolve silently:** ICT practitioners use fractal (n-bar), zigzag/ATR, and structural definitions of a swing. We implement the **n-bar fractal** definition as the default because it is deterministic, streamable and testable, and we record the alternatives as configurable/future.
- The confirmation-lag design is the direct lesson from `ForexQuant/FvgDetectionService.cs` (see `docs/financial-ai/LEGACY_RESEARCH.md` §5.1).

## Out of scope

Swing strength ranking beyond a documented basic measure; higher-timeframe swing projection (R2-07 handles MTF assembly).
