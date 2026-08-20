# R2-05.x — the composite ICT event graph

**Specification checkpoint document.** Written before any of R2-05.2 … R2-05.9 is
implemented. It fixes the dependency graph, the shared schema conventions, the leakage
criteria and — most importantly — the **ambiguity register**: every place where the ICT
material is loose or self-contradictory, what we propose to implement, and what we are
deliberately not implementing.

> **Status update.** The specification below was approved with two **authoritative
> overrides** from the project lead, and R2-05.2 has since implemented six detectors
> against the corrected definitions. Where this document's original proposal and the
> override disagree, **the override is the definition of record** and the per-concept
> doc reflects it:
>
> | | Original proposal (superseded) | **Authoritative definition** |
> |---|---|---|
> | **Order Block** | engulf + displacement + FVG (§5.3 "Q3") | the last opposing candle **or contiguous group** whose range is subsequently **closed through**; an FVG is **never** required unless explicitly configured |
> | **RDRB** | two-candle, zone from surrounding wicks (§5.6) | **four candles** C1→C2→C3→C4; C2 holds the protected wick; bullish `C4.low > C2.low`, bearish `C4.high < C2.high`; confirmation at **C4's close** |
>
> Implemented: IFVG, Order Block, Breaker, BPR, RDRB, CISD **and Unicorn**. CHoCH was
> reviewed and deliberately left unchanged (see `docs/ict/structure.md`). The R2-05.x
> composite layer is complete; see `docs/ict/unicorn.md` for the last of it.

---

## 1. Why these eight are one body of work

R2-01 … R2-05.1 produced six detectors that are each a pure function of bars. The
eight concepts in this phase are different in kind: **most of them are relationships
between events that already exist.** An IFVG is a state transition of an FVG. A
Breaker is a failed Order Block. A BPR is the intersection of two FVGs. A Unicorn is
the intersection of a Breaker and an FVG.

That changes the engineering problem. The risk is no longer "did we read the candles
correctly" but **"did the composite inherit its sources' observability correctly"** — a
composite that becomes observable before its own inputs is a leak, however carefully
each input was computed.

Hence one rule threaded through every story below:

> A composite event's `confirmation_timestamp` is **at least** the maximum of its
> sources' `confirmation_timestamp`s, plus whatever its own trigger requires.

This is checkable mechanically and will be enforced by a shared test helper rather than
re-argued per detector.

---

## 2. The dependency graph

```
                          Sessions (R2-01)
                                │
                          Swings (R2-02)
                                │
              ┌─────────────────┴─────────────────┐
              │                                   │
      Structure (R2-03)                   Liquidity (R2-04)
      BOS / MSS / CHoCH                   levels / sweeps
              │        │                          │
              │        └──────────┬───────────────┘
              │                   │
              │            Order Block (R2-05.3)
              │                   │
              │            Breaker Block (R2-05.4)
              │                   │
              │       ┌───────────┴───────────┐
              │       │                       │
        FVG (R2-05) ──┤                       │
              │       │                       │
        IFVG (R2-05.2)│                       │
              │       │                       │
        BPR (R2-05.5) │                       │
                      └──────► Unicorn (R2-05.9)

  independent of the above:

        bars ──► CISD (R2-05.7)          delivery-state transition, NOT structure
        bars ──► RDRB (R2-05.6)          two/three-candle delivery pattern
        bars ──► TRUE_DAILY_OPEN (R2-05.1, done)

  revision, not new detection:

        Structure (R2-03) ──► CHoCH semantics review (R2-05.8)
```

**Read the arrows as "consumes the confirmed output of", never as "re-derives".**
R2-05.3 does not detect FVGs; it asks `FvgDetector` for them. R2-05.4 does not detect
order blocks; it consumes them. A story that re-implements an upstream concept has
failed its acceptance criteria regardless of its test results.

### 2.1 Build order and why it is forced

