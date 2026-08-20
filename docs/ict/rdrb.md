# RDRB — Redelivered Rebalanced Price Range

**Story:** R2-05.2 · **Module:** [`ict_kronos/ict/rdrb.py`](../../ict_kronos/ict/rdrb.py)

## 1. Definition — FOUR candles, authoritative for this engine

```
C1  ->  C2  ->  C3  ->  C4
```

| | Role |
|---|---|
| **C1** | opens the initial delivery |
| **C2** | continues it and prints the **protected wick extreme** |
| **C3** | the intervening continuation candle |
| **C4** | the redelivery candle, which must **not reach or violate** C2's protected wick |

```
bullish   valid iff   C4.low  >  C2.low        (C2.low  is protected)
bearish   valid iff   C4.high <  C2.high       (C2.high is protected)
```

The comparison is **wick to wick**. Never close-to-close, never body-to-body, never
body-to-wick.

**Equality is invalid.** `C4.low == C2.low` *reached* the protected extreme, and
reaching it is violating it.

> The two-candle and three-candle readings in circulation are **not implemented**. See
> §10.

## 2. Inputs

Bars only. RDRB consumes no other detector and is consumed by none.

## 3. Exact deterministic rule

For every window of four positionally adjacent candles:

1. **Contiguity** (`require_contiguous_bars`, default on) — `close_time[i]` must equal
   `timestamp[i+1]` across all four. Over a weekend or data hole they are not one
   delivery sequence.
2. **Directional prerequisites** — each candle's close direction, per config (§10).
3. **The protected-wick test** —
   `clearance = C4.low − C2.low` (bullish) or `C2.high − C4.high` (bearish), and
   `clearance > wick_tolerance_points × point_value`. Default tolerance **0**, giving
   the strict inequality.

**Zone**: the band between the protected wick and the validation wick.

```
bullish   zone_bottom = C2.low    zone_top = C4.low
bearish   zone_top    = C2.high   zone_bottom = C4.high
```

Non-empty exactly when the validity condition holds, so a degenerate zone is
impossible by construction.

## 4. Event timestamp

**C1's open** — where the sequence begins on the chart.

## 5. Confirmation timestamp

**C4's `close_time`.** The validity condition is a statement about C4, so no amount of
information at C1, C2 or C3 can establish it.

```
RDRB cannot be confirmed at C2.
RDRB cannot be confirmed at C3.
```

On contiguous bars the lag is exactly four bar durations from C1's open.

## 6. Provenance

```
source_candle_timestamps  -> (C1, C2, C3, C4) in order
c1/c2/c3/c4_timestamp     -> named individually
protected_wick            -> C2's low (bullish) or high (bearish)
validation_wick           -> C4's corresponding extreme
clearance_points          -> how far C4 stayed clear, in instrument points
```

No candle is copied into unrelated geometry.

## 7. Observability

Shared gate only. Nothing is observable before C4 closes.

## 8. Leakage risks

| Risk | Mitigation |
|---|---|
| **Publishing at C2 or C3** using C4's wick | Confirmation is C4's `close_time`; three-candle prefixes provably emit nothing |
| Using bodies or closes to judge violation | Wick-to-wick only, tested with fixtures where body and wick disagree |
| Treating equality as valid | Strict inequality by default |
| Spanning a market gap | Contiguity guard on by default |

## 9. Examples

Bullish, valid:

```
C1  o 1.0000 h 1.0020 l 0.9990 c 1.0015     up-close
C2  o 1.0015 h 1.0040 l 1.0005 c 1.0035     up-close, protected low 1.0005
C3  o 1.0035 h 1.0050 l 1.0030 c 1.0045     intervening
C4  o 1.0045 h 1.0070 l 1.0040 c 1.0065     low 1.0040 > 1.0005  -> VALID
```

Zone `[1.0005, 1.0040]`, clearance 350 points, confirmation at C4's close.

| C4 variant | Result |
|---|---|
| `low = 1.0040` | **valid** |
| `low = 1.0005` | **invalid** — equality is a violation |
| `low = 1.0000` | **invalid** |
| `low = 1.0000`, body entirely above 1.0005 | **invalid** — the wick decides, not the body |

## 10. Known ambiguity

**Explicitly marked, per the source-vs-interpretation rule.**

| Element | Status |
|---|---|
| Four-candle shape, C2 protected, C4 validation, wick-to-wick, C4-close confirmation | **Project-authoritative definition.** Not negotiable in code. |
| Directional prerequisites for C1/C2/C3/C4 | **Engineering assumption** — the definition describes roles, not close directions. Defaults: C1, C2, C4 must close in the delivery direction; **C3 is unconstrained**, being described only as "intervening". Each is a separate config flag so the imposed conditions are visible. |
| `wick_tolerance_points` | **Optional qualifier**, default 0 (strict). |
| Two-candle reading (delivery → wick → redelivery, zone from surrounding candles) | **Community interpretation. Not implemented.** |
| Three-candle containment reading | **Community interpretation. Not implemented.** |

## 11. Explicit non-goals

Not an FVG, not a BPR, not a generic two-candle pattern. No signals, no scoring, no
cross-timeframe projection, no ML.
