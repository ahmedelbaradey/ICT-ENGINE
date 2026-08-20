# Dealing Range — Premium / Discount / Equilibrium

**Story:** R2-06 · **Module:** [`ict_kronos/ict/dealing_range.py`](../../ict_kronos/ict/dealing_range.py)
· **Candidate evaluation:** [R2-06-CONCEPT-MAP.md](R2-06-CONCEPT-MAP.md)

## 1. Definition

A **dealing range** is the price band between the two structural anchors of the most
recent confirmed break of structure. Within it:

```
equilibrium = (high + low) / 2

price above equilibrium  ->  PREMIUM
price below equilibrium  ->  DISCOUNT
price at   equilibrium   ->  EQUILIBRIUM   (within a documented tolerance)
```

The range is a **fact about structure**, fixed when it is created. The zone is a
**fact about a price at an instant**, recomputed per bar. They are different records
with different lifetimes and are never merged.

## 2. Inputs

`SwingDetector` (R2-02) and `StructureDetector` (R2-03). Nothing else.

This module contains no pivot detection, no break detection, no session logic and no
timezone handling. It selects two already-confirmed anchors and does arithmetic.

## 3. Eligible anchors

A pivot may anchor a range only if it is a **confirmed `SwingPoint`** — that is, its
`confirmation_timestamp` has passed. A forming pivot is never an anchor; its price is
still changing.

The **high anchor** and **low anchor** are always of opposite kinds. One of the two is
always the swing the break broke.

## 4. Range selection — the exact deterministic rule

Breaks are walked in confirmation order. For each confirmed `StructureBreak`:

```
BULLISH break (price broke a swing HIGH upward)
    high anchor = the broken swing high, identified by break.reference_swing_timestamp
    low  anchor = the LAST confirmed swing LOW that is
                    - observable at break.confirmation_timestamp, and
                    - located before the break on the chart

BEARISH break (price broke a swing LOW downward)
    low  anchor = the broken swing low
    high anchor = the LAST confirmed swing HIGH under the same two conditions
```

Then:

1. **Both anchors must be observable** at the break's confirmation. Enforced through
   the shared gate, not by a local comparison.
2. **`high_price > low_price` strictly.** Interleaved pivots can produce an inverted
   pair; such a candidate is **rejected**, counted in `rejected_inverted`, and never
   stored. A zero-width candidate is rejected the same way.
3. **No-change suppression.** If the resulting anchor pair is identical to the
   currently active range's, no new range is emitted — structure moved, the range did
   not. This keeps the range stream a record of *changes*.

Ranges are ordered by `(confirmation_timestamp, range_id)`.

## 5. Range validity, supersession and no-range

A range becomes the **active** range at its `confirmation_timestamp` and stays active
until a later range confirms. Supersession is recorded in the analysis
(`superseded_at`), **never written back onto the frozen record** — the same
immutable-record / timestamped-update-stream separation R2-04 established.

**Before the first confirmed break there is no range at all.** `range_at()` returns
`None` and `classify()` returns `None`. That is a correct answer, not a gap to fill:
premium and discount are meaningless without a range, and inventing one (a lookback
window, the first two pivots) would be manufacturing structure.

**There is no price-based invalidation.** A range is not killed by price trading
beyond an anchor. See §12 — this is an explicit non-decision, not an oversight.

## 6. Direction

Inherited from the break that created the range and **fixed at creation**:

| Break | Range direction |
|---|---|
| bullish BOS/MSS | `BULLISH` |
| bearish BOS/MSS | `BEARISH` |
| direction not causally establishable | `NEUTRAL` |

**Direction is never inferred from where price currently sits.** A bullish range with
price in discount is an ordinary, meaningful state; recomputing direction from price
would destroy exactly that information.

## 7. Timestamps

| | |
|---|---|
| `created_timestamp` | the break's `event_timestamp` — where the range begins on the chart |
| `confirmation_timestamp` | `max(high anchor, low anchor, break)` confirmations |

Computed by `composites.composite_confirmation`, the same function every R2-05.x
composite uses. The invariant is therefore mechanical rather than conventional:

```
range.confirmation_timestamp >= high_anchor.confirmation_timestamp
range.confirmation_timestamp >= low_anchor.confirmation_timestamp
range.confirmation_timestamp >= break.confirmation_timestamp
```