| # | Story | Blocked by | Reason |
|---|---|---|---|
| 1 | R2-05.2 IFVG | R2-05 | Pure state transition of an existing FVG; nothing else needed. Smallest useful increment and it exercises the composite-observability rule first. |
| 2 | R2-05.3 Order Block | R2-03, R2-04, R2-05 | Needs displacement + a qualifying confirmation event. Everything downstream depends on it. |
| 3 | R2-05.4 Breaker | R2-05.3 | A Breaker is definitionally a failed OB. |
| 4 | R2-05.5 BPR | R2-05 | Independent of OB; scheduled here so Unicorn's inputs land together. |
| 5 | R2-05.6 RDRB | — | Independent. Scheduled after BPR only because its definition is contested (§5.6) and may need a decision round. |
| 6 | R2-05.7 CISD | — (optionally R2-04) | Independent of structure by explicit requirement. |
| 7 | R2-05.8 CHoCH | R2-03, R2-05.7 | Cannot be settled until CISD exists, because CISD occupies the role CHoCH is sometimes stretched to fill (§5.8). |
| 8 | R2-05.9 Unicorn | R2-05.4, R2-05 | Needs Breaker and FVG both confirmed. |

R2-05.8 is deliberately **after** R2-05.7. Deciding what CHoCH means while the only
available "early reversal signal" is CHoCH itself would bias the answer.

---

## 3. Shared schema conventions

Every new concept in this phase is a **price zone with a lifecycle**. Rather than
inventing eight shapes, all eight follow the record/update split R2-04 and R2-05
already use:

```
immutable record          the confirmed event; never mutated
    +
update stream             timestamped lifecycle transitions
    +
point-in-time query API   what a decision at time t may see
```

### 3.1 The common zone record

| Field | Type | Meaning |
|---|---|---|
| `<concept>_id` | `str` | `"<kind>:<symbol>:<timeframe>:<event_timestamp ISO>"` — stable, timestamp-derived |
| `symbol`, `timeframe` | `str` | as elsewhere |
| `direction` | `Direction` | `BULLISH` / `BEARISH`; `NEUTRAL` only where the concept genuinely has none |
| `zone_top`, `zone_bottom` | `float` | `zone_top > zone_bottom` always; a zero-width zone is refused, not stored |
| `event_timestamp` | `datetime` | where it sits on the chart |
| `confirmation_timestamp` | `datetime` | earliest instant it was knowable |
| `status` | lifecycle enum | `ACTIVE` / `PARTIALLY_FILLED` / `MITIGATED` / `INVALIDATED` as the concept requires |
| provenance fields | `str` / `tuple[str, ...]` | ids of the **source events**, never re-derived copies of them |

Plus, per concept, the fields its own definition requires. Positional dataframe
indexes are diagnostics only and are **never** identity or join keys — the rule
R2-05.1 adopted.

### 3.2 Provenance is an id, not a copy

A composite stores `source_fvg_id`, not a duplicated `top`/`bottom`. The zone
geometry it needs is copied where the definition requires it (a BPR's overlap is
genuinely a new zone), but the **identity** always points back. This makes two things
testable that are otherwise only assertable:

- every referenced id resolves to an event the same analysis produced;
- every referenced source is observable no later than the composite.

Both become shared test helpers (`assert_provenance_resolves`,
`assert_sources_observable_first`), written once in R2-05.2 and reused by the rest.

### 3.3 Contract extension — the smallest compatible change

New `EventType` members only. No new timestamp semantics, no second observability
gate, no change to `IctEvent`:

```python
IFVG_BULLISH = "ifvg_bullish"          IFVG_BEARISH = "ifvg_bearish"
ORDER_BLOCK_BULLISH = "order_block_bullish"
ORDER_BLOCK_BEARISH = "order_block_bearish"
BREAKER_BULLISH = "breaker_bullish"    BREAKER_BEARISH = "breaker_bearish"
BALANCED_PRICE_RANGE = "balanced_price_range"
RDRB_BULLISH = "rdrb_bullish"          RDRB_BEARISH = "rdrb_bearish"
CISD_BULLISH = "cisd_bullish"          CISD_BEARISH = "cisd_bearish"
UNICORN_BULLISH = "unicorn_bullish"    UNICORN_BEARISH = "unicorn_bearish"
```

