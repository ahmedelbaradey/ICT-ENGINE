# R2-06 — Premium / Discount & the Dealing Range — concept map

**Specification document.** Written before `ict_kronos/ict/dealing_range.py` exists.
The implemented semantics live in [dealing_range.md](dealing_range.md); this document
records *why* one definition was chosen and the four that were not.

---

## 1. The shape of the problem

Premium/discount arithmetic is trivial:

```
equilibrium = (high + low) / 2
position    = (price - low) / (high - low)
```

**All of the risk is in choosing `high` and `low`.** Every leakage failure this story
can have is a failure to pick anchors causally:

| The tempting mistake | Why it leaks |
|---|---|
| `frame["high"].max()` / `frame["low"].min()` | dataset extrema — the single worst leak available, since the answer depends on bars that had not printed |
| the *forming* swing that the break is running into | not confirmed; its price is still changing |
| a rolling `max`/`min` over a lookback | causal, but not a dealing range — it has no structural meaning and drifts every bar |
| the swing high *after* the break | the range's own future |

So this story is a **selection** problem wearing an arithmetic problem's clothes.

## 2. Dependency graph

```
SwingDetector (R2-02) ──────► confirmed pivots ─┐
                                                ├─► DealingRange ──► RangeObservation
StructureDetector (R2-03) ──► confirmed BOS/MSS ┘
```

Nothing else is consumed. R2-04 liquidity and R2-05.x price arrays are **not**
inputs — see §4 candidates D and E.

---

## 3. The five candidates

Each is judged on: causal timestamp, stability (how often the range changes),
leakage exposure, and complexity.

### A — Most recent confirmed swing high / swing low

**Definition.** At any instant, take the most recently confirmed swing high and the
most recently confirmed swing low, whichever they are.

**Causal timestamp.** `max(high.confirmation, low.confirmation)` — clean.

| | |
|---|---|
| ✅ | Simplest possible rule. No dependency on R2-03 at all. |
| ✅ | Always defined once two swings of opposite kind exist. |
| ❌ | **Not a leg.** The two pivots need not belong to the same move — the "range" can be a high from one impulse and a low from an unrelated earlier one. |
| ❌ | **Unstable.** At `left=right=2` every minor pivot replaces an anchor, so the range and therefore the premium/discount label churn on noise. On real EURUSD 5m this produces a new range every few bars. |
| ❌ | The high can end up **below** the low when pivots interleave, producing inverted ranges that have to be discarded anyway. |

**Verdict: rejected.** Causally safe but structurally meaningless, and the instability
would make "price is in discount" a statement about the last two fractals rather than
about market structure.

### B — Last structural leg *(SELECTED)*

**Definition.** When a BOS/MSS confirms, the dealing range is the leg that produced
it:

```
bullish break   high = the swing high that was broken
                low  = the most recent confirmed swing LOW observable at that instant

bearish break   low  = the swing low that was broken
                high = the most recent confirmed swing HIGH observable at that instant
```

**Causal timestamp.** `max(high.confirmation, low.confirmation, break.confirmation)`.
In practice the break's confirmation dominates, because a break cannot confirm before
the swing it breaks.

| | |
|---|---|
| ✅ | **Both anchors are already confirmed at break time.** The broken swing is confirmed by definition — you cannot break a level that is not yet a level — and the opposite anchor is filtered through the observability gate. There is no forming pivot anywhere in the construction. |
| ✅ | **It is a real leg.** The anchors bracket the move that produced the break, which is what ICT actually draws. |
| ✅ | **Stable.** The range changes only when structure changes, not when a minor fractal prints. Real data: 5m EURUSD yields tens of ranges over four days rather than hundreds. |
| ✅ | Direction is inherited from the break, so it is causal and fixed — never inferred from where price happens to be. |
| ⚠️ | Requires R2-03. Accepted: R2-03 is approved and consumed by id. |
| ⚠️ | Undefined before the first confirmed break. Accepted and documented: **no range is a valid answer**, not a reason to invent one. |
| ⚠️ | The broken swing high is not the *highest* price of the leg — price ran past it to break it. The range therefore ends at the last **confirmed** structural level, not at the running extreme. This is a deliberate causality trade, documented in [dealing_range.md](dealing_range.md) §11. |

**Verdict: selected.** It is the only candidate that is simultaneously causal by
construction, structurally meaningful, and stable.

### C — BOS/MSS anchored range (broken swing + opposite structural anchor)

**Definition.** As B, but the opposite anchor must itself be a *structural* swing —
one that carries an HH/HL/LH/LL label from R2-03 — rather than any confirmed pivot.

**Causal timestamp.** Same as B.

