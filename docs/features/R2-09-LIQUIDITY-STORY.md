# R2-09 — Liquidity Model — STORY

**Specification. Written before `ict_kronos/ict/liquidity_model.py` exists.**
Master story: [Phase-2-Market-Intelligence-STORY.md](../Phase-2-Market-Intelligence-STORY.md)
· Concept map: [R2-09-CONCEPT-MAP.md](R2-09-CONCEPT-MAP.md)
· Tasks: [R2-09-TASKS.md](../../tasks/Phase-2-ICT-Engine/R2-09-TASKS.md)

> **SPECIFICATION ONLY — implementation NOT started, NOT approved.**
>
> **Production timeframes: 1H / 4H / 1D only.** No dependency below 1H or above 1D, direct
> or indirect. `assert_production_pair` is called before any picture is built and **raises**
> rather than converting.
>
> **HARD STOP at the end. R2-10 does not begin without explicit approval.**

---

## 0. Timeframe locality — binding

> **R2-09 is timeframe-local. It operates independently on the supplied 1H, 4H, or 1D
> point-in-time dataset. Multi-timeframe relationships are exclusively owned by R2-11.**

R2-09 **MUST NOT**: request lower-timeframe data · construct lower-timeframe candles ·
derive liquidity information from a lower timeframe · perform a hidden MTF join · introduce
an implicit 1H → 4H → 1D hierarchy inside the liquidity model.

Production 4H is supplied by the production dataset as **exactly four valid native 1H
bars**; production 1D is the **native provider 1D** candle. R2-09 consumes what it is
given and constructs neither. Guard tests assert the module imports no resampler, no
`build_h4_from_native_h1`, and reads no partition other than the one it was handed.

---

## 1. The question this layer answers

> **Where is meaningful liquidity relative to the current market state, and what has
> already been taken?**

Note what the question does *not* ask. It does not ask where price will go, which pool is
the target, or which side is "in control". Those are Phase 4 questions and this layer is
forbidden from answering them (master story §9, ambiguity **G3**).

---

## 2. Relationship to R2-04 — extend, do not replace

R2-04 `LiquidityDetector` is **approved, shipped and covered by 210 tests**. Its source
file is not modified by this story.

| Concept | Already exists in R2-04 | R2-09 disposition |
|---|---|---|
| Swing-high / swing-low levels | ✅ `LiquidityType.SWING_HIGH` / `SWING_LOW` | **reuse** |
| Equal highs / equal lows | ✅ pairwise, tolerance 1.0 point, extreme-of-pair price | **reuse** |
| Previous day high / low | ✅ 17:00-NY day, confirms at day end | **reuse** |
| Previous week high / low | ✅ Sun→Fri 17:00 NY, confirms at week end | **reuse** |
| Session high / low | ✅ from R2-01 completed occurrences | **reuse** |
| Buy-side / sell-side | ✅ fixed at creation, never flips | **reuse** |
| Sweep (wick beyond + tolerance, confirms at bar close) | ✅ | **reuse, unchanged** |
| `closed_beyond` / `is_rejection` | ✅ | **reuse** |
| `APPROACHED` state | ✅ but **disabled by default** (`approach_tolerance_points=None`) | **wrap** — R2-09 turns it on with a derived tolerance, and additionally exposes distance directly |
| Lifecycle `PENDING → ACTIVE → [APPROACHED] → SWEPT` | ✅ | **reuse** |
| `PendingPeriod` (in-progress day/week) | ✅ exposed, never a level | **leave untouched** — R2-09 does not consume it (master story §7.2, G6) |
| **Liquidity pools / clustering** | ❌ *explicitly absent* — liquidity.md §14 limitation **2** | **NEW — R2-09** |
| **Internal vs external liquidity** | ❌ *explicitly deferred* — liquidity.md §14 limitation **3**, "needs R2-06's dealing range" | **NEW — R2-09** |
| **Approach/touch COUNT** | ⚠️ R2-04 records only the **first** approach instant, and ships the feature **disabled** | **R2-09 ENABLES and COUNTS it** as `approach_count`. **No new status** — `APPROACHED` = touched (§5) |
| **Liquidity asymmetry / direction summary** | ❌ | **NEW — R2-09** |
| **Distance normalisation, side priority** | ❌ | **NEW — R2-09** |
| **Liquidity significance** | ❌ | **NEW — R2-09**, as a vector of measurable attributes (§5a) |
| Previous *month* high/low | ❌ | **NOT BUILT** — §3.2 |

**The verdict, stated once:** R2-09 **wraps** `LiquidityDetector`. It consumes
`LiquidityAnalysis` through `active_at()`, `swept_by()` and `state_of()`, and it produces
a *derived, point-in-time view*. It never re-detects a level, never re-detects a sweep,
never mutates an R2-04 record, and never redefines an R2-04 term.

A guard test asserts `liquidity_model.py` imports no swing, session, structure or
resampler module — only `liquidity`, `dealing_range` and `contract`.

---

## 2a. Input contract — AD-1 RESOLVED (bar frame approved, for measurement only)

```
LiquidityAnalysis          R2-04, authoritative for detection and lifecycle
DealingRangeAnalysis       R2-06, supplies the structural range
PointInTimeBarFrame        NEW -- an OBSERVATION SOURCE, not a second detector
```

The bar frame is approved because **R2-04 does not expose every measurable quantity the
significance model needs**. Verified in the repository, not assumed:

| Required quantity | Available from R2-04? |
|---|---|
| Touch count | ❌ `LiquidityAnalysis.approached` is `dict[str, datetime]` recording only the **first** approach (`liquidity.py:749` guards on `not in analysis.approached`), and it is **disabled by default** (`approach_tolerance_points=None`) |
| Age | ⚠️ derivable from `confirmation_timestamp`, but bar-count age needs the frame |
| Distance to current price | ❌ needs the current bar's close |

### 2a.1 Responsibility boundary — binding

| R2-04 remains authoritative for | R2-09 may measure from already-detected levels |
|---|---|
| liquidity detection | touch count |
| level creation | age |
| lifecycle | distance from current price |
| **sweep detection and `SWEPT`** | structural context |
| existing identity | sweep / lifecycle status |
| existing provenance | significance components |

### 2a.2 The critical guard — R2-09 MUST NOT

1. **discover new liquidity levels independently** — every level comes from R2-04, by id;
2. **change an R2-04 level's lifecycle** — records are frozen and are never written back;
3. **redefine `SWEPT`** — R2-04's definition is authoritative and unchanged;
4. **create a competing liquidity identity** — pool ids are built *from* member level ids;
5. **use future bars to measure a historical row** — every measurement is point-in-time.

Enforced by guard tests: `liquidity_model.py` imports `liquidity`, `dealing_range` and
`contract` only — **never** `swings`, `sessions`, `structure`, `resample` or
`build_h4_from_native_h1` — and contains no level-construction call.

### 2a.3 Point-in-time bar access is the whole risk

Walking bars is where a future touch can be counted. The rule:

> At `as_of`, R2-09 reads **only** bars with `close_time <= as_of`, and **only** against
> levels already observable at `as_of`.

This is the story's deliberately incorrect implementation (§10.1): *count future touches
after `as_of`*.

---

## 3. Buy-side and sell-side liquidity — the candidate register

