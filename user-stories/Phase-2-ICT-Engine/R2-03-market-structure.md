# R2-03 — Market structure (HH / HL / LH / LL, BOS, MSS, CHoCH)

- **Project:** ICT-Kronos
- **Phase:** Phase 2 — ICT Engine
- **Epic:** Deterministic ICT representation
- **Issue type:** Story
- **Story points:** 8 — the largest interpretation surface in Phase 2
- **Labels:** `ict`, `structure`, `leakage`
- **Depends on:** R2-02
- **Blocks:** R2-04, R2-07

## Description

As a quantitative researcher, I want deterministic market-structure labelling and break detection, so that "the trend is bullish" and "structure just shifted" become measurable, timestamped facts rather than chart-reading opinions.

## Scope

- Swing classification: HH, HL, LH, LL
- BOS — Break of Structure (continuation)
- MSS — Market Structure Shift (reversal)
- CHoCH — Change of Character, **only if** the implementation distinguishes it explicitly from MSS; if it does not, that is recorded as a deliberate decision rather than silently conflated

## Acceptance criteria

1. Classification is computed **only from confirmed swings** (R2-02), so structure inherits the swing confirmation lag rather than bypassing it.
2. A BOS/MSS is confirmed by a documented, configurable rule — candidate rules being *wick break* vs *close beyond the reference level*. The default is stated explicitly and is overridable.
3. **`confirmation_timestamp` = the close time of the bar that satisfies the break condition**, never the bar's open, and never the reference swing's timestamp.
4. `reference_level` names the swing level that was broken, so any event can be audited back to its cause.
5. `direction` is explicit (bullish/bearish). `strength` is a documented, deterministic measure — not a free parameter tuned to taste.
6. BOS and MSS are **distinguishable by definition**, and the distinction is documented (continuation vs shift relative to prevailing structure).
7. **No LLM is involved in identifying structure.** Pure algorithm (CLAUDE.md rules 2 and 3).
8. Every event carries: `symbol`, `timeframe`, `event_type`, `direction`, `event_timestamp`, `confirmation_timestamp`, `price_level`, `reference_level`, `strength`.
9. **LEAKAGE CRITERION (mandatory):** tests prove a structure event is never emitted before its confirmation is knowable, and that batch equals streaming replay.

## Test coverage required

- Each of HH, HL, LH, LL
- BOS bullish and bearish
- MSS bullish and bearish
- CHoCH if implemented (else an explicit test asserting the documented non-distinction)
- Break-rule variants (wick vs close) under configuration
- Boundary: break exactly at the reference level
- Batch vs streaming replay
- Leakage tests per event type
- Real data: EURUSD + XAUUSD 2024-03-08 → 2024-03-12

## Notes and decisions

- **Heavily contested terminology.** BOS/MSS/CHoCH are used inconsistently across ICT sources; some treat CHoCH and MSS as synonyms. Per the documentation rule we state our definitions precisely in `docs/ict/structure.md` and make the break rule configurable, rather than asserting one community reading as truth.
- Internal vs external (swing) structure is deferred; if the default needs a choice, it is documented.

## Out of scope

Displacement scoring, order blocks, premium/discount interaction (R2-06).
