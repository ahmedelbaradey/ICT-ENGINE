# FVGDetector (R2-05)

Story: [R2-05](../../user-stories/Phase-2-ICT-Engine/R2-05-fair-value-gap.md) · Code: [`ict_kronos/ict/fvg.py`](../../ict_kronos/ict/fvg.py)

**Written before the implementation.** The risk in this story is timestamp semantics, not arithmetic.

---

## 0. The bug this story exists to not repeat

`ForexQuant/src/Infrastructure/.../FvgDetectionService.cs` detects FVGs correctly and timestamps them wrongly. Verbatim, lines 64–67:

```csharp
if (candle3.Low > candle1.High)          // condition depends on candle3.Low
{
    var bullishFvg = new FairValueGap {
        TopPrice   = candle3.Low,
        BottomPrice = candle1.High,
        StartTime  = candle3.Timestamp,  // 🔧 FIXED: Changed from candle2 to candle3 ...
```

Three defects, in increasing order of importance:

1. **`StartTime = candle3.Timestamp` is candle 3's _open_.** The detection condition reads `candle3.Low`, which is not final until candle 3 **closes**. The FVG is therefore stamped as existing one full bar before it could be known.
2. **The "fix" moved the bug, it did not remove it.** The comment records a real production correction (candle 2 → candle 3), which shortened the error from two bars to one. It is still one bar early.
3. **There is only one timestamp field.** With a single `StartTime`, the distinction between *where the pattern sits* and *when it became knowable* cannot even be expressed. That is the root cause; (1) and (2) are symptoms.

Downstream, mitigation is checked with `c.Timestamp > fvg.StartTime` — so any consumer asking "which FVGs existed at time *t*?" gets each one a bar too early, and every feature built on it is contaminated.

### Why our implementation cannot reproduce it

| Legacy | Ours |
|---|---|
| one field, `StartTime` | **two required fields**, `formation_timestamp` and `confirmation_timestamp` |
| set to candle 3's **open** | confirmation is candle 3's **`close_time`** |
| no invariant | `IctEvent.__post_init__` **refuses** `confirmation < event` |
| observability re-derived per call site | the single shared gate (`is_observable_at` / `filter_observable`) |

`close_time` is `timestamp + timeframe.duration` (R2-01), so `confirmation_timestamp > formation_timestamp` **by construction** — it is not possible to write the legacy value into our confirmation field and still have it type-check as a confirmation. Pinned by `test_confirmation_is_exactly_one_bar_after_formation` and by `test_the_legacy_single_timestamp_filter_would_leak`, which replays the legacy filter beside ours and shows it admits FVGs one bar early.

**No code was copied from the legacy implementation.**

---

## 1. Exact three-candle definition

Over three bars `C1`, `C2`, `C3` that are **consecutive in the observed series**:

**Bullish FVG** — an unfilled gap left by an up-move:

```
low(C3) > high(C1)

    bottom = high(C1)
    top    = low(C3)
```

**Bearish FVG** — the mirror:

```
high(C3) < low(C1)

    bottom = high(C3)
    top    = low(C1)
```

`C2` is the displacement candle. Its own high/low are **not** part of the boundary arithmetic — the gap is defined purely by `C1` and `C3`. `C2` matters only if the optional displacement filter is enabled (§9).

**Boundaries are wick-to-wick by default** (`high`/`low`). `GapMeasure.BODY` switches to `max(open,close)` / `min(open,close)`. Wick-to-wick is the default because it is the more common ICT reading and the more conservative: a body-measured gap is always at least as wide, so it would report gaps the wick measure denies.

**Exact equality is not a gap.** `low(C3) == high(C1)` yields nothing — the comparison is strict, then `min_gap_points` is applied on top.

---

## 2. Timestamps

| Field | Value | Meaning |
|---|---|---|
| `formation_timestamp` (= `event_timestamp`) | **C3's open time** | where the completed pattern sits on the chart |
| `confirmation_timestamp` | **C3's `close_time`** | the earliest instant the FVG could be known |

They differ by **exactly one bar duration**, always.

**Why formation is C3's open, not C2's.** The pattern does not exist until C3 has printed; C3 is the bar whose action completes it. Some practitioners draw the zone at C2 (it sits between C1 and C3, visually centred on C2). Both readings are defensible, so the zone carries `candle1_timestamp`, `candle2_timestamp` and `candle3_timestamp` explicitly — a consumer preferring the C2 convention can read it off without re-deriving anything, and nothing is silently assumed.

**Why confirmation is C3's close.** The condition reads `low(C3)` / `high(C1)`. A bar's low is not final until it closes. Anything earlier is the legacy bug.