Per the brief, **not every possible liquidity concept is automatically included.** Each
candidate below carries source, rule, confirmation, observability, provenance, identity,
invalidation, lifecycle, taken-state and timestamp semantics.

### 3.1 Included (all inherited from R2-04, unchanged)

| Candidate | Side | Exact rule | Confirms at | Identity | Provenance |
|---|---|---|---|---|---|
| **Swing high** | buy | R2-02 confirmed pivot, `left`/`right` bars | pivot bar + `right` bars, at that bar's close | `swing:{dir}:{pivot ISO}` | `source_swing_timestamps = (pivot,)` |
| **Swing low** | sell | as above | as above | as above | as above |
| **Equal highs** | buy | two confirmed swing highs ≤ `equal_max_swing_distance` apart in the same-type sequence with `abs(B.price − A.price) <= equal_tolerance_points × point`; level price = `max` of the pair | `max(A.confirmation, B.confirmation)` | `equal:equal_highs:{A ISO}:{B ISO}` | both pivot timestamps |
| **Equal lows** | sell | mirror; level price = `min` of the pair | as above | as above | as above |
| **Previous day high / low** | buy / sell | extreme of a **completed** 17:00-NY day window that contained bars | the day window's **end** | `day:{type}:{local date}` | `period_label`, `period_start`, `period_end` |
| **Previous week high / low** | buy / sell | extreme across the day windows Sun…Thu of a **completed** trading week | the week's end (Fri 17:00 NY) | `week:{type}:{anchor Sunday}` | as above |
| **Session high / low** | buy / sell | extreme of an R2-01 **completed** session occurrence | the session window's end | `session:{type}:{name}:{local date}` | as above |

**Invalidation and lifecycle for every one of them** is R2-04's, verbatim: there is no
mechanism by which a level becomes invalid *without* being swept; `SWEPT` is terminal and
**is** the consumption; a swept level leaves the active set and can never be swept twice;
levels are frozen dataclasses so a later bar cannot revise one.

**Timestamp semantics** are R2-04's table (liquidity.md §8), unchanged, and
`confirmation >= created` is enforced by the shared contract constructor.

### 3.2 Deliberately NOT included, and why

| Candidate | Why not |
|---|---|
| **Previous month high / low** | Trivially derivable from the day windows, but nothing in the source material treats a *calendar month* as an ICT reference the way it treats day and week. Adding it would be inventing a fourth period without evidence. Recorded as the cheapest future extension |
| **A "significant high" as a distinct LEVEL TYPE** | Still not built, and this is narrower than it looks. **Significance is built (§5a)** — as an attribute vector over pools that already exist. What is *not* built is a new level type admitted or rejected on a significance test, because "significant" has no admission rule in the source material and HANDOFF item 6 records swing significance as an open R2-02/R2-03 concern. **Describing significance is engineering; using it as a gate would be inventing doctrine.** Ambiguity **A6** |
| **Trendline liquidity / diagonal liquidity** | Requires fitting a line to pivots; the fit is not deterministic without a further arbitrary rule (which pivots, how many, what tolerance) |
| **Relative equal highs at a *looser* tolerance than R2-04's** | Would create a second, competing answer to "are these two swings equal?". R2-04 owns that question. The pool tolerance (§4) answers a *different* question and is kept visibly separate — see §4.1 |
| **Round-number / psychological levels** | Not an ICT construct in the source material; and it would be a hardcoded price grid, which CLAUDE.md rule 4 forbids |
| **Volume-profile / order-flow liquidity** | The data does not exist. Dukascopy volume is a **tick count**, recorded as such in the manifest, and is not exchange volume |
| **Forming-period running extremes** (`PendingPeriod`) | Real information, but it is not a *level* (liquidity.md §6). Feeding it into a feature would reintroduce the exact conflation R2-04 exists to prevent. Master story G6 |

---

## 4. Liquidity pools

### 4.1 What a pool is

> A **pool** is the set of observable, untaken liquidity levels on one side whose prices
> lie within a clustering tolerance of one another, at a given instant.

Three things follow immediately, and each is a deliberate decision:

1. **A pool is a derived, point-in-time view — not a confirmed immutable event.** It has
   no lifecycle of its own, no `SWEPT` state and no invalidation rule. Its members have
   all of those. This is the central architectural choice of R2-09; the rejected
   alternative (pools as immutable confirmed events) is in
   [the concept map](R2-09-CONCEPT-MAP.md) §3 candidate **P3**.
2. **Pool membership legitimately changes over time**, because the observable set grows
   and because members get swept. That is not mutation — the pool at `T₁` and the pool at
   `T₂` are two different derived objects with two different ids.
3. **A pool tolerance is not the equal-highs tolerance.** They answer different questions:

```
equal_tolerance_points      "are these two SWING pivots the same high?"     R2-04 owns it
pool_tolerance_points       "do these HETEROGENEOUS levels -- a PDH, a      R2-09 owns it
                             session high and an equal-highs level --
                             represent one place stops rest?"
```

Conflating them would silently change R2-04's equal-highs output. A test asserts the two
config values are independent and that changing the pool tolerance does not change any
`LiquidityLevel`.

### 4.2 The clustering rule

Deterministic single-linkage over a price-sorted list, per side, over the **observable,
untaken** set at `as_of`:

```
levels := analysis.active_at(as_of) filtered to one side
sort by (price_level, level_id)          # level_id breaks price ties deterministically
walk the sorted list; start a new cluster when
    price[i] - price[i-1] > pool_tolerance
```

| Property | Value |
|---|---|
| **Tolerance unit** | instrument **points** (`symbol.spec.point_value`) — the engine's universal distance unit; prices and points are never mixed |
| **Tolerance value** | per `(symbol, timeframe)`, derived from **measured median bar range** — §4.5 |
| **Instrument-specific?** | **Yes, necessarily.** A point is not a unit of volatility: 50 points is 0.0005 on EURUSD and five cents on gold. This is the same argument `ProductionTargetParameters` already makes |
| **Minimum cardinality** | `2` (configurable, `>= 1`). A "pool" of one is a level, and calling it a pool adds nothing |
| **Can one level belong to two pools?** | **No.** Single linkage partitions the side's level set, so membership is exclusive by construction. Chaining (A–B–C where A and C are further apart than the tolerance) puts all three in one pool — accepted, documented, and the alternative is rejected in the concept map |
| **Pool price** | the **extreme** of its members: `max` for buy-side, `min` for sell-side. Rationale is R2-04's, unchanged: *stops rest beyond the furthest touch, so that is the price a sweep must exceed* |
| **Pool identity** | `pool:{side}:{sha1(",".join(sorted(member level ids)))[:12]}` |
| **Pool confirmation** | `max(member.confirmation_timestamp)` — the composite rule, via `composite_confirmation()`. By construction ≤ `as_of`, because every member came from `active_at(as_of)` |
| **Levels not in any pool** | Still fully reported as levels. A pool is an *additional* view, never a filter |

### 4.3 Why `level_id` breaks price ties

Two levels can share a price exactly — a PDH and a session high, say. Sorting on price
alone leaves their order to the input sequence, which makes the resulting pool id
non-deterministic across runs. `(price, level_id)` is total and stable. This is the same
tiebreaker `_latest()` already uses in `market_state.py`.