`CHOCH` already exists and is unchanged by the addition. `tests/test_ict_contract.py`
keeps a deliberately exhaustive roster, so each story updates it as its registration
step — that file changing is expected and is the only edit to existing tests any of
these stories should need.

---

## 4. Leakage criteria — the shared pattern

Every story inherits these six proofs. They are listed once here and referenced, not
restated eight times.

| # | Proof | Form |
|---|---|---|
| L1 | **Future-bar mutation** | Wreck every bar after confirmation; the event set must be byte-identical. |
| L2 | **Boundary mutation + control** | Mutating the confirming bar's future-dependent fields must not make the event observable earlier — **and** a control proving that mutating the field the definition *does* read changes the result. Without the control, L1/L2 prove nothing. |
| L3 | **Prefix equivalence** | `detect(bars[:n]) == filter_observable(detect(all), t_n)` for every n. |
| L4 | **Naive divergence** | Construct the plausible leaky implementation and prove the causal one disagrees with it. |
| L5 | **Timestamp invariant** | `confirmation_timestamp >= event_timestamp`, enforced by the contract constructor. |
| L6 | **Provenance invariant** | Every source id resolves, and every source is observable no later than the composite (§3.2). |

Plus the two structural guards R2-04/R2-05/R2-05.1 already carry, extended to each new
module: no hand-rolled `confirmation_timestamp <=` comparison, and no re-import of a
detector the story is supposed to consume rather than reimplement.

**L4 is the one that actually catches things.** For each concept, the named naive
implementation is specified in its story so it cannot be quietly skipped:

| Story | The plausible leaky implementation |
|---|---|
| IFVG | Marking inversion at the moment price *touches* through the gap rather than *closes* through it |
| Order Block | Labelling the OB candle at its own close, before the displacement that qualifies it exists |
| Breaker | Flipping polarity at the wick that pierces the OB rather than at the close beyond it |
| BPR | Emitting the overlap at the *earlier* FVG's confirmation instead of the later one's |
| RDRB | Using the trailing surrounding candle's wick to draw a zone stamped at the pattern's start |
| CISD | Anchoring to the delivery run's extreme open discovered with hindsight |
| Unicorn | Emitting at the FVG's confirmation when the Breaker confirms later |

---

## 5. Ambiguity register

**This is the part that needs approval.** Each entry is a place where the ICT material
is loose, self-contradictory, or silent. For each: the readings found, what we propose,
and what that costs.

Sources consulted: `innercircletrader.net/tutorials` (treated as the primary source
because it is the one named in the brief), plus the wider community where the primary
source is silent. **Where they conflict, the conflict is recorded rather than
averaged.**

### 5.2 IFVG — what "broken" means

> *"An IFVG forms when price closes beyond a Fair Value Gap, breaking it in the
> direction opposite to its original delivery."* — primary source

- **Reading A — body close beyond the far edge** (full traversal, then close through).
- **Reading B — body close anywhere beyond the near edge** (inside the zone counts).
- **Reading C — wick through** is ruled out by the source's word *closes*.

**Proposed: Reading A, configurable.** It is the strictest, and it makes IFVG strictly
rarer than FVG mitigation — which matters because R2-05 already models mitigation and
the two must not silently become the same event.

**Consequence worth stating plainly:** an FVG can be **mitigated without inverting**.
R2-05 computes fill from bar *extremes*; inversion needs a *close*. A wick that fills
the gap 100% mitigates it and does **not** create an IFVG. These are different
questions about the same zone and the docs must say so.

### 5.3 Order Block — the qualifying event (**biggest open question**)

The primary source gives the candle pattern clearly — *"the last bearish candle before
a bullish impulse move"*, engulfed by the impulse — but is vague on what *qualifies*
it, mentioning displacement, an engulf, a lower-timeframe MSS and an FVG without
ranking them.

Candidate qualifying conditions, all deterministic:

