# ICTMarketState — the point-in-time aggregation layer

**Story:** R2-07 · **Module:** [`ict_kronos/ict/market_state.py`](../../ict_kronos/ict/market_state.py)
· **Feature catalogue:** [features.md](features.md)

## 1. What this layer is, and what it is not

`ICTMarketState` answers exactly one question:

> **What could a decision made at instant `t` have known about ICT structure?**

It is an **aggregation**, not a detector. It contains no pattern logic, no thresholds,
no geometry and no lifecycle rules of its own. Every value in it is either read from an
approved detector's point-in-time API or derived arithmetically from values that were.

```
R2-01 sessions ─┐
R2-02 swings   ─┤
R2-03 structure┤
R2-04 liquidity┤
R2-05 FVG      ├──► ICTMarketState ──► ICTFeatureVector
R2-05.1 TDO    ┤        (rich)              (flat)
R2-05.2..9     ┤
R2-06 range    ─┘
```

The state is **the truth**; the vector is a **projection** of it. That direction matters:
a model-shaped compromise in the vector can never degrade the underlying representation,
because the vector is derived and the state is not.

## 2. The one rule

Every component of the state at `as_of = t` is produced by passing `t` to a detector's
own observability-aware API — `active_at`, `status_at`, `state_at`, `range_at`,
`latest_at`, `session_state_at` — or by `filter_observable` from the shared contract.

**This module contains no `confirmation_timestamp <= as_of` comparison**, and a
source-level guard enforces that. There is one observability gate in this engine and
R2-07 does not become the second.

## 3. Structure of the state

| Component | Source | Carries |
|---|---|---|
| `ObservationBar` | the frame | symbol, timeframe, timestamp, `close_time`, OHLCV |
| `StructureContext` | R2-03 | state, direction, latest BOS/MSS/CHoCH ids and levels, bars since |
| `LiquidityContext` | R2-04 | active level ids by side, nearest level each side, latest sweep, bars since |
| `ImbalanceContext` | R2-05, R2-05.2, R2-05.5 | active FVG ids by polarity, nearest each side, IFVG, BPR |
| `InstitutionalContext` | R2-05.3, R2-05.4 | active Order Block and Breaker ids by direction, latest of each |
| `CompositeContext` | R2-05.6/7/9 | RDRB, CISD delivery state, Unicorn with inherited parents |
| `DailyOpenContext` | R2-05.1 | the latest observable 00:00 New York open, its trading date, staleness |
| `PremiumDiscountContext` | R2-06 | range id, both anchor ids, equilibrium, position, zone, direction |
| `SessionContext` | R2-01 | active sessions, elapsed time, day/hour, minute of session |
| `BiasContext` | derived | independent bullish / bearish evidence, then an explicit bias |

Every component is a frozen dataclass. `ICTMarketState` is frozen. Nothing in it is a
mutable detector internal — collections are tuples, not lists.

## 4. Provenance is an id, never geometry

Every value that originates in an ICT event carries that event's **id**:

```
structure          latest_break_id, latest_bos_id, latest_mss_id, latest_choch_id
liquidity          active_buy_side_ids, active_sell_side_ids, nearest_*_id, latest_sweep_*_id
imbalance          active_bullish_fvg_ids, ..., latest_ifvg_id, latest_bpr_id
institutional      active_*_order_block_ids, latest_*_order_block_id, *_breaker_id
composites         latest_rdrb_id, latest_cisd_id, latest_unicorn_id
                   + latest_unicorn_fvg_id, latest_unicorn_breaker_id, latest_unicorn_order_block_id
daily open         level_id
premium/discount   range_id, high_anchor_id, low_anchor_id, source_break_id
```

`assert_provenance_resolves` from `composites.py` resolves every one of them back to a
real event in the same analysis. **No id is ever reconstructed from a price**: two
events can share a price, and the R2-05.2 audit found two real id collisions caused by
exactly that kind of shortcut.

Unicorn provenance is inherited whole — the state carries the Unicorn's id *and* its
source FVG, source Breaker and (transitively) source Order Block ids, without copying
any of their geometry.

## 5. Event selection — "latest" is a timestamp question

When several events are observable, "the latest" is decided by

```
sorted(events, key=lambda e: (e.confirmation_timestamp, e.event_timestamp, <id>))[-1]
```