| | |
|---|---|
| ✅ | Slightly stronger structural claim: both anchors are structure, not just fractals. |
| ❌ | R2-03 filters swings by `min_swing_strength_points` before labelling. Reaching into that filtered set from here would mean **re-implementing a structure-internal decision** in a second place — precisely the duplication the engine's guards exist to prevent. |
| ❌ | Labels exist only from the *second* swing of a kind onward, so early anchors are unavailable and the first ranges are silently delayed. |
| ❌ | Buys a marginal semantic improvement for a real coupling cost. |

**Verdict: rejected — but it is B's nearest neighbour.** If a future story wants
structure-labelled anchors, it is a config flag on B, not a different algorithm.

### D — Liquidity anchored range

**Definition.** Anchor on confirmed R2-04 liquidity: equal highs/lows, PDH/PDL, or the
level a sweep took.

**Causal timestamp.** `max(level.confirmation, sweep.confirmation)`.

| | |
|---|---|
| ✅ | Causally clean — R2-04 levels and sweeps are confirmed events with correct timestamps. |
| ❌ | **A sweep is not a range.** Nothing in the source material says a liquidity grab defines the boundaries of premium and discount; it says price *seeks* liquidity. Promoting a sweep to a range boundary would be inventing doctrine. |
| ❌ | PDH/PDL are a *daily* construction on a 17:00 NY boundary. Using them as the range for a 5m premium/discount silently mixes two timeframes, which §"multi-timeframe" of this story explicitly defers. |
| ❌ | Equal highs are a *pool*, not a single price; picking one member is arbitrary. |

**Verdict: rejected.** The brief asked whether confirmed liquidity should *qualify* a
range. The honest answer from the source is: it is not established that it does, so it
is not implemented. Recorded rather than assumed.

### E — Hybrid structural + liquidity range

**Definition.** Combine B with a liquidity qualifier — e.g. only accept a range whose
low anchor coincides with a swept low.

**Causal timestamp.** `max` over every input; safe if and only if every input is
observable, which is enforceable.

| | |
|---|---|
| ✅ | Would produce fewer, arguably higher-quality ranges. |
| ❌ | **"Higher quality" is exactly the claim this project refuses to make without evidence.** A qualifier that discards ranges is a filter with an unvalidated hypothesis inside it. |
| ❌ | Real-data precedent: the R2-05.3 audit showed a plausible-sounding qualifier (requiring an FVG on an Order Block) would have discarded 56% of valid events. A liquidity qualifier here is the same shape of mistake. |
| ❌ | Highest complexity of the five, for the least defensible gain. |

**Verdict: rejected.** It is a research question for a later ablation — "does a
liquidity-qualified range predict better than an unqualified one?" — and that question
needs the unqualified baseline to exist first. R2-06 builds the baseline.

## 4. Ranking

| Rank | Candidate | Causal | Structural | Stable | Complexity | Outcome |
|---|---|---|---|---|---|---|
| **1** | **B — last structural leg** | ✅ | ✅ | ✅ | medium | **implemented** |
| 2 | C — structural anchors only | ✅ | ✅✅ | ✅ | medium-high | documented |
| 3 | A — most recent two swings | ✅ | ❌ | ❌ | low | documented |
| 4 | E — hybrid | ✅ | ✅ | ✅ | high | documented |
| 5 | D — liquidity anchored | ✅ | ❌ | ? | medium | documented |

**Exactly one is implemented.** A, C, D and E exist in this document and nowhere else —
no competing active algorithm, no policy enum switching between range definitions.
That is deliberate: two live range definitions would mean every downstream result has
to name which one it used, and the first person to forget makes the results
unreproducible.

## 5. Leakage criteria inherited

The six proofs from [R2-05x-CONCEPT-MAP.md](R2-05x-CONCEPT-MAP.md) §4 apply unchanged
(L1 future mutation, L2 boundary + control, L3 prefix equivalence, L4 naive divergence,
L5 timestamp invariant, L6 provenance invariant).

**L4 for this story — the named naive implementation:**

> Build the range from `frame["high"].max()` and `frame["low"].min()`, or from the
> highest high and lowest low of a trailing window that includes bars after the range's
> confirmation.

This is written out in the leakage suite and proven to disagree with the causal
implementation. It is the single most likely way a premium/discount layer leaks, and
it is invisible in the output — a leaked range still looks like a perfectly ordinary
range.

## 6. What R2-06 does not build

Stated so absence is a decision:

Optimal Trade Entry · Fibonacci sub-levels (0.62/0.705/0.79) · standard-deviation
projections · cross-timeframe range projection (a 4H range read on 5m bars) ·
price-based range invalidation · range "quality" scores · `ICTMarketState` ·
`ICTFeatureVector` · any ML, probability, label or normalisation beyond the documented
`percentage_position` · any signal, entry, stop, target or backtest rule.
