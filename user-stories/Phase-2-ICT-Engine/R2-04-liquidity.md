# R2-04 — Liquidity (equal highs/lows, PDH/PDL, PWH/PWL, session H/L, sweeps)

- **Project:** ICT-Kronos
- **Phase:** Phase 2 — ICT Engine
- **Epic:** Deterministic ICT representation
- **Issue type:** Story
- **Story points:** 8
- **Labels:** `ict`, `liquidity`, `leakage`
- **Depends on:** R2-01 (sessions), R2-02 (swings)
- **Blocks:** R2-07

## Description

As a quantitative researcher, I want deterministic liquidity levels and sweep detection, so that "price is reaching for the previous day high" becomes a timestamped, testable feature.

Liquidity is where look-ahead leakage is easiest to introduce accidentally: a "previous day high" computed from a full daily bar is not knowable until that day has closed, and a level drawn on today's chart from today's not-yet-final high is pure future information.

## Scope

**Levels:** Equal Highs, Equal Lows, Buy-side liquidity, Sell-side liquidity, Previous Day High/Low, Previous Week High/Low, Session High/Low.
**Events:** liquidity sweep.

## Acceptance criteria

1. **A level only exists once it is observable.** PDH/PDL become available at the daily close that produced them, PWH/PWL at the weekly close, session H/L at session end (R2-01). Never earlier.
2. Equal highs/lows use a **configurable tolerance** (in points/pips or ATR fraction), documented, never a hardcoded epsilon.
3. Buy-side/sell-side classification is explicit: buy-side liquidity rests **above** highs, sell-side **below** lows — documented so the convention cannot be misread.
4. A **sweep is confirmed only when the required price action has actually occurred** — the documented, configurable rule (e.g. wick through the level, optionally with a close back inside). `sweep_timestamp` is the close time of the confirming bar.
5. Each liquidity object exposes: `liquidity_type`, `price_level`, `created_timestamp`, `confirmation_timestamp`, `swept`, `sweep_timestamp`, `distance_from_price`, `strength`, `timeframe` — plus the common contract fields.
6. `distance_from_price` is evaluated **point-in-time** against the price observable at the evaluation instant, never against a future price.
7. **No future information creates a historical level.** Recomputing levels over a truncated history must give the same levels as streaming replay.
8. **LEAKAGE CRITERION (mandatory):** tests prove (a) no level is observable before its `confirmation_timestamp`, (b) no sweep is flagged before its confirming bar closes, and (c) batch equals streaming replay.

## Test coverage required

- Equal highs/lows at, just inside, and just outside tolerance
- PDH/PDL across a day boundary, including the weekend
- PWH/PWL across a week boundary
- Session H/L for all three sessions
- Sweep confirmed / not confirmed (wick through vs close beyond, per configuration)
- Sweep of an unswept vs already-swept level
- Batch vs streaming replay
- Leakage tests per level type and for sweeps
- Real data: EURUSD + XAUUSD 2024-03-08 → 2024-03-12 (the weekend gap makes "previous day" non-trivial)

## Notes and decisions

- **"Previous day" is ambiguous in FX** — the day boundary can be UTC midnight, New York 17:00, or the broker's server day. This materially changes PDH/PDL. The choice is documented and configurable; it is **not** silently assumed.
- The weekend gap means "previous day" is often not the previous calendar day. Handled explicitly via the session/holiday calendar from R2-01.

## Out of scope

Internal vs external liquidity ranking, liquidity voids, and any trade-decision logic.