---

## 3. Minimum gap size and tolerance

```
gap_size = top − bottom
required: gap_size > min_gap_points × point_value
```

`min_gap_points` defaults to **0.0** — any strictly positive gap qualifies. It exists because on quantised instruments (XAUUSD especially) one-tick gaps are noise rather than imbalance, and a researcher will want to sweep the threshold. It is configuration, never a literal.

`size` is reported in **price units** (`size`) and in **instrument points** (`size_points`). Normalisation to ATR is deliberately *not* implemented here — it needs an ATR lookback, which is a feature-engineering concern owned by R2-07.

---

## 4. Fill: does touching the boundary count?

**No.** Touching the near edge is 0% fill.

Fill is measured by **penetration into the zone**, from the edge price must cross to enter it:

| Direction | Price enters from | `fill_percentage` |
|---|---|---|
| bullish | above, coming **down** | `(top − lowest_low_since) / (top − bottom)` |
| bearish | below, going **up** | `(highest_high_since − bottom) / (top − bottom)` |

Clamped to `[0, 1]`. Only bars **strictly after C3** are considered — C3 itself defines the gap and cannot fill it.

| State | Rule |
|---|---|
| untouched | `fill_percentage == 0` |
| **partial fill** | `fill_percentage > partial_fill_threshold` (default `0.0`, i.e. any real penetration) |
| **full fill** | `fill_percentage >= full_fill_threshold` (default `1.0`, i.e. price traded through the far edge) |

Both thresholds are configuration.

