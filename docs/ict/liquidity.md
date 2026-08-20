# LiquidityDetector (R2-04)

Story: [R2-04](../../user-stories/Phase-2-ICT-Engine/R2-04-liquidity.md) · Code: [`ict_kronos/ict/liquidity.py`](../../ict_kronos/ict/liquidity.py)

---

## 0. The architectural distinction

> **A liquidity LEVEL is not a liquidity SWEEP.**

A **level** is an observable price reference — a place stops are presumed to rest. It has a creation, a confirmation, and a lifetime.

A **sweep** is a *later* price event that interacts with an already-observable level.

They are separate types with separate timestamps, and they are never collapsed. Merging them would make it impossible to ask the two questions that matter separately: *what liquidity exists right now?* and *what has just been taken?*

```
LiquidityLevel                        LiquiditySweep
  created_timestamp    ─┐               event_timestamp        (sweeping bar open)
  confirmation_timestamp├─ observable   confirmation_timestamp (sweeping bar close)
  status / swept_by     ┘               level_id ──────────────► the level it took
```

## 1. Level types

| Type | Side | Source |
|---|---|---|
| `SWING_HIGH` / `SWING_LOW` | buy / sell | R2-02 confirmed swings |
| `EQUAL_HIGHS` / `EQUAL_LOWS` | buy / sell | two confirmed swings within tolerance |
| `PREVIOUS_DAY_HIGH` / `PREVIOUS_DAY_LOW` | buy / sell | a completed trading day |
| `PREVIOUS_WEEK_HIGH` / `PREVIOUS_WEEK_LOW` | buy / sell | a completed trading week |
| `SESSION_HIGH` / `SESSION_LOW` | buy / sell | R2-01 completed sessions |

All use the existing shared `IctEvent` contract. **No second timestamp contract is introduced.**

## 2. Equal highs / equal lows

**Rule.** Two confirmed swing highs `A` (earlier) and `B` (later), separated by at most `equal_max_swing_distance` positions in the confirmed same-type swing sequence, are *equal* when

```
abs(B.price − A.price) <= equal_tolerance_points × point_value
```

Never floating-point `==`. The tolerance is configuration (default **1.0 point**, i.e. 0.00001 on EURUSD, 0.001 on XAUUSD).

- **Level price** = the **extreme** of the pair (`max` for highs, `min` for lows) — stops rest beyond the furthest touch, so that is the price a sweep must exceed.
- **`created_timestamp`** = the **later** swing's pivot time (the moment the pair existed).
- **`confirmation_timestamp`** = **`max(A.confirmation, B.confirmation)`**.

That last point is the one the story singles out, and it is load-bearing: *if the two swings have different confirmation timestamps, the level cannot become observable until the later required information is observable.* Ordinarily `B` confirms later, but across a market gap the ordering can invert (an earlier pivot's confirming bars may arrive after a later pivot's), so the `max` is taken explicitly rather than assumed.

**Runs of three or more.** A triple of equal highs produces **two** levels — `(S₁,S₂)` and `(S₂,S₃)` — each immutable. Merging them into one growing cluster would mutate an already-confirmed level, which the immutability rule forbids. Documented rather than silently deduplicated.

**Source swings are preserved** on every level (`source_swing_timestamps`), so R2-07 can trace a level back to the pivots that formed it.

## 3. Previous day / previous week

### What is a trading day?

**Default: 17:00 America/New_York to 17:00 America/New_York.** The FX/broker day, not the UTC calendar day.

This is not an arbitrary preference — it is what the validated Phase 1.5 data shows. After the same weekend closure, EURUSD's first bar was `2024-03-10 21:00 UTC` and XAUUSD's `22:00 UTC`. 17:00 New York is 21:00 UTC under EDT and 22:00 UTC under EST; the instruments reopened either side of the 2024-03-10 DST transition. A naive UTC calendar day would cut the trading day in the middle of the New York session and produce daily highs nobody trades off.

| Question | Answer |
|---|---|
| What defines the day? | A window from `day_boundary_local` to the same time next local day, in `day_timezone` |
| Timezone for the day | `America/New_York` (configurable) |
| Timezone for the week | The same — weeks are built from day windows, never re-derived |
| When does the level become observable? | At the **day window's end**, never earlier |
| Weekends | The Friday-anchored and Saturday-anchored windows contain no bars, so they produce **no** level. "Previous day" therefore means *the most recent completed day that actually had bars* |
| DST transitions | The boundary is **local**, so its UTC instant shifts automatically — exactly the R2-01 mechanism. A DST day is genuinely 23 or 25 hours long, and that is recorded |
| No market data | No bars in a window ⇒ no level. Absence is preserved, never fabricated |
| Incomplete days/weeks | **Ignored.** A window is only used once the observed data extends to or past its end |