### 4.4 Internal versus external liquidity

R2-04 limitation **3** says this needs a structural range, and R2-06 now provides one.

```
range := dealing_range.range_at(as_of)      # None before the first confirmed break

EXTERNAL   level.price > range.high_price   (buy-side)
           level.price < range.low_price    (sell-side)
INTERNAL   otherwise, i.e. inside [low_price, high_price]
UNKNOWN    range is None
```

**`UNKNOWN` is a third value and is never collapsed into `INTERNAL`.** Before the first
confirmed structural break there is no range, and "inside a range that does not exist" is
not a fact.

Two consequences worth stating plainly, both inherited from R2-06:

- The range high is the **broken** structural level, not the highest price traded
  (dealing_range.md §11). So immediately after a break, price itself is commonly outside
  the range — 42–81 % of real observations have `percentage_position` outside `[0, 1]`.
  Levels *at* current price are therefore routinely classified **EXTERNAL**. That is
  correct and must not be "fixed".
- The classification is a property of `(level, range)` at an instant. It changes when the
  range changes. It is a derived view, exactly like pool membership.

### 4.5 The tolerance value — evidence, not invention

**ICT does not state a pool tolerance.** The source material describes stops clustering
"around" equal highs and "above" old highs; it gives no number, no unit and no instrument
adjustment. Inventing one would be inventing doctrine (ambiguity **A1**).

The repository has faced this exact problem once before, in R2-08, and answered it with a
**measured** rule (`production_universe.md` §4). R2-09 reuses that method verbatim:

```
pool_tolerance_points(symbol, timeframe) := median bar range of that pair,
                                            measured on a declared real-data month,
                                            divided by POOL_TOLERANCE_DIVISOR
```

with `POOL_TOLERANCE_DIVISOR` a single, declared, configurable number applied uniformly to
every pair — so nothing is chosen case by case.

| Decision | Value | Justification |
|---|---|---|
| Base quantity | median bar range per `(symbol, timeframe)` | Already measured and recorded in `PRODUCTION_TARGET_PARAMETERS.take_profit_points` |
| Divisor | **`10`**, uniform | A tenth of a typical bar's range is a distance the instrument crosses many times within a single bar, so levels that close are indistinguishable to an execution engine. Any uniform divisor is a judgement; this one is declared, applied identically to all six pairs, and is **configuration** |
| Provenance | `rationale` string on every parameter row, exactly as `ProductionTargetParameters` does | A reviewer never has to guess where a number came from |

**Measured on the approved six-month production evidence** (2026-02-01 → 2026-08-01,
`production-native-2026-02_08`), read-only, on 2026-08-21. **One value per instrument ×
timeframe**, units in instrument points throughout — never mixed across instruments:

| Symbol | TF | Bars | Median range (pts) | **Pool tolerance (pts)** | In price |
|---|---|---|---|---|---|
| EURUSD | 1H | 3 120 | 102.0 | **10.2** | 0.000102 |
| EURUSD | 4H | 754 | 210.5 | **21.0** | 0.000210 |
| EURUSD | 1D | 156 | 536.5 | **53.6** | 0.000536 |
| XAUUSD | 1H | 2 955 | 19 020.0 | **1 902.0** | 1.902 USD |
| XAUUSD | 4H | 642 | 41 240.0 | **4 124.0** | 4.124 USD |
| XAUUSD | 1D | 155 | 99 160.0 | **9 916.0** | 9.916 USD |

**These supersede a provisional table derived from July 2026 alone.** The drift is material
— EURUSD 1H median range 85 → 102 pts, XAUUSD 1H 13 840 → 19 020 pts — which is exactly why
six-month calibration is mandated. The implementation must **recompute and assert** these
values from the manifested partitions rather than copying them by hand.

### 4.5a Calibration is a one-time, offline, leak-free step — and it is NOT re-derived at analysis time

The brief requires the derivation to be *"deterministic, documented and free of future/test
leakage"*. Three rules make that true, and each closes a specific hole:

| Rule | Closes |
|---|---|
| **1. Calibration runs ONCE, offline, and its output is a committed constant table** with the measured inputs and a `rationale` string per row | A tolerance re-measured at analysis time would be a *dataset-wide statistic used per row* — exactly the leak master story §6.5 forbids for normalisation, wearing a configuration parameter's clothes |
| **2. `LiquidityModel` never measures anything.** It reads `POOL_TOLERANCE_PARAMETERS[(symbol, timeframe)]` and nothing else. A guard test asserts the module computes no median, quantile, `std` or rolling statistic over its input frame | The same leak, introduced by convenience |
| **3. Calibration reads the full six-month production window and says so.** It is **not** fitted on a train split, because it is not fitted at all — it is a declared instrument scale, and tying it to a split would make the tolerance change whenever the split does | A tolerance that silently varies per experiment |

**Why this is not "fitting on test data" despite reading the whole window.** A fitted
parameter is chosen because it optimises an outcome. Nothing here is: the divisor is
declared before any measurement, applied uniformly to all six pairs, and no value is
selected because it produced a nicer pool count. The measured quantity is the instrument's
*typical bar range* — a property of the instrument, not of any label. The distinction is
recorded because it is exactly the distinction a reviewer must be able to check.

**A stricter alternative was considered and rejected:** derive tolerances from the earliest
month only, so no later data touches them at all. Rejected because a one-month volatility
estimate is noisier than a six-month one, and the noise would propagate into every pool on
every later month — trading a real measurement error for a theoretical purity that the
"not fitted to an outcome" argument already provides. **Recorded so the choice is visible.**

**Honest limitation, carried through from R2-08:** the six-month window is the same window
the pipeline is validated on. These are a volatility *scale*, not a fitted parameter, but
they should be re-derived when a longer history exists. Task `R2-09-m` requires the
completion report to state pool-count sensitivity to the divisor at `5`, `10` and `20`,
**as a description, never as a selection**.

### 4.6 Approach tolerance

R2-09 enables R2-04's dormant `approach_tolerance_points` using the **same** derived value
as the pool tolerance, for one reason: "close enough to be the same pool" and "close enough
to be approaching" are the same physical claim about the instrument's scale.

But the **primary** representation of approach is the **distance itself**, not the state.
`nearest_buy_side_pool_points` carries strictly more information than
`nearest_buy_side_is_approached`, and it carries it without a threshold. The state is
retained because R2-04 already defines it and it costs nothing; the feature layer prefers
the distance.

---

## 5. Interaction states — R2-04's lifecycle, unchanged

> **APPROACHED = TOUCHED.** APPROACHED is the engine's canonical representation of a
> liquidity level being touched/approached. R2-09 reuses this existing lifecycle meaning and
> **must not introduce a parallel `TOUCHED` status.**

**There is no `TOUCHED` enum value, and none may be added.** R2-04's lifecycle is
authoritative and R2-09 adds no state to it:

```
PENDING      created, not yet observable                     R2-04, unchanged
ACTIVE       observable and untaken                          R2-04, unchanged
APPROACHED   price came to the level without exceeding it    R2-04, unchanged
             -- this IS "touched". Non-consuming; the level stays fully usable.
             R2-09 ENABLES it (R2-04 ships it disabled) and MEASURES it.
SWEPT        price traded beyond the level, on a closed bar  R2-04, unchanged. Terminal.
```