`midpoint` (the 50% level, ICT's *consequent encroachment*) is on every zone, since it is the level most commonly used and re-deriving it downstream invites off-by-one errors.

---

## 5. Invalidation, and an explicit statement about its equivalence

**In this implementation, invalidation IS full mitigation.** Said plainly rather than modelled as two states that do the same thing.

An FVG is a price *imbalance*. Once price has traded through the entire zone, the imbalance is gone — there is no separate way for the gap to become invalid while still existing. `MITIGATED` is therefore terminal, and `invalidation_timestamp` is exposed as the same instant as `mitigation_timestamp` so a consumer expecting either name finds it.

This mirrors R2-04's decision that a swept level *is* a consumed level. Inventing a distinct `INVALIDATED` state would give two labels for one event.

---

## 6. Does an FVG remain usable after partial mitigation?

**Yes.** A partially filled FVG stays `PARTIALLY_FILLED`, remains in the active set, and is still returned by `active_at()`.

This is deliberate and matters: a great deal of ICT practice keys on price returning *into* a gap — the 50% level especially — so treating first touch as death would discard the concept's main use. `fill_percentage` is exposed point-in-time so downstream can filter on depth. Only **full** fill removes a zone.

---

## 7. Lifecycle

```
             (C3 closes)        (price enters)        (price crosses far edge)
   [pending] ──────────► ACTIVE ──────────► PARTIALLY_FILLED ──────────► MITIGATED
                                                                        (terminal)
```

| Status | Meaning |
|---|---|
| `ACTIVE` | Observable, untouched |
| `PARTIALLY_FILLED` | Price entered the zone; **still usable** |
| `MITIGATED` | Price traded through the whole zone. Terminal (= invalidated) |

As in R2-04, there is no observable `pending` state: a zone is only ever constructed once C3 has closed, so every returned zone has already confirmed. That absence is the guarantee, not an oversight.

**Zones are immutable.** Fill progression lives on the analysis as timestamped `FvgFillUpdate` records, never by mutating a confirmed zone — the same level/sweep separation R2-04 uses.

---

## 8. Weekend, session gaps, and missing candles

**The three candles must be contiguous in time.** `require_contiguous_bars` defaults to **True**, meaning `close_time(Cn) == timestamp(Cn+1)` for both steps.

This is not decoration. Across the Friday-to-Sunday closure, `low(C3)` will routinely sit far above `high(C1)` simply because the market re-opened at a different price. Positionally the three bars are adjacent; in reality nothing traded between them. Admitting that would manufacture a **phantom FVG** at every weekend and every data gap — a large, confident, entirely fictitious imbalance.

The same rule covers missing bars from any cause (thin liquidity, vendor gaps, holidays).

Setting `require_contiguous_bars=False` restores the naive positional behaviour. It is available for comparison, documented as leaky, and off by default.

---

## 9. Relationship to other detectors

**To liquidity (R2-04):** independent, deliberately. An FVG is an *imbalance zone*; a liquidity level is a *price where orders rest*. They frequently coincide — a sweep often prints the displacement candle that leaves the gap — but coupling them would make each unusable alone and would hide which signal a model actually keyed on. R2-07 composes them.

**To structure (R2-03):** independent, same reasoning. C2 is often the same displacement bar that produces a BOS, but the FVG detector never consults structure state. The optional `require_displacement` filter (off by default, `displacement_factor` × mean range of the previous N bars) provides the commonly-requested "expansion candle" variant without importing R2-03.

Neither relationship introduces a dependency. R2-05 reads bars and nothing else.

---

## 10. Batch vs streaming

A zone is emitted only once **C3 has closed within the observed data**. Fill updates are emitted only for bars that have closed. Consequently:

```
batch(history[:k]) == prefix of batch(history)   for every k
```

and true bar-by-bar replay reproduces the batch result exactly.

Extending history can **add** zones and **add** fill updates. It can never move a `confirmation_timestamp`, revise a zone, or retract a fill update.

---

## 11. Configuration

```bash
export ICT_FVG_MIN_GAP_POINTS=0.0
export ICT_FVG_MEASURE=wick              # wick | body
export ICT_FVG_REQUIRE_CONTIGUOUS_BARS=1
export ICT_FVG_PARTIAL_FILL_THRESHOLD=0.0
export ICT_FVG_FULL_FILL_THRESHOLD=1.0
export ICT_FVG_REQUIRE_DISPLACEMENT=0
export ICT_FVG_DISPLACEMENT_LOOKBACK=20
export ICT_FVG_DISPLACEMENT_FACTOR=1.5
```

---

## 12. Edge cases

| Case | Behaviour |
|---|---|
| Fewer than 3 bars | No FVG. Too early, not an error |
| `low(C3) == high(C1)` | **Not** a gap (strict comparison) |
| Gap below `min_gap_points` | Rejected |
| Non-contiguous bars (weekend/missing) | **No FVG** by default (§8) |
| C3 not yet closed | Not emitted |
| Price exactly touching an edge | 0% fill, not a partial fill |
| Overlapping FVGs | Independent zones, each with its own identity and fill state |
| Consecutive FVGs | Each emitted separately; one bar can be C3 of one and C1 of another |
| Fully filled zone | Terminal; never re-activates |
| Empty frame | Empty analysis |

---

## 13. Test coverage

**225 tests** across three files (80 + 30 + 115).

| File | Tests | Covers |
|---|---|---|
| `tests/test_fvg.py` | 80 | Config, bullish/bearish, timestamps, exact boundary equality, minimum gap, wick vs body, contiguity, displacement filter, consecutive & overlapping zones, fill (touch/partial/full/thresholds), point-in-time fill, `state_of`, boundaries, vectorised-vs-reference, events |
| `tests/test_fvg_leakage.py` | 30 | **The legacy off-by-one** (5 tests), the adversarial set (removal, unconfirmed, backward confirmation, rewrite, market gap, weekend phantom, fill timing), contract-level gating incl. a source-level single-gate guard, batch == prefix == bar-by-bar, HTF non-leakage, detector independence |
| `tests/test_fvg_real_data.py` | 115 | Real EURUSD + XAUUSD on 1M/5M/15M/1H/4H: detection, boundaries matching real bars, contiguity, vectorised-vs-reference, timestamps, fills, leakage, prefix replay, weekend, DST, composition with R2-03/R2-04, and the recorded 1D/1W dataset limit |

## 14. Known limitations

1. **No ATR normalisation** of gap size — needs a lookback; R2-07 owns feature scaling.
2. **No IFVG / BPR / volume imbalance / liquidity voids** — Master Plan §8, deferred.
3. **No cross-timeframe projection.** An FVG is found by running the detector on that timeframe's bars. R2-07 owns multi-timeframe assembly via `align_htf_context()`.
4. **Fill scanning is O(bars × active zones).** Fine at Phase-1.5 scale; a multi-year run would want an interval structure or an age cap.
5. **`require_contiguous_bars=False` is leaky by construction** and exists only for comparison.
6. **Validation timeframes are bounded by the four-day window.** 1D resamples to 0 complete bars and 1W is not representable in the `Timeframe` enum, so neither can be validated here. Not an implementation limit — a dataset limit, pinned by `TestUnvalidatedTimeframes` so it surfaces the moment more data makes 1D possible.
7. **A genuine absence of FVGs is a valid result on sparse timeframes.** XAUUSD's nine 4H bars overlap throughout and yield zero zones, while EURUSD yields two. The tests assert the invariants over whatever is found rather than demanding a non-empty result the data need not contain.
