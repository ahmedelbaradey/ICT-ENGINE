# R2-05 — Fair Value Gaps (size, age, fill %, invalidation)

- **Project:** ICT-Kronos
- **Phase:** Phase 2 — ICT Engine
- **Epic:** Deterministic ICT representation
- **Issue type:** Story
- **Story points:** 5
- **Labels:** `ict`, `fvg`, `leakage`
- **Depends on:** R2-01
- **Blocks:** R2-07

## Description

As a quantitative researcher, I want deterministic Fair Value Gap detection with correct confirmation semantics, so that FVG features carry no look-ahead bias.

**This story exists partly to correct a known real-world bug.** `ForexQuant/…/FvgDetectionService.cs` carries the comment *"FIXED: Changed from candle2 to candle3 to exclude formation candles"* — someone hit the confirmation-timestamp problem in production, and **the fix is still one bar early**: it stamps the event at candle 3's *open* when the pattern depends on candle 3's *low/high*, which is not final until candle 3 *closes*. See `docs/financial-ai/LEGACY_RESEARCH.md` §5.1.

## Scope

Bullish FVG, Bearish FVG, boundaries, size, age, fill percentage, partial fill, full fill, invalidation.

## Acceptance criteria

1. The **exact candle pattern is documented** in `docs/ict/fvg.md` with explicit indices: for a bullish FVG over candles (1, 2, 3), `low[3] > high[1]`; bearish is the mirror, `high[3] < low[1]`. The gap boundaries are stated unambiguously.
2. Two distinct timestamps are emitted:
   - **`formation_timestamp`** — the pattern's location on the chart (candle 3's open time, documented).
   - **`confirmation_timestamp`** — **candle 3's `close_time`**, the first instant the FVG could be known.
   These are never equal, and the legacy off-by-one-bar error must be impossible by construction.
3. `size` is in price units with a documented normalisation option (points/pips/ATR). `age` is measured in bars **and** in time, from confirmation.
4. Fill percentage is computed **point-in-time** from bars closed at or before the evaluation instant; partial fill and full fill have documented thresholds and are configurable.
5. Invalidation has a documented rule with an `invalidation_timestamp` when it occurs.
6. Insufficient history (fewer than 3 bars) yields no FVG, not an error. Gaps in the series are handled explicitly and documented — three *consecutive present* bars, with the behaviour across a market gap stated.
7. **No code is copied from the legacy implementation.** Independently written and independently tested (instruction §15).
8. **LEAKAGE CRITERION (mandatory):** tests prove no FVG is observable before candle 3 closes, fill percentage never uses future bars, and batch equals streaming replay.

## Test coverage required

- Bullish FVG, bearish FVG
- Partial fill, full fill
- Invalidation
- Same-bar edge cases (gap exactly touching; zero-width gap)
- Market gaps / weekend between the three candles
- Insufficient history
- Batch vs streaming replay
- Leakage: confirmation timing and fill computation
- Real data: EURUSD + XAUUSD 2024-03-08 → 2024-03-12

## Notes and decisions

- **Ambiguity to document:** some practitioners require candle 2 to be a displacement/expansion candle; others do not. The base three-candle imbalance is the default, with the displacement filter available as configuration and off by default.
- Whether a gap is measured wick-to-wick or body-to-body is stated explicitly (wick-to-wick default) and configurable.

## Out of scope

IFVG, BPR, volume imbalance, liquidity voids (Master Plan §8 — deferred).