**Human-readable prose may say "touched / approached".** The machine-readable status
remains `APPROACHED`, everywhere, in every record and every serialisation.

### 5.0 What R2-09 adds, and what it does not

| R2-09 adds | R2-09 does **not** |
|---|---|
| `approach_count` — **how many times** the level was approached/touched up to `as_of`. R2-04 records only the *first* approach instant (`liquidity.py:749`) | a new status, a new enum value, a renamed state |
| `CONFIRMED_TAKEN` as a **derived read** of R2-04's existing `closed_beyond` — a label over published data, **not** a lifecycle state | any change to `SWEPT`, or a second consumption rule |
| Enabling `approach_tolerance_points` with the calibrated value (§4.6) | changing what APPROACHED *means* |

Creating a `TOUCHED` enum, renaming `APPROACHED`, or modelling two concepts for one event
are **hard stops** (§16).

### 5.1 The four questions the definition must answer

| Question | R2-09's answer | Alternatives rejected |
|---|---|---|
| Is crossing the level enough? | **Yes** — the extreme must exceed `price ± sweep_tolerance`; a wick suffices. R2-04's rule, unchanged | *Close through* is a structural break (R2-03), a different concept. *Touch* takes no stops |
| Must price return? | **No.** Return is recorded (`closed_beyond=False` ⇒ `is_rejection`), never required | Requiring return would make "swept" unknowable until an unbounded wait; R2-04 removed a `require_rejection` flag for exactly this reason |
| Must the close return? | **No, but it is recorded**, and that recording is what `CONFIRMED_TAKEN` reads | — |
| Is liquidity consumed immediately? | **Yes, at the sweeping bar's close.** `SWEPT` is terminal; the level leaves the active set | Deferred consumption would let one level be swept twice |

### 5.2 Wick-only versus close-confirmed

**Both are supported and both are reported, as two different fields.** They are not two
configurations of one thing:

- `SWEPT` (wick) answers *"were the resting stops taken?"*
- `CONFIRMED_TAKEN` (close beyond) answers *"did the market accept the new level?"*

A textbook stop-hunt is `SWEPT and not CONFIRMED_TAKEN`. A genuine break-through is both.
Collapsing them would destroy the distinction that makes the sweep interesting.

### 5.3 Partial penetration and exact touch