A `RangeObservation` classifies one bar's **close**, so its `observation_timestamp` is
the bar's open time and its `confirmation_timestamp` is the bar's `close_time` — the
instant the close is knowable. Observations begin at the range's own confirmation; a
range never classifies the bars that created it.

## 8. Classification

```
width       = high - low
equilibrium = (high + low) / 2
distance    = price - equilibrium                 # signed, price units
position    = (price - low) / width               # NaN when width == 0
band        = equilibrium_tolerance_points * point_value

|distance| <= band   ->  EQUILIBRIUM
distance   >  band   ->  PREMIUM
distance   <  -band  ->  DISCOUNT
```

**`position` is not clamped.** Price beyond an anchor gives `position < 0` or `> 1`,
and those are legal, meaningful values — price is outside the range. Clamping would
erase the distinction between "at the low" and "far below the low".

**No raw floating-point equality anywhere.** EQUILIBRIUM is a band, never `==`.

`equilibrium_tolerance_points` defaults to **0.5 instrument points — half a tick**.
That is *below the instrument's own price resolution*, so by default EQUILIBRIUM means
"price is at equilibrium as precisely as this instrument can express", and the default
is a numerical-safety choice rather than an ICT claim. Setting it larger turns
equilibrium into a genuine zone; that is a research decision and is why it is a knob.

**Degenerate range (`high == low`).** Cannot arise from the detector (§4 rejects it),
but is representable so its behaviour is defined and tested:

- `width = 0`, `equilibrium = high = low`
- `position = NaN` — the documented sentinel. There is no meaningful normalised
  position inside a zero-width range, and returning `0.5` would be a lie.
- `distance` and therefore `zone` remain perfectly well defined, because they compare
  against equilibrium rather than dividing by width.
- **No division by zero occurs on any path.**

`high < low` is refused at construction — that is a contract violation, not a
degenerate case.

## 9. Provenance and identity

```
high_source_id   -> "swing:<symbol>:<timeframe>:<direction>:<pivot open time>"
low_source_id    -> same form
source_break_id  -> "break:<symbol>:<timeframe>:<type>:<break open time>"
```

Both anchor confirmations are carried on the record so the §7 invariant is checkable
from the record alone, without re-running R2-02.

The swing and break ids are **derived**, computed by `composites.swing_point_id` and
`composites.structure_break_id` from values those records already carry. R2-02 and
R2-03 are approved and are not modified to add an id field.

```
range_id = "range:<symbol>:<timeframe>:<direction>:<confirmation>:<high_id>|<low_id>"
```

Identity is anchored on **causal source identity**, not on price and not on the
current bar. Two ranges with identical prices confirming on the same bar are distinct
records if their anchors differ — which is the property that makes provenance
resolvable at all.

## 10. Observability

Shared gate only — `is_observable_at` / `filter_observable` from `contract.py`. This
module contains no `confirmation_timestamp <= as_of` comparison, and a source-level
regression guard enforces that.

## 11. Leakage risks

| Risk | Mitigation |
|---|---|
| **Dataset extrema** (`frame["high"].max()`) | never referenced; the naive form is implemented in the leakage suite and proven to disagree |
| Anchoring on the swing the break is *running into* | anchors are confirmed pivots only, filtered through the gate |
| Using the post-break swing high as the range high | the range ends at the **broken** level; the running extreme is deliberately not used |
| A later break rewriting an earlier range | records are frozen; supersession is a separate stream |
| Direction inferred from current price | direction is fixed at creation from the break |
| Observations classifying bars before the range existed | observations start at the range's own confirmation |

The cost of the third row is stated plainly: **the range high is the last confirmed
structural level, not the highest price traded.** Price will usually sit above it
right after a bullish break, giving `position > 1`. That is the honest, causal answer;
using the extreme would require a pivot that has not confirmed.

## 12. Known ambiguity

**Explicitly marked, per the source-vs-interpretation rule.**

