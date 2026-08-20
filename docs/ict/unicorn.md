# Unicorn Model

**Story:** R2-05.9 · **Module:** [`ict_kronos/ict/unicorn.py`](../../ict_kronos/ict/unicorn.py)

## 1. Definition

A **Unicorn** is the overlap between a **Breaker Block** and a **Fair Value Gap of the
same polarity**. It is a *relationship*, not a candle pattern — there is no sequence of
bars you can point at and call a Unicorn without first having both components.

```
bullish Breaker  ∩  bullish FVG   ->  bullish Unicorn
bearish Breaker  ∩  bearish FVG   ->  bearish Unicorn
```

The zone is the **intersection** of the two, per the source's *"the shaded overlap area
between the Breaker Block and the Fair Value Gap is the Unicorn entry zone"*.

## 2. Inputs

`BreakerDetector` and `FvgDetector`. Nothing else.

This module contains **no gap detection, no Order Block detection, no breaker
detection and no structure detection**. It computes one intersection and one
adjacency test. Everything else is consumed by id from detectors that are already
approved — which is also why a Unicorn's provenance reaches three levels deep
(`FVG`, `Breaker`, and transitively the `Order Block` the Breaker came from) without a
single line of duplicated geometry.

```
Order Block (R2-05.3) ──► Breaker (R2-05.4) ┐
                                            ├─ same polarity + overlap ─► Unicorn
FVG (R2-05) ────────────────────────────────┘
```

## 3. Exact deterministic rule

For every (Breaker, FVG) pair on the same symbol and timeframe:

1. **Polarity must match** — `fvg.direction is breaker.direction`. Opposite-polarity
   overlaps are not Unicorns. (That pairing is a BPR-shaped idea and is a different
   concept with a different module.)
2. **Overlap must be strictly positive** —

   ```
   zone_top    = min(breaker.zone_top,    fvg.top)
   zone_bottom = max(breaker.zone_bottom, fvg.bottom)
   overlap     = zone_top - zone_bottom            # must be > 0
   ```

   Touching at a single price is not an overlap. A zero-width Unicorn is refused at
   construction and never stored, the same rule R2-05 applies to gaps and R2-05.2
   applies to BPRs. `min_overlap_points` raises the bar further.
3. **Partial overlap is sufficient.** Full containment is *not* required — the source
   says to mark every Breaker and *"check whether any of those Breaker Blocks have a
   Fair Value Gap overlapping them"*. `require_full_containment` exists as an opt-in
   qualifier and is **off** by default.
4. **Adjacency** — the two components must confirm within `max_bars_from_breaker` bars
   of one another (default 50). Unbounded pairing would match a Breaker against an
   unrelated gap weeks later and call it a structure.

## 4. Cardinality — one Unicorn per qualifying pair

Three FVGs overlapping one Breaker produce **three** Unicorns with three ids. Two
Breakers overlapping one FVG produce **two**.

**No deduplication, no "best" selection, no merging of overlapping zones.** This is an
explicit requirement rather than an optimisation opportunity: collapsing identities is
the failure mode this concept is most likely to hit, and the R2-05.2 audit found two
real id collisions in exactly this shape. The pair is therefore part of the identity:

```
unicorn:<symbol>:<timeframe>:<confirmation ISO>:<breaker_id>|<fvg_id>
```

## 5. Event timestamp

**The later of the two components' event timestamps** — the instant at which the
completed relationship sits on the chart.

## 6. Confirmation timestamp

```
confirmation = max(breaker.confirmation_timestamp, fvg.confirmation_timestamp)
```

No additional trigger. Both components are already fully confirmed events; the overlap
is pure arithmetic over them, so the relationship is knowable the moment the second one
is.

> **The naive leak (L4).** Stamping the Unicorn at the **FVG's** confirmation when the
> Breaker confirms later publishes a Unicorn before its Breaker exists. That
> implementation is written out in the leakage suite and proven to disagree.