| Case | Behaviour |
|---|---|
| Extreme exceeds by `0 < d <= sweep_tolerance` | **Not a sweep.** Counted in `approach_count` if within `approach_tolerance`; status stays `APPROACHED` |
| Extreme exactly equals the level | **Not a sweep** (R2-04's strict comparison). Counted in `approach_count`; status `APPROACHED` |
| Extreme exceeds by `d > sweep_tolerance` | `SWEPT`, with `penetration_points = d` recorded so "barely tagged" and "blown through" stay distinguishable |
| The bar that *created* the level | Cannot sweep or touch it — its extreme **is** the level by construction. R2-04 already prevents this by requiring `close_time >= level.confirmation_timestamp` |
| One bar sweeping several levels | One sweep event per level, one pool interaction per pool. Several distinct pools of liquidity were genuinely taken |
| Gap-open through a level | A valid sweep. The bar's extreme exceeded and the bar closed. Lifecycle is driven by bars, never by wall-clock elapsed time |

`approach_tolerance_points` is set from the calibrated pair value (§4.6), so approach and
pool clustering share one physical scale. R2-04 ships it as `None` (disabled); **enabling it
is R2-09's change, and it changes no R2-04 semantics** — only whether the existing state is
recorded.

### 5.4 Can a sweep happen more than once?

**No, per level.** `SWEPT` is terminal (R2-04, unchanged). A later trade through the same
price belongs to some *other* level, which has its own identity and its own sweep.

**Yes, per pool** — and this is a genuinely new thing R2-09 can say. A pool of three levels
can be swept incrementally: the first sweep takes one member, and the pool at the next
instant is a *different* pool (two members, possibly a different price). R2-09 therefore
reports, per side, the **cumulative count of swept levels** and the **pool's remaining
cardinality**, and never claims "the pool was swept" as a single atomic event.

---

## 5a. Significance — AD-2 RESOLVED (no invented reaction formula)

> **Significance is a vector of independently measurable components, not a claim.**
> Every component is exposed separately. The composite is optional and off by default.

### 5a.1 The seven approved components

Each is measurable from existing semantics plus point-in-time bar data. **None is invented
ICT doctrine.**

| # | Component | Definition | Source | Missing means |
|---|---|---|---|---|
| 1 | `pool_cardinality` | `len(member_level_ids)` — **pool size** | R2-09 over R2-04 levels | — (never; a pool has ≥ 2) |
| 2 | `pool_density` | §5a.1a — deterministic from span and cardinality | R2-09 | — |
| 3 | `span_points` | `(max member price − min member price) / point_value` | R2-09 | — (`0.0` when all members share a price) |
| 4 | `approach_count` | Bars with `close_time <= as_of` on which the level was **approached/touched** (§5), counted against levels already observable at `as_of` | R2-09 measurement over R2-04 levels | — (a real `0`) |
| 5 | `age_bars` | Production bars between the level's `confirmation_timestamp` and `as_of` | R2-04 + frame | — |
| 6 | `distance_to_current_price_points` | `abs(level.price − close)` in instrument points | R2-04 + frame | no current bar |
| 7 | `structural_context` | `INTERNAL` / `EXTERNAL` / `UNKNOWN` versus the R2-06 dealing range | R2-06 | `UNKNOWN` when no range exists |

Plus `sweep_lifecycle_status` (R2-04's status, unchanged) and `reaction_status` (§5a.2),
both carried on the record.

**Cardinality and density are significance COMPONENTS, not passive attributes.** An earlier
draft treated them as descriptive pool fields only; they are first-class here.

`age_bars` counts **bars, not elapsed time** — across a weekend a time measure would imply
activity that did not occur (the R2-07 rule).

### 5a.1a Pool density — the deterministic definition

```
pool_density = cardinality / (1 + span_points / pool_tolerance_points)
```

| Property | Value |
|---|---|
| Units | dimensionless |
| Range | `(0, cardinality]` |
| All members at one price (`span = 0`) | `= cardinality` — maximum density |
| Span equal to one tolerance | `= cardinality / 2` |
| Monotonic | increasing in cardinality, decreasing in span |
| Division by zero | impossible — `pool_tolerance_points > 0` is enforced in `__post_init__` |

**This is engineering, not ICT doctrine**, and it is labelled as such. The obvious
alternative — `cardinality / span_points` — is **rejected**: it is singular at `span = 0`,
which is the *tightest and most interesting* pool, so the natural formula is undefined
exactly where the concept matters most. The `+1` normalisation removes the singularity
without changing the ordering for any non-degenerate pool.

`pool_tolerance_points` here is the **configured constant** for the pair (§4.5), never a
runtime-measured quantity.

### 5a.2 Reaction — intentionally NOT scored

> **Reaction is intentionally not scored in R2-09 until a deterministic definition is
> approved.**

The repository contains **no** deterministic definition of displacement or reaction *after a
liquidity-level interaction*. R2-03's `displacement_ratio` measures **structural-break**
displacement — a different concept over different anchors — and **may not be reused** for
this purpose.

Therefore:

```
reaction_status = NOT_AVAILABLE
```

`NOT_AVAILABLE` is a **schema value**. It is **not** zero, **not** false, **not** neutral,
**not** a failed reaction, and **not** a calculated value. Nothing may infer that a missing
reaction means "no reaction occurred".

`reaction_status: ReactionStatus` is emitted with the single value `NOT_AVAILABLE` in v1, so
the absence is **a declared state in the schema** rather than a missing field.

**The schema stays extensible.** When a formally approved reaction definition arrives, the
enum gains values and an optional `reaction_value: float | None` is populated — **no
consumer's shape changes and no existing field's meaning changes**. That is the whole reason
the sentinel is a declared enum rather than a `None`.

**No heuristic is manufactured to complete the component list.** The five measurable
components above remain fully available without it.

### 5a.3 Components deliberately dropped from an earlier draft

| Dropped | Why |
|---|---|
| `type_rank` (a level-type ordinal) | An earlier draft ranked PWH > PDH > equal-highs > session > swing, justified by "duration of exposure". **No source ranks level types.** That is an invented ordinal presented as doctrine, so it is removed. Pool composition remains visible via `member_types` and `distinct_type_count` as *pool attributes*, not as a significance weight |
| `displacement` | AD-2 — no deterministic definition |

### 5a.4 The composite

`significance_score` — optional, **off by default**, formula deterministic, documented,
point-in-time safe, reproducible and **independently tested**. Weights are declared
configuration; **nothing in this phase fits or tunes them**, and nothing measures them
against an outcome.

Three guards:

1. The five components are **always** present, so a consumer that distrusts the composite
   can ignore it entirely.
2. A guard test asserts the composite **never filters, ranks-and-truncates, or selects**
   inside R2-09. It is a projected number, never a gate.
3. It stays disabled until validated — validation is a later phase's work, not R2-09's.

---

## 6. Liquidity direction and the nearest valid candidate

Reported as measurement only. **No target is claimed** (ambiguity **A3**, master story
**G3**).

### 6.0 "Nearest valid liquidity candidate"

The brief asks for a nearest **valid** candidate, which requires *valid* to be defined
rather than assumed. Validity here is a **filter over observability and state**, never over
significance or predicted relevance:

```
valid at as_of  :=  observable at as_of            (confirmation_timestamp <= as_of)
                AND not swept by as_of
                AND on the requested side
                AND cardinality >= min_pool_cardinality   (for a pool candidate)
```

`nearest_valid_buy_side` / `nearest_valid_sell_side` are then the minimum by
`(abs(pool.price − close), pool_id)` over that set — the `pool_id` tiebreaker making the
choice deterministic when two pools sit equidistant.

**Validity deliberately does not include a significance floor.** Filtering candidates by
significance would make the "nearest valid" answer depend on an unvalidated weighting, and
would hide the very levels a Phase 4 ablation needs in order to test whether significance
matters at all.

| Field | Definition | Missing means |
|---|---|---|
| `nearest_buy_side_pool` | pool with the smallest `abs(pool.price − close)` among buy-side pools; ties broken by `pool_id` | no observable untaken buy-side pool |
| `nearest_sell_side_pool` | mirror | no observable untaken sell-side pool |
| `nearest_buy_side_points` | `abs(pool.price − close) / point_value`, **absolute** | as above |
| `nearest_sell_side_points` | mirror | as above |
| `nearest_side` | `+1` if the buy-side pool is nearer, `−1` if the sell-side pool is nearer, `0` if exactly equal | **either side missing** — not `0` |
| `nearest_relative_position` | `sell_points / (buy_points + sell_points)`, in `[0, 1]`; `0` = hard against sell-side liquidity, `1` = hard against buy-side | either side missing, or both distances zero |
| `above_below` | Not a separate field. **Buy-side is above and sell-side is below, by definition and by construction** — R2-04 fixes the side at creation and it never flips | — |
| `untouched` / `taken` | `active_at()` gives untaken; `swept_by()` gives taken. Both are counted per side | never missing (a real `0` means "none") |
| `liquidity_asymmetry` | `(buy_count − sell_count) / (buy_count + sell_count)`, in `[−1, +1]` | **both counts zero** |

**"Relative priority" is deliberately not modelled.** Ranking pools by importance requires
a significance rule that the source material does not provide. What R2-09 exposes instead
is the raw material a ranking would need — distance, cardinality, member types, age,
internal/external — so Phase 4 can *test* a ranking rather than R2-09 asserting one.

---

## 7. Provenance

> **Every liquidity object is traceable to its source, and provenance is never
> reconstructed from price geometry.**

| Object | Provenance | Resolves against |
|---|---|---|
| `LiquidityLevel` | R2-04's own: `source_swing_timestamps`, `period_label`, `period_start`, `period_end` | `LiquidityAnalysis.level_by_id` |
| `LiquidityPool` | `member_level_ids: tuple[str, ...]` — **ids, never prices** — **plus the union of the constituent levels' own provenance**, reachable by resolving each id | `LiquidityAnalysis.level_by_id` for each |
| `PoolInteraction` | `pool_id` + `level_id` + R2-04's `LiquiditySweep` | both registries |
| Internal/external label | `range_id` of the R2-06 range that produced it | `DealingRangeAnalysis.range_by_id` |

Reconstructing a pool's membership from "which levels are near this price" is the failure
mode this rule forbids: two levels can share a price exactly (the R2-05.2 audit found two
real id collisions caused by exactly that shortcut), so price is not an identifier.

**A pool must never lose the ability to trace back to the original R2-04 levels.**
`pool_provenance()` returns the union of every member's provenance — `source_swing_timestamps`,
`period_label`, `period_start`, `period_end` — resolved through the registry, **never**
replaced by opaque aggregate-only metadata and never flattened into a summary.

**Serialisation preserves provenance.** `from_dict(as_dict()) == value` reproduces the same
logical object including `member_level_ids` in the same order, and a test asserts the
round-tripped pool still resolves every member.

`assert_provenance_resolves` and `assert_sources_observable_first` — the shared helpers
built in R2-05.2 — are reused verbatim; no new provenance helper is written.

---

## 8. Output schema

### 8.1 `LiquidityPool` (frozen)

| Field | Type | Notes |
|---|---|---|
| `pool_id` | `str` | `pool:{side}:{sha1(sorted member ids)[:12]}` |
| `symbol`, `timeframe` | `str` | |
| `side` | `LiquiditySide` | R2-04's enum, reused |
| `price_level` | `float` | extreme of members (max buy-side, min sell-side) |
| `member_level_ids` | `tuple[str, ...]` | **sorted**; the provenance |
| `cardinality` | `int` | `len(member_level_ids)`, `>= min_pool_cardinality` |
| `span_points` | `float` | `(max member price − min member price) / point_value`; `0.0` for a degenerate one-member pool |
| `member_types` | `tuple[str, ...]` | sorted distinct `LiquidityType` values in the pool |
| `created_timestamp` | `datetime` | `min(member.created_timestamp)` — when the earliest constituent price action occurred |
| `confirmation_timestamp` | `datetime` | `max(member.confirmation_timestamp)` — via `composite_confirmation()` |
| `zone` | `PoolZone` | `INTERNAL` / `EXTERNAL` / `UNKNOWN` (§4.4) |
| `source_range_id` | `str \| None` | the R2-06 range that produced `zone`; `None` iff `zone is UNKNOWN` |
| `tolerance_points` | `float` | the clustering tolerance in force, carried for auditability |
| `schema_version` | `str` | `LIQUIDITY_MODEL_VERSION` |

`is_observable_at()` delegates to the one contract predicate. **No hand-rolled comparison.**

### 8.2 `LiquidityPicture` — the point-in-time answer

The object R2-12 consumes. Not persisted; derived per instant.

| Field | Type |
|---|---|
| `symbol`, `timeframe`, `as_of` | `str`, `str`, `datetime` |
| `buy_side_pools`, `sell_side_pools` | `tuple[LiquidityPool, ...]`, ordered by distance from `close` then `pool_id` |
| `unpooled_buy_side_ids`, `unpooled_sell_side_ids` | `tuple[str, ...]` — levels in no pool |
| `nearest_buy_side_pool_id`, `nearest_sell_side_pool_id` | `str \| None` |
| `nearest_buy_side_points`, `nearest_sell_side_points` | `float \| None` |
| `internal_buy_side_count` … `external_sell_side_count` | `int` (four fields) |
| `unknown_zone_count` | `int` |
| `taken_buy_side_count`, `taken_sell_side_count` | `int` — cumulative levels swept by side, observable at `as_of` |
| `approached_level_ids` | `tuple[str, ...]` — touched/approached, status `APPROACHED` |
| `confirmed_taken_level_ids` | `tuple[str, ...]` — swept **and** closed beyond |
| `bars_since_buy_side_sweep`, `bars_since_sell_side_sweep` | `int \| None` |
| `liquidity_asymmetry` | `float \| None` |
| `nearest_side_code` | `int \| None` |
| `nearest_relative_position` | `float \| None` |
| `schema_version` | `str` |

### 8.3 Serialisation

`as_dict()` / `from_dict()` follow the engine convention exactly (master story §7.3):
stable field order, enums by `.value`, timestamps ISO-8601 UTC, tuples as lists, `None` for
missing — **never `0`, never `NaN`** — and `from_dict(as_dict()) == value` exactly.

`LIQUIDITY_MODEL_VERSION = "r2-09.1"`. R2-07's `STATE_VERSION` and `FEATURE_VERSION` are
**not** touched by this story: R2-09 ships standalone and is not wired into the state until
R2-12.

---

## 9. Data quality

R2-09 introduces **no new data-quality policy**. It propagates R2-08's.

| Situation | Behaviour |
|---|---|
| A bar is `BOUNDARY_INCOMPLETE` | It never reached the detector (the resampler dropped it), so no level, pool or interaction can arise from it |
| A bar is `MARKET_GAP` or `DEGRADED_UNKNOWN` | Fully usable. Levels, pools and sweeps compute normally. The quality is carried alongside, not applied as a filter |
| A period (day/week/session) contained no bars | **No level.** R2-04's absence-preserving rule, unchanged |
| A weekend gap opens through a level | A valid sweep (R2-04 §7, unchanged) |
| A DST transition shifts the 17:00-NY boundary | R2-04/R2-01 handle it; R2-09 adds no second timezone implementation. A guard test asserts the module defines no timezone and imports no `zoneinfo` |
| No dealing range yet | `zone = UNKNOWN`, `source_range_id = None`. **Not `INTERNAL`** |
| No levels at all on a side | `count = 0` (real), `nearest_* = None` (missing). Never `0` for both |

`LiquidityPicture` carries the anchoring bar's `BarCoverage` **by reference through R2-12**,
not by copy: R2-09 does not read the coverage module, because the coverage of a bar is not a
property of liquidity.

---

## 10. Leakage contract

### 10.1 The deliberately incorrect implementation (§15) — count future touches

R2-04's records are individually correct. R2-09 can still leak in **two** places, and the
bar frame added one of them:

```
CORRECT   touches := count over bars with close_time <= as_of, against levels
                     observable at as_of
LEAKING   touches := count over the WHOLE frame            <- counts the future

CORRECT   pools   := cluster( active_at(as_of) )           cluster AFTER the gate
LEAKING   pools   := filter( cluster(all levels), as_of )  cluster BEFORE the gate
```

A **third** prohibited shape, named because it is the one a batch pipeline reaches for
naturally:

```
PROHIBITED   build the complete six-month liquidity picture first,
             then query historical timestamps out of it

CORRECT      history available up to T
                     |
             R2-04 liquidity state at T
                     |
             R2-09 pool / significance state at T
```

**All three are built and all three must be proven to fail**, with the number of differing
instants reported. The first is the brief's named example for R2-09; the second is the pool-specific
form. Neither is visible in the output — a leaked touch count and a leaked pool both look
entirely ordinary.

### 10.2 The leakage matrix (master story §6.3 — authoritative L1–L8)

| # | R2-09 instantiation |
|---|---|
| **L1** | **No future bars.** Truncate or append bars after `as_of`; every `LiquidityPicture` field at `as_of` is identical |
| **L2** | **Future OHLC mutation.** Violently modify every bar after `as_of`; the picture at `as_of` is byte-identical |
| **L3** | **Wick dependency declared.** `approach_count`, `penetration_points` and `CONFIRMED_TAKEN` depend on **high/low wick** — legitimately. `distance_to_current_price_points` depends on **close**. Pool membership, pool price, `pool_cardinality`, `pool_density`, `span_points`, `structural_context` and `age_bars` depend on **confirmed events only**. Undeclared wick dependence is a defect |
| **L4** | **Point-in-time lifecycle.** A level swept *after* `as_of` is still `ACTIVE` at `as_of`; a level approached after `as_of` does not raise `approach_count` at `as_of`; pool membership reflects the gated set only |
| **L5** | **Prefix equivalence.** `picture(bars[:n], t_n) == picture(all, t_n)` for every `n`, on 1H and 4H |
| **L6** | **Identity stability.** A `pool_id` must not change because later data arrived — asserted **both** ways: no two distinct pools share an id (collision), and one pool's id is invariant across prefix and batch (stability) |
| **L7** | n/a — R2-09 consumes no external input. **Asserted by the import guard**, never a blank cell |
| **L8** | **Non-vacuous control.** Mutate a bar *before* `as_of` that a level or an approach derives from; the picture **must change**. Run against the §10.1 incorrect implementations, each of which must **fail** |

**Provenance integrity** is contracted in §7 and tested by marker substitution; it is not an
L-number under this contract and is not thereby optional.

### 10.3 The paired control (L8)

```
mutate bars strictly BEFORE as_of  -> picture MUST change
mutate bars strictly AFTER  as_of  -> picture MUST NOT change
```

Shipped as one test so neither half can exist without the other.

### 10.4 Confirming-bar wick mutation

Mutate the wick of the bar that confirms a level (the `pivot + right` bar for a swing, the
last bar of a day window for a PDH) and assert that the level does **not** become
observable earlier — paired with a control proving that mutating the field the definition
*does* read changes the outcome.

---

## 11. Streaming contract

| Regime | Required behaviour |
|---|---|
| Batch | `analyse(all)` then `picture_at(T)` |
| Prefix | `analyse(bars ≤ T)` then `picture_at(T)` |
| Bar-by-bar | Feed one bar at a time; picture after each bar's close |

**All three must be equal.** R2-09 claims **no** asymmetry and introduces none: it reads
only confirmed R2-04 and R2-06 records, and both are prefix-equivalent already. The True
Daily Open asymmetry does not reach this layer, because R2-09 never consumes the daily open.

If prefix equivalence fails during implementation, the cause is almost certainly clustering
before the gate (§10.1), not a streaming exception. **A new streaming exception may not be
introduced without an explicit, documented, tested justification.**

---

## 12. Identity contract

Tested collision stress cases:

| # | Case | Required outcome |
|---|---|---|
| 1 | Two levels created on the same bar (a session high and a PDH sharing an extreme bar) | Two distinct `level_id`s (R2-04 already guarantees); one pool if within tolerance |
| 2 | Two pools whose `price_level` is identical but whose membership differs | **Distinct `pool_id`s.** This is the test that proves identity is not price-based |
| 3 | Two pools with identical membership at different instants | **The same `pool_id`** — the pool is the same derived object |
| 4 | Three levels forming a chain A–B–C where `dist(A,C) > tolerance` | **One** pool of three (single-linkage chaining), documented, with `span_points > tolerance` recorded so the chaining is visible |
| 5 | Multiple sweeps on one bar | One `PoolInteraction` per affected pool; no merged event |
| 6 | An equal-highs run of three producing two overlapping R2-04 levels | Both are members of one pool; `cardinality = 2` from the two level ids, not `3` from the underlying swings. Documented so the count is not mistaken for a swing count |

---

## 13. Real-data validation

**EURUSD and XAUUSD, 1H / 4H / 1D only.** `assert_production_pair` is called first, so a
lower timeframe raises rather than silently producing rows.

Primary evidence: `production-native-2026-02_08` — six contiguous native months, already on disk. Named test cases:

| Case | Assertion |
|---|---|
| Normal weekday | Pools form; every member resolves; cardinality ≥ 2 |
| Weekend closure | No level from an empty period; a Sunday gap-open through a level is a valid sweep |
| Missing bars / provider gap | Picture computes; no fabricated level; `MARKET_GAP` bars are not excluded |
| Session breaks | Session-derived levels appear only after the session ends |
| Daily boundary | A PDH confirms at the 17:00-NY day end, never at its extreme bar |
| Dataset edges | Boundary-incomplete bars contribute nothing |
| 1D specifically | Pools form from PDH/PWH/swing levels; the 00:00-UTC vs 17:00-NY day discrepancy produces **two different prices for "the day's high"**, and the test asserts they are separate levels and may be pooled — never reconciled |
| Both symbols | The XAUUSD tolerance is ~160× EURUSD's in points and the pool counts are of the same order — evidence the per-pair scaling is doing its job |

March 2026 is inside that window, so the DST months come for free even though R2-09 has no
DST-sensitive logic of its own.

Measured facts the completion report must state: pool count per side per timeframe, median
cardinality, the fraction of levels in no pool, the internal/external/unknown split, and
the fraction of instants with no dealing range.

---

## 13a. Edge cases — every one explicitly addressed

No case is silently ignored. Each row is a named test.

| Case | Required behaviour |
|---|---|
| **Empty liquidity analysis** | Empty picture; every count `0`, every `nearest_*` `None`. Not an error |
| **Single liquidity level** | No pool (min cardinality 2). The level is still reported and still counted; `unpooled_*_ids` carries it |
| **Duplicate levels at identical prices** | Distinct `level_id`s (R2-04 guarantees), one pool, `span_points = 0.0`, `pool_density = cardinality` (maximum) |
| **Levels exactly on the tolerance boundary** | `price[i] − price[i−1] > tolerance` starts a new cluster, so **exactly at tolerance stays in the pool**. The `>` is stated once and tested from both sides |
| **Levels just outside tolerance** | Two separate pools (or a pool plus an unpooled level). Tested at `tolerance + 1 point` |
| **Multiple pools / overlapping candidate groups** | Single linkage **partitions** the side, so membership is exclusive. A chain A–B–C with `dist(A,C) > tolerance` is **one** pool of three, with `span_points > tolerance` recorded so the chaining is visible |
| **Expired liquidity** | **There is no expiry in this engine.** R2-04 states there is no mechanism by which a level becomes invalid without being swept. A level is `ACTIVE` until `SWEPT`. `age_bars` measures staleness; **it never removes a level**. Documented so "expired" is not silently invented |
| **Insufficient history** | Before the first confirmed level: empty picture. Before the first confirmed structural break: `structural_context = UNKNOWN` and `source_range_id = None` — **not `INTERNAL`** |
| **Missing dealing range** | As above — `UNKNOWN`, a third value, never collapsed |
| **Missing provenance** | **Impossible by construction, and asserted anyway.** A pool cannot be built from a level id that does not resolve; `assert_provenance_resolves` runs over every emitted pool, and a level whose id fails to resolve raises rather than being dropped |
| **1H → 4H withheld windows** | R2-08.2 withholds a 4H bar whose four native 1H bars are not all valid. R2-09 **operates only on the valid production 4H bars it is given** and **never fabricates a 4H candle**. A withheld window is simply absent from the frame; `age_bars` spans it, and no interpolation occurs |
| **Session gaps / provider gaps / missing bars** | No bar, no measurement. `approach_count` does not increase across a gap; the lifecycle is driven by bars, never by wall-clock elapsed time |
| **Zero-volume provider padding** | **Already removed upstream** by R2-08.2 at decode. R2-09 never sees it and must not re-detect or re-admit it |
| **Weekend boundaries** | A Friday level stays `ACTIVE` through the weekend; a Sunday gap-open through it **is** a valid sweep (R2-04 §7, unchanged) |
| **DST transitions** | R2-09 defines **no** timezone and imports no `zoneinfo`; DST reaches it only through R2-01/R2-04, each of which owns its tested conversion |
| **Boundary timestamps** | `as_of` exactly at a level's `confirmation_timestamp` ⇒ **observable**. One microsecond earlier ⇒ not |
| **Invalid / unknown level price** | A `NaN` or non-finite price **raises** at pool construction rather than clustering into a silently wrong pool. R2-04 does not produce one; the guard exists because a `NaN` sorts unpredictably and would corrupt the cluster walk |
| **Deterministic ordering** | Sort is `(price, level_id)` — total and stable, so pool ids are reproducible across runs. Asserted by building the same picture twice from shuffled input |
| **Serialisation with missing optional values** | `from_dict(as_dict()) == value` exactly, including every `None`; no field is dropped and no `None` becomes `0` |
| **`reaction_status = NOT_AVAILABLE`** | Round-trips as the enum value; never serialises as `null`, `0` or `false` |
| **Pool identity stability** | The same membership yields the same `pool_id` in batch and in prefix (**L6**) |

---

## 13b. Performance — measure first, do not optimise

Measured independently and reported, never optimised during R2-09 unless correctness
requires it:

| Stage | Measured |
|---|---|
| R2-04 input processing | time to obtain `LiquidityAnalysis` |
| Pool construction | clustering per instant |
| Touch / approach measurement | the bar-walk added by AD-1 |
| Significance calculation | seven components per pool |
| Serialisation | `as_dict` / `from_dict` per picture |
| **Total R2-09 execution** | end to end |

Reported figures: total runtime · **ms per bar** · number of liquidity levels · number of
pools · number of bars · **scaling behaviour** across 1H / 4H / 1D.

### Known inherited risk — stated before implementation

liquidity.md §14 limitation 1 records R2-04's sweep scan as **O(bars × active levels)**.
**R2-09's approach-counting adds a second traversal of the same shape**, so the combined
cost is plausibly two passes of `bars × active levels`.

This is **reported, not designed around**. Production bar counts are small — a six-month
month-set is 3 120 / 754 / 156 bars per symbol, two to three orders of magnitude below the
1M frames where this cost was first observed. **Do not redesign the architecture to improve
a benchmark.** If optimisation proves necessary, it is a separate, approved engineering
decision with its own before/after measurement.

---

## 14. Ambiguity register

| # | Ambiguity | Interpretations found | Chosen | Why | Kind |
|---|---|---|---|---|---|
| **A1** | The source material never gives a numeric pool tolerance | points · pips · % of price · ATR multiple · fraction of bar range | **fraction of measured median bar range, per (symbol, timeframe)** | The repository's own precedent (R2-08 §4). Evidence-backed. ATR is refused because no approved contract defines one and it would put a volatility model inside a representation layer | **Engineering** |
| **A2** | Whether equal highs require exact equality | exact · within tolerance | **Within tolerance — R2-04's existing answer, untouched** | Already decided, shipped and tested. Re-deciding it here would create two answers | **Settled upstream** |
| **A3** | Whether the nearest pool is the target | it is · it is not · unknown | **Unknown; distance exposed, target never claimed** | ICT says price seeks liquidity, not that it seeks the *nearest* liquidity | **Doctrine — refused** |
| **A4** | Minimum pool cardinality | 1 · 2 · 3 | **2**, configurable | A pool of one is a level. 3 is defensible but discards real two-level clusters with no stated reason | **Engineering** |
| **A5** | Whether a level may belong to several pools | yes · no | **No — single-linkage partitions the side** | Overlapping pools make `cardinality` ambiguous and pool identity unstable. The cost (chaining) is documented and measurable via `span_points` | **Engineering** |
| **A6** | What "significance" is | refuse it · a vector of measurable components · a single invented score | **A vector of FIVE measurable components, each exposed separately; the composite optional and off by default** | **DIRECTED BY THE BRIEF** (master **G11**), superseding an earlier refusal. The refusal's substance survives in *how* it is built: nothing is fitted, nothing filters on it, and every component is measurable from existing semantics plus point-in-time bars. Two candidates were **removed** for being invented — `type_rank` (§5a.3) and `reaction` (§5a.2, AD-2). Admitting or rejecting a *level* on significance remains refused | **Engineering — directed, concern preserved** |
| **A7** | Whether "taken" means wick or close | wick · close | **Wick is `SWEPT` (unchanged); close adds `CONFIRMED_TAKEN`** | Changing `SWEPT` changes a shipped feature's meaning. Both facts are already in R2-04's `closed_beyond`; R2-09 only names the derived one | **Doctrine + engineering** |
| **A8** | Whether a pool is an event or a view | immutable confirmed event · derived point-in-time view | **Derived view** | An event would need a lifecycle, and membership legitimately changes as levels confirm and get swept — which is mutation of a confirmed record, forbidden. See concept map candidate P3 | **Engineering** |
| **A9** | Whether internal/external should use the dealing range or a running extreme | R2-06 range · rolling max/min · session range | **R2-06 dealing range** | The only structurally meaningful, causal range the engine has. A rolling window has no structural meaning and drifts every bar (R2-06 concept map candidate A) | **Engineering** |
| **A10** | Whether pools should merge across sides | yes · no | **No.** Side is fixed at creation and never flips | R2-04 §5, unchanged: inferring side from current price destroys immutability | **Settled upstream** |

**No ICT rule is invented by this story.** Every doctrinal question above is either settled
upstream, refused, or deferred with a reason.

---

## 15. Files

### New

| File | Contents |
|---|---|
| `ict_kronos/ict/liquidity_model.py` | `LiquidityModelConfig`, `PoolZone`, `LiquidityInteraction`, `LiquidityPool`, `LiquidityPicture`, `LiquidityModel`, `LIQUIDITY_MODEL_VERSION` |
| `docs/ict/liquidity_model.md` | Implemented semantics (this document is the *specification*; that one records what was built) |
| `tests/test_liquidity_model.py` | Unit + identity |
| `tests/test_liquidity_model_leakage.py` | L1 … L8, the broken implementation, the L4 historical control |
| `tests/test_liquidity_model_real_data.py` | 1H/4H/1D, both symbols, `production-native-2026-02_08` |

*Naming note:* `liquidity_model.py` beside `liquidity.py` is deliberate — the module is the
Liquidity **Model** of this story, layered over the Liquidity **Detector** of R2-04.
`pools.py` was considered and rejected because the module owns more than pools
(internal/external, interactions, asymmetry).

### Modified

| File | Change |
|---|---|
| `ict_kronos/ict/__init__.py` | Export the new names |
| `ict_kronos/app/config.py` | `LiquidityModelConfig.from_env()`, wired into `Settings` |
| `.env.example` | The new `ICT_LIQUIDITY_MODEL_*` variables |
| `docs/ict/README.md`, `docs/ict/liquidity.md` §14 | Cross-reference; mark limitations 2 and 3 as addressed by R2-09 |
| `docs/dev/HANDOFF.md` | Status, gotchas, open items |
| `tasks/README.md` | R2-09 row |

### MUST NOT change

`ict_kronos/ict/liquidity.py` · `swings.py` · `sessions.py` · `structure.py` ·
`dealing_range.py` · `fvg.py` · `ifvg.py` · `order_blocks.py` · `breakers.py` · `bpr.py` ·
`rdrb.py` · `cisd.py` · `unicorn.py` · `true_daily_open.py` · `contract.py` ·
`composites.py` · `market_state.py` · `feature_vector.py` · anything under
`ict_kronos/features/` · anything under `ict_kronos/data/`.

A regression task asserts `git diff --stat` touches none of them.

---

## 16. Definition of done, and the hard stop

1. Every task in [R2-09-TASKS.md](../../tasks/Phase-2-ICT-Engine/R2-09-TASKS.md) ✅
2. `pytest -q` green — the existing suite plus the new ones, with **no test skipped
   silently**
3. `ruff check .` and `black --check .` clean
4. Real-data validation on `production-native-2026-02_08`, both symbols, 1H/4H/1D
5. Performance measured and reported (per-bar, batch, prefix)
6. Every ambiguity in §14 either resolved in code or restated as a limitation
7. R2-01 … R2-08 provably untouched
8. `docs/ict/liquidity_model.md` written; HANDOFF updated **in the same commit**
9. One local commit. **No push.**

```
=> R2-09 complete
=> audit
=> completion report
=> STOP
=> explicit approval required before R2-10 begins
```