| Element | Status |
|---|---|
| `equilibrium = (high + low) / 2`, premium above / discount below | **Sourced.** Uncontroversial across every reading. |
| The dealing range is the current structural leg | **Interpretation, selected and justified** in [R2-06-CONCEPT-MAP.md](R2-06-CONCEPT-MAP.md) §3B. Four alternatives are documented and not implemented. |
| Anchoring the high at the *broken* swing rather than the running extreme | **Engineering decision forced by causality** (§11). The alternative needs an unconfirmed pivot. |
| `equilibrium_tolerance_points = 0.5` | **Engineering assumption** — a sub-tick numerical band, not an ICT equilibrium zone. |
| Price-based range invalidation | **Undefined by the source and not implemented.** A range survives until superseded. Whether trading beyond an anchor should kill a range is a real question the material does not settle; imposing an answer would be manufacturing a rule. |
| `position` outside `[0, 1]` | **Deliberately unclamped**, per the story's acceptance criteria. |
| Equal highs/lows (R2-04) as anchors | **Not consumed.** A pool is not a single price; the swing at the pool anchors the range in the normal way. See concept map §3D. |
| Liquidity sweeps qualifying a range | **Not implemented** — see concept map §3D/§3E. |

## 13. Multi-timeframe

R2-06 is **timeframe-local**. A 5m range is built from 5m swings and 5m breaks.

Projecting a higher-timeframe range onto lower-timeframe bars is **not** done here.
It requires the higher-timeframe bar to be closed at the moment of use — the rule
CLAUDE.md states — and that belongs to the feature/state layer, not to a detector.

## 14. Sessions, weekends and DST

No new timezone, DST or session code exists in this module. Bar timestamps are already
UTC and timezone-aware, and the resampler owns higher-timeframe completeness. Across a
weekend the range simply persists: no bars print, so no observations are produced and
nothing supersedes it — the Friday range is still the active range on Sunday's reopen,
which is the correct answer.

## 14a. Observed behaviour on real bars

EURUSD + XAUUSD, 2024-03-08 → 2024-03-11:

| | bars | ranges | bull | bear | observations | premium | discount | equilibrium |
|---|---|---|---|---|---|---|---|---|
| EURUSD 1m | 2933 | 276 | 134 | 142 | 2922 | 1397 | 1489 | 36 |
| EURUSD 5m | 581 | 49 | 22 | 27 | 568 | 249 | 317 | 2 |
| EURUSD 15m | 190 | 13 | 6 | 7 | 179 | 75 | 104 | 0 |
| EURUSD 1H | 44 | 2 | 1 | 1 | 31 | 17 | 14 | 0 |
| EURUSD 4H | 9 | **0** | 0 | 0 | 0 | 0 | 0 | 0 |
| XAUUSD 1m | 2817 | 270 | 134 | 136 | 2797 | 1500 | 1295 | 2 |
| XAUUSD 5m | 561 | 45 | 26 | 19 | 548 | 313 | 235 | 0 |
| XAUUSD 15m | 185 | 15 | 10 | 5 | 174 | 108 | 66 | 0 |
| XAUUSD 1H | 44 | 2 | 2 | 0 | 32 | 32 | 0 | 0 |
| XAUUSD 4H | 9 | **0** | 0 | 0 | 0 | 0 | 0 | 0 |

Zero degenerate ranges, zero inverted candidates, 0–1 missing-anchor rejections and
0–5 no-change suppressions per combination. **4H is a genuine zero** — nine bars is not
enough for a pivot to confirm and a break to follow — and is reported as one.

**The headline characteristic — most observations sit OUTSIDE the range.**

| | EURUSD | XAUUSD |
|---|---|---|
| 1m | 67% | 75% |
| 5m | 58% | 74% |
| 15m | 62% | 54% |
| 1H | 42% | 81% |

This is the measured cost of §11's third row, and it is not a defect. The range ends
at the **broken** structural level, so immediately after a break price is by definition
beyond it, and it stays there until the next break redraws the range. Anyone consuming
`percentage_position` must expect values outside `[0, 1]` as the common case rather
than the exception. The alternative — anchoring on the running extreme — would require
a pivot that has not confirmed, which is the leak this whole module exists to avoid.

Premium and discount observations are roughly balanced (EURUSD 1m: 1397 / 1489), which
is what an unbiased structural range should look like and is a useful smell test: a
range definition that produced 90% premium would be measuring its own construction.

**Performance.** 190 ms to analyse 2933 1m bars end to end (`detect` alone: 106 ms);
under 40 ms on every other combination. Cheap relative to the R2-05.x composites, and
no hotspot was identified worth optimising.

## 15. Explicit non-goals

No OTE, no Fibonacci sub-levels, no standard-deviation projections, no cross-timeframe
projection, no range scoring, no signals, no ML, no Kronos.