Never by array position, never by dictionary or set iteration order. The id is the
final tiebreaker so that two events sharing both timestamps still order deterministically
— which is what makes the state reproducible across runs and across machines.

Ties are **preserved**, not collapsed: counts count every event, and only the
single "latest" accessor picks one.

## 6. Lifecycle is the detector's, not ours

Each underlying lifecycle is read through that detector's own API and reported in its
own vocabulary:

| Concept | Lifecycle asked for | Meaning of "active" |
|---|---|---|
| liquidity level | `LiquidityAnalysis.active_at` | observable and not swept **as of t** |
| FVG | `FvgAnalysis.active_at` | observable and not fully mitigated |
| IFVG | `IfvgAnalysis.active_at` | observable and not mitigated |
| Order Block | `OrderBlockAnalysis.active_at` | ACTIVE or PARTIALLY_FILLED — not mitigated, not invalidated |
| Breaker | `BreakerAnalysis.status_at` | not MITIGATED |
| BPR / RDRB | `status_at` | not MITIGATED |
| Unicorn | `UnicornAnalysis.active_at` | not mitigated and not inheriting a dead Breaker |
| dealing range | `DealingRangeAnalysis.range_at` | the active, non-superseded range |

**No universal lifecycle is invented.** A "mitigated" FVG and an "invalidated" Order
Block are different facts and stay different; flattening them into one enum would
change what the detectors mean.

**A sweep never makes an unobservable level observable.** `active_at` filters levels
*and* sweeps through the gate independently, so a level whose confirmation has not
passed cannot appear merely because something swept it later.

## 7. Missing values — `0` and `UNKNOWN` are different

This is the single most consequential encoding decision in the story, because zero is
a **real** price distance.

| Meaning | Representation |
|---|---|
| the detector ran and found nothing active | `0` for a count, `()` for an id tuple |
| there is no reference event, so the value cannot exist | `None` |
| the value is not yet observable at `t` | `None` |
| the concept does not apply to this timeframe or symbol | `None` |

So `bullish_fvg_count == 0` means "no live bullish gaps"; `nearest_bullish_fvg_points
is None` means "there is no bullish gap to measure to". Those are different statements
and a consumer that conflates them is measuring something else.

In the flat vector the same distinction survives: `as_dict()` emits `None`, and
`as_row()` emits `math.nan`. **Never zero.**

**NaN belongs to `as_row()` and nowhere else.** R2-06 returns `math.nan` from
`position_of` for a degenerate (zero-width) range — *its* sentinel for "undefined".
This layer translates that to `None` at the boundary, so there is one missing-value
convention rather than two. It is not a lost fact: `range_id` is still present and
`width_points` is `0`, which is exactly what distinguishes a degenerate range from no
range at all. The translation also keeps record equality meaningful — NaN is not equal
to itself, so a state carrying one would fail both the serialisation round-trip and the
batch-versus-prefix comparison for a reason that has nothing to do with the market.

## 8. Distances declare their unit

Every distance in the state and the vector is expressed in **instrument points** and
named `*_points`. Prices are named `*_price`. There is no field where the two could be
confused, and no implicit conversion anywhere.

Points come from `symbol.spec.point_value` — 1e-5 for EURUSD, 1e-3 for XAUUSD — so the
same numeric feature is comparable in magnitude across instruments without introducing
a normalisation scheme. **No ATR normalisation is introduced**: no approved contract
defines one, and inventing one here would be an unvalidated modelling choice inside a
representation layer.

## 9. Market bias — evidence first, verdict second

Bias is the one derived value in the state, and it is built to be **auditable**:

```
bullish_evidence : tuple[str, ...]      each entry names the fact that produced it
bearish_evidence : tuple[str, ...]
bias             : BULLISH | BEARISH | NEUTRAL | UNKNOWN
```

The evidence lists are exposed **independently and always**, so a consumer can ignore
the verdict entirely and use the facts. The aggregation rule is deliberately trivial
and fully documented:

```
no evidence on either side            -> UNKNOWN
strictly more bullish than bearish    -> BULLISH
strictly more bearish than bullish    -> BEARISH
equal and non-zero                    -> NEUTRAL
```

Four sources contribute at most one item each: structural direction, delivery state
(CISD), the dealing-range zone (discount is bullish-leaning, premium bearish-leaning),
and the latest sweep's direction. **Conflicting evidence never gets forced into a
direction** — it produces `NEUTRAL`, which is a real answer, and absence produces
`UNKNOWN`, which is a different real answer.