**Retest is not confirmation.** The source describes price returning to the overlap as
what confirms a Unicorn *trade*. The *event* exists as soon as both components do.
Conflating the two would defer the event to an arbitrary later instant — and to an
instant that may never arrive — making it unobservable in replay. The retest is
recorded in the update stream instead (§8).

## 7. Provenance

| Field | Resolves to |
|---|---|
| `source_breaker_id` | a `BreakerBlock` from the same analysis |
| `source_fvg_id` | an `FvgZone` from the same analysis |
| `source_order_block_id` | the `OrderBlock` beneath the Breaker — carried transitively, not recomputed |
| `source_breaker_confirmation` | copied so the composite invariant is checkable from the record alone |
| `source_fvg_confirmation` | ditto |

No source geometry is duplicated. The Unicorn's own `zone_top`/`zone_bottom` are the
**intersection**, which is genuinely new geometry; identity always points back.

**Field naming.** The R2-05.9 specification sketched `breaker_block_id` / `fvg_id`.
The implemented names carry the `source_` prefix every other composite in this engine
uses (`source_fvg_id`, `source_order_block_id`, `source_break_id`), because
`assert_provenance_resolves` and the audit tooling read those fields by convention.
Same information, one naming rule.

## 8. Lifecycle

`ACTIVE` → `PARTIALLY_FILLED` → `MITIGATED`, measured against the Unicorn's **own**
intersection zone via the shared `track_zone_fill`, starting no earlier than its own
confirmation.

**Retest** is the first fill update — the first bar whose extreme penetrates the zone.
It is a timestamped entry in the update stream, never a change to the immutable record.

**Inherited invalidation.** A Unicorn cannot outlive the Breaker that defines it. When
the source Breaker reaches `ZoneStatus.MITIGATED` in `BreakerAnalysis`, the Unicorn is
`INVALIDATED` from that instant.

This is **inherited by reference, never recomputed**: `unicorn.py` reads the timestamp
out of the Breaker's own fill stream and does not evaluate any price condition of its
own to decide it. Mitigation-is-invalidation is the convention `composites.py` already
states for every zone in this phase, so no new semantics are introduced here.

**Precedence.** When a Unicorn's own intersection is filled *and* its Breaker later
dies, the reported state moves `MITIGATED` → `INVALIDATED`. Both are terminal; the
second names the cause more precisely. This follows `OrderBlockAnalysis.status_at`,
where a structural failure likewise outranks a fill — one reporting rule, not two.

A Unicorn whose Breaker died *before* the Unicorn confirmed is still emitted, and is
born `INVALIDATED`. That is deliberate: the relationship demonstrably existed, and
suppressing it would hide a real event behind a lifecycle state. Consumers filter on
status; they are never handed a silently shortened list.

## 9. Observability

Shared gate only — `is_observable_at` / `filter_observable`. A Unicorn is observable
exactly when both of its components are, which is what the confirmation rule in §6
guarantees mechanically rather than by convention.

## 10. Leakage risks

| Risk | Mitigation |
|---|---|
| **Publishing at the FVG's confirmation** when the Breaker confirms later | `composite_confirmation` takes the max; the naive form is implemented in the suite and proven to disagree (L4) |
| Deferring the event to the retest | Retest is lifecycle, never confirmation (§6) |
| Recomputing the Breaker's death from prices | Inherited by reference from `BreakerAnalysis` (§8) |
| Pairing a Breaker with an unrelated later gap | `max_bars_from_breaker`, finite and documented |
| Collapsing several qualifying pairs into one | The pair is part of the id; asserted explicitly on real data |
| Treating a touch as an overlap | Strictly positive overlap, refused at construction |

## 11. Examples

```
bearish Breaker   zone [1.0100, 1.0140]
bearish FVG       zone [1.0120, 1.0160]

zone_top    = min(1.0140, 1.0160) = 1.0140
zone_bottom = max(1.0100, 1.0120) = 1.0120
overlap     = 0.0020   ->  VALID, 200 points (EURUSD)
```