### What is a trading week?

Weeks are **built from day windows**, so they inherit the same boundary and timezone.

- A day window anchored on local date `D` belongs to the week anchored on the **Sunday on or before `D`**.
- The trading week therefore runs Sunday 17:00 NY → Friday 17:00 NY (day windows anchored Sunday through Thursday).
- The week's high/low aggregates every day in it that had bars.
- The week **completes** at the end of its Thursday-anchored day window — i.e. Friday 17:00 NY.

### The observability rule — and why it needs leakage tests

> A day's high at 15:00 cannot be known as the day's *final* high at 15:00.

`confirmation_timestamp` for PDH/PDL is the **day window's end**, and for PWH/PWL the **week's end**. At the instant the extreme printed you could not know a later bar in the same period would not exceed it — identical reasoning to R2-01's session extremes, and the same code shape.

`created_timestamp` is the bar that printed the extreme, so the two remain distinguishable.

## 4. Session high / low

**R2-01 is reused directly.** `SessionDetector.detect()` supplies completed occurrences; no session boundary logic is reimplemented here.

A session's high/low becomes a liquidity level **only after the session completes**, carrying R2-01's `confirmation_timestamp` (the session window's end) unchanged.

**Running session state is deliberately excluded.** `SessionDetector.session_state_at()` still exists for point-in-time features, but an in-progress session high is *not* a completed liquidity level and is never emitted as one.

## 5. Buy-side / sell-side

**Fixed at creation, by type. It never changes.**

| Level | Side |
|---|---|
| swing high, equal highs, PDH, PWH, session high | **buy-side** (rests *above* — buy stops) |
| swing low, equal lows, PDL, PWL, session low | **sell-side** (rests *below* — sell stops) |

The story asks explicitly whether a level changes side as price moves through it. **It does not**, and that is a deliberate decision:

- Inferring the side from *current* price would make the same historical level flip sides as price oscillates, so its classification would depend on when you asked — destroying immutability and making any stored feature non-reproducible.
- The side describes **what kind of resting orders the level represents**, which is a property of how the level formed, not of where price happens to be.

What *does* change when price passes through is the **status** (`ACTIVE → SWEPT`), which is recorded with its own timestamp.

## 6. Lifecycle

```
CREATED ──► OBSERVABLE ──► [APPROACHED] ──► SWEPT
   │             │                            │
created_ts   confirmation_ts            swept_timestamp
                                     (== CONSUMED; see below)
```

**Three states carry the meaning.** `APPROACHED` is an optional refinement and is **off by default** (`approach_tolerance_points=None`) — deliberately not over-engineered.

**`PENDING` never appears in output, and that absence is a guarantee, not an oversight.** `analyse()` constructs a level only once the period/pair that defines it has completed, so every returned level has already confirmed. `PENDING` is the transient label a level carries between construction and admission during the bar walk. Pinned by `TestPendingIsNeverEmitted`.

| Status | Meaning |
|---|---|
| `PENDING` | Created but not yet observable — the period/pair has not completed |
| `ACTIVE` | Observable and unswept. This is the liquidity that currently exists |
| `APPROACHED` | *(optional)* Price came within `approach_tolerance_points` without exceeding. Never gates anything; the level stays fully usable |
| `SWEPT` | Price traded beyond the level. Terminal |

**Pending periods are exposed, never emitted as levels.** `LiquidityAnalysis.pending` lists periods still in progress (`PendingPeriod`: kind, label, window, running high/low, bar count). "The current day's high so far" is real information a live system has — but it is *not* a previous-day high, and conflating them is exactly the leak this module prevents.

**A future sweep can never make a level observable earlier.** `confirmation_timestamp` is fixed when the level is constructed, from the period/pair that defines it. Sweeps are computed afterwards and write only to sweep records and status — never back to a level. Levels are frozen dataclasses, so this is enforced by the type, not by discipline.

**`CONSUMED` and `INVALIDATED` are not separate states here, and that is stated rather than implied.** A sweep *is* the consumption — the resting orders are taken — so `SWEPT` is terminal and a swept level is removed from the active set. There is no mechanism by which a liquidity level becomes invalid *without* being swept: it either still rests there or it has been taken. If a later story needs an expiry (e.g. "a PDH older than N days is no longer relevant"), that is an age filter on `ACTIVE` levels, not a new lifecycle state.

## 7. Sweep semantics

**Rule.** For an observable, `ACTIVE` level:

| Side | Sweep condition |
|---|---|
| buy-side | `bar.high > level.price + sweep_tolerance` |
| sell-side | `bar.low < level.price − sweep_tolerance` |

- **`event_timestamp`** — the sweeping bar's **open**.
- **`confirmation_timestamp`** — the sweeping bar's **close**. Intrabar sequencing is unknowable from bar data, so even a wick sweep is only *knowable* once the bar ends. Identical honesty to R2-03.
- **The level must already be observable**: a sweep is only considered on bars with `close_time >= level.confirmation_timestamp`. A level cannot be swept before anyone could see it.

### The formal definition, and the alternatives rejected

> **A sweep is: price trading beyond an already-observable liquidity level by more than `sweep_tolerance_points`, on a bar that has closed.**

The four decisions the definition must make, each settled explicitly:

| Question | Our answer | Alternatives not taken |
|---|---|---|
| **Penetration** | The bar's **extreme** must exceed the level (a wick suffices) | *Close through* — that is a structural break (R2-03), not a sweep. *Touch* — too weak; touching a level takes no stops |
| **Minimum amount** | `sweep_tolerance_points`, default **0** (any strict exceedance) | A larger minimum would filter noise but silently discard genuine stop-runs; left as configuration |
| **Rejection required?** | **No.** Every penetration is a sweep; `closed_beyond` records what happened next | A `require_rejection` filter was considered and **removed** — see below |
| **Confirmation** | The sweeping bar's **close** | The bar's open or the intrabar moment — both unknowable from bar data |

**Why no `require_rejection` flag.** An earlier draft had one. It creates an incoherent state: a level penetrated but closing beyond would be *consumed* yet emit no event, leaving a swept level with nothing explaining it. Instead every sweep is emitted and carries:

- `closed_beyond=False` → swept and **rejected** — the textbook stop-hunt
- `closed_beyond=True` → swept and **broken through**

plus `is_rejection` as the convenience inverse. Filtering is a one-line downstream choice, and both variants stay available as ML features. **No time limit** applies to rejection: it is judged on the sweeping bar's own close, so it is knowable at confirmation rather than requiring an unbounded wait.

### Other sweep properties

- **Penetration depth** is recorded in points (`penetration_points`), so "barely tagged" and "blown through" are distinguishable.
### One sweep per level; one sweep event per level

**A consumed level can never generate another sweep.** `SWEPT` is terminal and the level leaves the active set. A later touch of the same price belongs to some *other* level (a newer session high, say), which has its own identity and its own sweep.

**When one bar sweeps several levels, each level gets its own sweep event.** This is the deliberate policy, chosen over "one sweep referencing many levels":

At any price there may be an equal high, a PDH, a PWH and a session high stacked within a point of each other. They are **not** collapsed — each keeps its identity, source type and timestamps. Downstream feature engineering must be able to distinguish *"PDH swept"* from *"session high swept"* even when the prices are identical, because those are different signals. A single multi-reference sweep event would force every consumer to unpack a list and would make "was the PDH swept?" a filtering problem rather than a lookup.

The cost is that one wick can emit several sweep events. That is correct: several distinct pools of liquidity were taken.

### Weekend and gap behaviour

- A level created on Friday is **not** swept during the weekend — no bars close, so no sweep can confirm. The lifecycle is driven entirely by bars, never by wall-clock elapsed time.
- **If the first Sunday/Monday bar trades through the level, that IS a valid sweep** under this definition: the bar's extreme exceeded the level and the bar closed. A gap-open through a level takes the resting orders exactly as a continuous move would.
- Bar *positions* are never used to infer elapsed time. Everything keys on `close_time`.

## 8. Timestamp semantics — summary

| Object | `created` / `event` | `confirmation` |
|---|---|---|
| Swing level | pivot bar open | R2-02 swing confirmation (`pivot + right` close) |
| Equal highs/lows | later pivot's open | **`max`** of both swings' confirmations |
| Session high/low | extreme bar's open | R2-01 session window end |
| PDH / PDL | extreme bar's open | **day window end** |
| PWH / PWL | extreme bar's open | **week end** (Friday 17:00 NY) |
| Sweep | sweeping bar's open | sweeping bar's close |

`confirmation < created` is impossible — the shared contract's constructor refuses it.

## 9. Edge cases

| Case | Behaviour |
|---|---|
| Period with no bars (weekend, holiday) | **No level.** Absence preserved |
| Period not yet complete in the data | **No level** — same rule as R2-01 sessions |
| Fewer than two same-type swings | No equal highs/lows |
| Equal levels beyond tolerance | Two separate swing levels, no equal-highs level |
| Run of three equal highs | Two overlapping levels, both immutable |
| Sweep exactly at the level | **Not** a sweep (strict comparison + tolerance) |
| The bar that creates a level | Cannot sweep it — its extreme *is* the level by construction |
| One bar sweeping several levels | One sweep event per level |
| A level already swept | Removed from the active set; never swept twice |
| Empty frame | Empty analysis |

## 10. Leakage rules

**The single gate.** `LiquidityLevel` and `LiquiditySweep` both implement `is_observable_at()`, which delegates to the contract's one `is_observable_at()` predicate; `filter_observable()` / `assert_observable()` accept them directly. The module hand-rolls **no** `confirmation_timestamp <= t` comparison, and a source-level test (`test_liquidity_module_hand_rolls_no_observability_comparison`) fails if one is ever reintroduced — five private copies of a rule are five places it can drift.

1. Swings enter only as **confirmed** R2-02 swings — never raw candidates.
2. Sessions enter only as **completed** R2-01 occurrences — never running state.
3. A period level confirms at the **period's end**, never at its extreme bar.
4. Equal-level confirmation is the **later** of the two source confirmations.
5. A sweep requires the level to be **already observable**.
6. A sweep confirms at its own bar's **close**.
7. Confirmed levels and sweeps are **immutable** — a later bar cannot revise one.

## 11. Configuration

```bash
export ICT_LIQUIDITY_EQUAL_TOLERANCE_POINTS=1.0
export ICT_LIQUIDITY_EQUAL_MAX_SWING_DISTANCE=1
export ICT_LIQUIDITY_SWEEP_TOLERANCE_POINTS=0.0
export ICT_LIQUIDITY_REQUIRE_REJECTION=0
export ICT_LIQUIDITY_APPROACH_TOLERANCE_POINTS=0.0
export ICT_LIQUIDITY_DAY_TIMEZONE=America/New_York
export ICT_LIQUIDITY_DAY_BOUNDARY_LOCAL=17:00
export ICT_LIQUIDITY_INCLUDE_SWING_LEVELS=1
```

**Alternatives deliberately not defaulted:** UTC-midnight day (`ICT_LIQUIDITY_DAY_TIMEZONE=UTC`, `DAY_BOUNDARY_LOCAL=00:00`) and New-York-midnight day (`America/New_York`, `00:00`). Both are in circulation; neither matches the observed instrument reopen times as well as 17:00 NY.

## 12. Test coverage

**210 tests** across three files (73 + 39 + 98).

| File | Tests | Covers |
|---|---|---|
| `tests/test_liquidity.py` | 73 | Config, all ten level types, equal-level tolerance and runs, side classification, sweeps (penetration/rejection/tolerance/terminality), multi-level policy, lifecycle, day/week calendar, events, boundaries |
| `tests/test_liquidity_leakage.py` | 39 | Confirmed-inputs-only (swings, sessions, days, weeks), a **naive-implementation proof** that removing the constraint leaks, sweep-after-observability, immutability, batch == prefix == bar-by-bar replay, gaps/weekends, HTF non-leakage, **the R2-03 wick-vs-break separation** |
| `tests/test_liquidity_real_data.py` | 98 | Real EURUSD + XAUUSD on 1M/5M/15M: every level type occurring, prices matching real bars, period confirmation timing, sweeps and their extremes, both rejections and break-throughs, multi-level sweeps, weekend behaviour, the DST day-boundary shift, session inheritance from R2-01, structure separation, the ML data model |

## 13. A real bug this design caught

The shared contract rejected a `previous_week_low` whose `confirmation_timestamp` (Friday 17:00 NY) *preceded* its `event_timestamp`. The cause: the **Friday-anchored day window** (Fri 17:00 → Sat 17:00) was being folded into the trading week whose close is Friday 17:00 — so a "week extreme" could post-date the week's own end.

Real FX data hides this completely, because there are no bars in that window. Synthetic 24/7 test data exposed it immediately. `_in_trading_week()` now excludes Friday- and Saturday-anchored windows, and the contract's `confirmation >= event` invariant is what surfaced it in the first place.

## 14. Known limitations

1. **Sweep scanning is O(bars × active levels).** Fine at Phase-1.5 scale; a multi-year 1M backfill with no expiry would want an interval tree or an age filter.
2. **No liquidity-pool clustering.** Nearby distinct levels (a PDH and a session high a point apart) stay separate objects rather than merging into one pool.
3. **No internal/external distinction.** Master Plan §6 lists it; it needs a structural range (R2-06's dealing range) to define "internal", so it is deferred.
4. **Equal-level runs produce overlapping pairs** (§2), by design, to preserve immutability.
5. **`APPROACHED` is recorded but never gates anything.** It is a feature for R2-07, not a filter here.
6. **Week boundaries assume a Sunday-open market.** The trading week is the Sunday…Thursday day windows; Friday- and Saturday-anchored windows are excluded (§13). Correct for FX and metals; an instrument with a different weekly calendar would need its own configuration.
7. **Swing levels dominate the level count.** With `left=right=3` on 5m data, most levels are swing highs/lows. `include_swing_levels=False` narrows to the period/session/equal set when that is what a model wants.