This is **counting, not scoring**. There are no weights, because a weight is a
hypothesis and this story does not test hypotheses. If the counting rule turns out to
matter, it becomes an ablation in Phase 4, not a tuning knob here.

## 10. Timeframe-local

R2-07 as implemented is **timeframe-local**: a 5m state is built from 5m detectors.

The in-repo story text for R2-07 anticipated multi-timeframe assembly via
`align_htf_context()`, and that helper does exist and is the sanctioned join. The
R2-07 execution brief instead directed that this story remain timeframe-local and that
no HTF projection be implemented. **The brief is followed; the divergence is recorded
here rather than silently resolved.** When HTF context is authorised, `align_htf_context()`
— which joins on `close_time`, never on `timestamp` — is the only path, and nothing in
this layer needs restructuring to accept it.

No 1D or 1W timeframe is fabricated. The state supports exactly the timeframes the
engine already has bars for.

## 10a. Batch vs prefix replay — the one permitted asymmetry

`batch == prefix replay == true bar-by-bar` holds for every component, with exactly one
documented exception, and it is a property of R2-05.1 rather than of this layer.

**The True Daily Open is the engine's only zero-lag event.** It is a bar's *open* price,
knowable the instant that bar opens, so its `confirmation_timestamp` equals its
`event_timestamp` (see [true_daily_open.md](true_daily_open.md) §4). But a frame
contains only **closed** bars, so a prefix ending at `t` cannot contain the bar that
*opened* at `t` — even though a live observer at `t` would already have seen its open
print.

At exactly that instant the prefix therefore reports either **no** daily open, or the
**previous day's**, where the full frame reports the new one — and
`session.trading_day_age_minutes`, which is derived from it, moves with it.

Note the shape: `daily_open` is a *most-recent pointer*, not an accumulating set, so
"the prefix sees less" means **staler**, not merely absent. A subset test over ids
would be asking the wrong question about this one field; the right question is that the
prefix's level is never **newer**.

```
prefix sees LESS than the full frame   <-  safe, and what happens here
prefix sees MORE than the full frame   <-  a leak, and it never happens
```

The real-data suite asserts this precisely: states must be equal, and where they are
not, the difference must be confined to those two fields, the prefix's must be the
empty one, and the full frame's level must confirm exactly at `as_of`. A separate test
asserts the direction independently — every provenance id a prefix emits is a subset of
what the full frame emits at the same instant.

This is not a softened equality. It names the single case, pins its shape, and would
fail loudly if the asymmetry ever pointed the other way.

## 11. Determinism and serialisation

Both records serialise through `as_dict()` with **stable field order** (dataclass field
order), enum values as their `str` value, timestamps as ISO-8601 UTC, and `None` for
missing. `ICTFeatureVector` additionally exposes `FEATURE_NAMES` — a module-level
tuple that *is* the column order — and `as_row()` returning floats in exactly that
order.

`ICTFeatureVector.from_dict(v.as_dict()) == v` round-trips exactly, including `None`
and NaN semantics, and `schema_version` is carried on the record so a dataset can be
tied to the definitions that produced it.

## 12. Known ambiguity

| Element | Status |
|---|---|
| Aggregating detectors into one point-in-time state | **Engineering, not ICT.** No source defines a "market state object". |
| The bias counting rule | **Engineering assumption**, documented in §9. Evidence is exposed separately precisely so the rule can be ignored or replaced. |
| Discount ⇒ bullish-leaning, premium ⇒ bearish-leaning | **Interpretation.** ICT describes buying in discount and selling in premium; treating that as directional *evidence* is this layer's reading, and it is one of four inputs rather than a decision. |
| `bars_since_*` measured in bars, not time | **Engineering choice** — bars are what the detectors index, and across a weekend a time-based measure would imply activity that did not occur. |
| Points as the universal distance unit | **Engineering choice** (§8), chosen over price so features are comparable across instruments without a normalisation hypothesis. |
| Timeframe-local scope | **Directed by the R2-07 brief**; see §10 for the divergence from the in-repo story text. |

## 13. Explicit non-goals

No labels or targets (Phase 3) · no model training (Phase 4) · no Kronos (Phase 5) ·
no backtesting or execution · no setup-quality scoring · no optimisation · no ATR or
volatility normalisation · no cross-timeframe projection · no new ICT concepts.