| Variant | Result |
|---|---|
| FVG is **bullish**, Breaker bearish | **no Unicorn** — polarity must match |
| FVG `[1.0140, 1.0180]` (touches at 1.0140) | **no Unicorn** — a touch is not an overlap |
| FVG confirms 400 bars after the Breaker | **no Unicorn** at the default window |
| Three overlapping same-polarity FVGs | **three** Unicorns, three ids |

## 12. Known ambiguity

**Explicitly marked, per the source-vs-interpretation rule.**

| Element | Status |
|---|---|
| Breaker ∩ same-polarity FVG, zone = intersection, partial overlap sufficient | **Sourced.** The primary source states all three. |
| Confirmation = `max(component confirmations)` | **Derived from the engine's contract**, not from the source. The source discusses trade timing, not information availability. |
| `max_bars_from_breaker` = 50 | **Engineering assumption.** A configured proxy for the source's informal *"same structural leg"*. The source gives no number. |
| Ordering of the two components | **Undefined by the source.** The window is measured as an absolute distance, so an FVG that confirmed *before* its Breaker may pair with it. In practice the displacement that breaks an Order Block usually prints the gap, so both orders occur. Not constrained, and not silently constrained. |
| One Unicorn per (Breaker, FVG) pair | **Engineering decision**, recorded in [R2-05x-CONCEPT-MAP.md](R2-05x-CONCEPT-MAP.md) §5.9. The source is silent on cardinality. |
| Inherited invalidation on Breaker mitigation | **Engineering decision**, following this phase's mitigation-is-invalidation convention. The source does not define a Unicorn's death. |
| `require_full_containment` | **Optional qualifier**, off by default. The source explicitly does not require containment. |

## 12a. Observed behaviour on real bars

EURUSD + XAUUSD, 2024-03-08 → 2024-03-11, Breaker structure gate **off** so the
geometry is exercised on every timeframe:

| | 1m | 5m | 15m | 1H | 4H |
|---|---|---|---|---|---|
| EURUSD | 3857 | 721 | 139 | 9 | 2 |
| XAUUSD | 3804 | 696 | 123 | 0 | 0 |

With the gate **on** (the shipped default) the inputs are sparser: EURUSD 1H gives 3
and XAUUSD 1H gives 0. **Zero is a valid result and is reported as one** — the sparse
timeframes carry only 44 and 9 bars over this window.

Up to **37 Unicorns confirm on a single bar** without an id collision, which is the
cardinality rule working rather than a pathology.

## 12b. Known limitations

1. **Unicorns outnumber their own Breakers** — 3081 from 849 gated Breakers on EURUSD
   1m. Direct consequence of §4; `max_bars_from_breaker` is the governing knob. The
   source's characterisation of the overlap as "rare" does not survive contact with 1m
   bars, and that disagreement is recorded rather than tuned away.
2. **This is now the engine's slowest detector.** `analyse` takes ~25 s for 2933 1m
   bars (`detect` alone: ~2.9 s), because `track_zone_fill` runs a Python loop per
   Unicorn and there are thousands of them. No correctness impact. Deliberately not
   optimised — the fix is vectorising the fill scan, which belongs to a performance
   story with a benchmark, not to this one.
3. **`analyse` recomputes the Breaker layer** to read the inherited death, so the
   Breaker and Order Block detectors run more than once per call. Correct but wasteful;
   a shared analysis object would fix it and would change the detector's shape.
4. Every limitation of the Order Block qualifier and the Breaker reading beneath it is
   inherited unchanged.
5. The four-day fixture is short. Counts here are an engineering sanity check, **not**
   a statistical claim about how often the pattern occurs.

## 13. Explicit non-goals

Not an entry model. No retest-based signal, no scoring, no ranking, no "quality"
weighting, no cross-timeframe projection, no ML, no Kronos. A Unicorn here is an
event with a zone, two parents and two timestamps — nothing more.