| | Qualifier | Confirms at | Cost |
|---|---|---|---|
| Q1 | Engulf only | the engulfing bar's close | Very permissive; an OB on nearly every reversal candle |
| Q2 | Engulf **+ displacement** (R2-03's ratio) | the displacing bar's close | Deterministic, no new dependency |
| Q3 | Engulf + displacement **+ an FVG in the impulse leg** | the FVG's confirmation | Reuses R2-05; matches "an imbalance prints inside or just above the OB zone" |
| Q4 | Q3 + a structure break (R2-03) | the break's confirmation | Strictest, latest, fewest events |

**Proposed default: Q3**, with `require_structure_break` promoting it to Q4 and
`require_fvg=False` demoting it to Q2. Rationale: Q3 is the weakest condition that is
both deterministic and *not* trivially satisfied, it reuses two existing detectors
rather than inventing a threshold, and displacement is already computed identically in
R2-03 and R2-05 so there is one definition of it, not three.

**Zone boundaries** are separately contested — *"the body of that candle becomes your
OB zone"* against the common full-range reading. **Proposed: `FULL_RANGE` default**
(high–low), with `BODY` configurable and the 50% level exposed as `mean_threshold`.
Rationale: the wider zone is touched sooner, so it mitigates sooner, so it errs toward
declaring an OB *spent* rather than still live — the safe direction for a research
claim.

### 5.4 Breaker — which reading

- **Reading A — failed OB** (primary source): *"price closed past the OB extreme — the
  OB failed... that is a Breaker Block, not a Mitigation."*
- **Reading B — classic swing construction**: the candle at a swept swing, confirmed
  when the opposing swing breaks.

**Proposed: Reading A**, because it composes directly with R2-05.3 and satisfies the
brief's requirement that Breaker not be an independent candle pattern. Reading B is
documented as the alternative not adopted.

**Mitigation Block is the complement** of a Breaker under Reading A — same level,
opposite outcome. It is **not in this phase's scope** and will not be implemented;
noted here so its absence is a decision rather than an oversight.

### 5.5 BPR — three silences

The source defines the zone unambiguously (*"the overlap of these two FVGs is the
BPR"* — intersection, not union) and the polarity requirement (opposite), but is silent
on:

1. **Adjacency** — must the two FVGs be near each other in time? Proposed:
   `max_bars_between` (documented default, finite). Unbounded would pair a gap from
   March with one from June and call it a structure.
2. **Touching vs overlapping** — proposed: **strictly positive overlap required**,
   matching R2-05's rule that exact equality is not a gap.
3. **Direction** — the source describes BPRs as directional by context, which is not
   a computable rule. Proposed: **direction = the polarity of the later FVG**
   (the most recent delivery), with `NEUTRAL` configurable. Flagged as the weakest
   default in this document.

### 5.6 RDRB — **the sources genuinely disagree** ⚠

This is the one place where the primary source and the wider community describe
**different patterns**, not different emphases.

| | Primary source (`innercircletrader.net`) | Wider community |
|---|---|---|
| Candles | **two** — deliver, wick back, redeliver | **three** — with containment rules |
| Zone | *"the area between these two wicks"* — the wicks of the candles **surrounding** the pair; *"Forget the RDRB pair itself"* | between the pattern candles themselves |
| Shape | a *hidden* range inside apparently efficient delivery | a visible imbalance, FVG-like |

These cannot both be implemented under the "one definition, no competing
implementations" rule.

**Proposed: the primary source's two-candle reading**, because the brief names that
site as the reference and because the wider-community reading is close enough to an FVG
that R2-05 would largely subsume it — implementing it would produce a detector whose
output correlates with an existing one, which is the outcome the brief explicitly warns
against.

**This is the entry most likely to need a decision from you**, and it carries a
leakage consequence: the primary source's zone is drawn using the candle **after** the
pair, so `confirmation_timestamp` must be that trailing candle's close — the pattern is
*not* knowable when the second candle closes. That is precisely the naive
implementation L4 will be written against.

### 5.7 CISD — the anchor

The rule is unusually crisp: *"a bullish CISD prints when price closes above the
opening price of a bearish delivery leg"*, and *"ignore the wicks. Only the opening and
closing prices matter."*

One ambiguity remains — **which open** of the delivery run:

- **Reading A — the run's first candle's open** (the series' opening price). Source
  wording: *"the opening price of the final consecutive series of those down-closing
  candles"* → the price at which the series opened.
- **Reading B — the highest open** within the run.

**Proposed: Reading A**, configurable as `cisd_anchor`. Reading B is only discoverable
by scanning the whole run, which invites exactly the hindsight framing L4 tests for.

**A liquidity sweep is not required** by the source (*"CISD alone is just a candle
close"*), so `require_prior_sweep` defaults **off**, reusing R2-04 when enabled.

### 5.8 CHoCH — the evidence, and a recommendation that may be "change nothing"

R2-03 currently sets `ChochPolicy.SYNONYM` by default: counter-trend breaks emit `MSS`
and `CHOCH` is never emitted. The brief asks that this be re-evaluated rather than
preserved by inertia. Having re-read the material:

**The primary ICT source does not define CHoCH as a distinct algorithm.** What it does
define — and this is the substantive finding — is that **CISD is the early,
close-based signal and MSS is the later, structure-based one**: *"CISD is a
candle-close signal that prints early; MSS is a structural break that confirms later"*,
and *"MSS is based on wicks while CISD on closing price"*.

That matters because the role most often invoked to justify separating CHoCH from MSS
— "the earlier, weaker reversal hint" — is, in the source material, **CISD's role, not
CHoCH's**. Once R2-05.7 lands, that role is occupied by a concept with a real
definition.

**Proposed outcome: `SYNONYM` remains the default, and the justification changes from
"we found no distinction" to "the distinction people reach for is CISD, which now
exists."** Concretely R2-05.8 would:

1. keep BOS/MSS behaviour byte-identical (regression-tested);
2. add a documented third policy, `DISTINCT_BY_STRUCTURE_SCALE`, for the one remaining
   defensible reading in circulation — CHoCH breaks *internal* structure, BOS/MSS break
   *swing* structure — which is deterministic given two swing scales;
3. document the CISD/CHoCH/MSS/BOS relationship in one table so the next person does
   not re-litigate it.

If (2) proves to need more machinery than it earns, the honest outcome is to document
the reading and not implement it. **R2-05.8 may legitimately end with no behaviour
change** — that is a successful outcome for a review story, and it is flagged now so it
does not look like scope was dropped later.

### 5.9 Unicorn — cardinality

The source is clear on geometry (*"the shaded overlap area between the Breaker Block
and the Fair Value Gap is the Unicorn entry zone"*), that partial overlap suffices, and
that polarity must match. It is silent on cardinality.

**Proposed: emit one Unicorn per (breaker, FVG) pair that qualifies.** Three FVGs
overlapping one Breaker produce three Unicorns with three ids. The brief's
"do not silently collapse multiple valid identities" is taken literally: no
deduplication, no "best" selection, no merging of overlapping zones.

> **Implemented as proposed** (R2-05.9). The consequence is visible on real bars and
> worth stating plainly: because several gaps routinely overlap one Breaker, Unicorns
> outnumber Breakers — 3081 Unicorns from 849 Breakers on EURUSD 1m over the four-day
> fixture. The pair is part of the id, so all of them stay independently addressable.
> The source calls the overlap "rare"; at `max_bars_from_breaker = 50` on 1m bars it is
> not, and the window is the knob that governs that. See
> [unicorn.md](unicorn.md) §12.

---

## 6. What this phase does not build

Stated so absence is a decision:

Mitigation Block · Rejection Block · Vacuum Block · Propulsion Block · Liquidity Void ·
SMT divergence · Optimal Trade Entry · Premium/Discount and dealing ranges (R2-06) ·
`ICTMarketState` / `ICTFeatureVector` (R2-07) · any ML, probability, score, label or
normalisation · any signal, entry, exit, stop, target, sizing or backtest rule.

The output of R2-05.x is deterministic event information and nothing else.

---

## 6a. What R2-05.2 actually built

| Concept | Module | Doc | Tests |
|---|---|---|---|
| IFVG | `ict/ifvg.py` | [ifvg.md](ifvg.md) | 27 |
| Order Block | `ict/order_blocks.py` | [order_block.md](order_block.md) | 39 |
| Breaker | `ict/breakers.py` | [breaker_block.md](breaker_block.md) | 21 |
| BPR | `ict/bpr.py` | [bpr.md](bpr.md) | 23 |
| RDRB | `ict/rdrb.py` | [rdrb.md](rdrb.md) | 38 |
| CISD | `ict/cisd.py` | [cisd.md](cisd.md) | 28 |
| Unicorn | `ict/unicorn.py` | [unicorn.md](unicorn.md) | 56 |
| *(shared machinery)* | `ict/composites.py` | this document §3 | — |
| *(cross-cutting)* | — | — | leakage + real-data suites, parametrised over all seven |

**CHoCH** was reviewed and left unchanged; the reasoning is in
[structure.md](structure.md) §5. **Unicorn** landed in R2-05.9 as a pure composite of
Breaker ∩ same-polarity FVG — no new candle logic, three levels of provenance.

**Two real defects were found by tests rather than by reasoning**, both composite
identity collisions on real data: several FVGs can invert on the same bar, and several
Order Blocks can fail on the same bar. Both ids now include their provenance.

## 7. Implementation plan

Eight stories, strictly sequential, each ending at a checkpoint that must be approved
before the next begins. Per story:

```
1  confirm/refine the spec        6  ruff + black
2  write docs/ict/<concept>.md    7  real-data validation (EURUSD, XAUUSD, 1m…4h)
   BEFORE implementation          8  leakage audit incl. L1–L6
3  implement                      9  local commit, no push
4  write tests                   10  checkpoint report, then STOP
5  run the full suite
```

Two pieces of shared machinery are built once, in R2-05.2, and reused:

- `assert_provenance_resolves` / `assert_sources_observable_first` (§3.2);
- a composite-confirmation helper asserting the §1 rule.

Estimated shape, for planning only — not a commitment:

| Story | New module | Rough test count | Risk |
|---|---|---|---|
| R2-05.2 IFVG | `ict/ifvg.py` | ~120 | Low — one transition, one source |
| R2-05.3 Order Block | `ict/order_blocks.py` | ~200 | **High — §5.3 qualifier choice** |
| R2-05.4 Breaker | `ict/breakers.py` | ~150 | Medium |
| R2-05.5 BPR | `ict/bpr.py` | ~130 | Medium — §5.5 direction default |
| R2-05.6 RDRB | `ict/rdrb.py` | ~150 | **High — §5.6 sources disagree** |
| R2-05.7 CISD | `ict/cisd.py` | ~160 | Medium — §5.7 anchor |
| R2-05.8 CHoCH | *(revision)* | ~60 | Medium — regression risk on approved behaviour |
| R2-05.9 Unicorn | `ict/unicorn.py` | ~130 | Low — geometry over two confirmed inputs |

---

## 8. Decisions requested before R2-05.2 begins

1. **§5.6 RDRB** — confirm the two-candle primary-source reading, or direct us to the
   three-candle community reading. *Highest-impact question in this document.*
2. **§5.3 Order Block** — confirm Q3 (engulf + displacement + FVG) as the default
   qualifier, and `FULL_RANGE` as the default zone.
3. **§5.5 BPR** — confirm "direction = later FVG's polarity" and a finite
   `max_bars_between`.
4. **§5.8 CHoCH** — confirm that "no behaviour change, better justification, one
   optional new policy" is an acceptable outcome for a review story.
5. **§5.2 IFVG** — confirm that mitigation and inversion are deliberately different
   events over the same zone.

Everything else in this document we consider settled enough to build against.
