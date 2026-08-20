# StructureDetector (R2-03)

Story: [R2-03](../../user-stories/Phase-2-ICT-Engine/R2-03-market-structure.md) · Code: [`ict_kronos/ict/structure.py`](../../ict_kronos/ict/structure.py)

---

## 0. The five distinctions this module preserves

Market-structure code usually collapses these. Keeping them apart is the whole design:

| # | Thing | When it happens | When it is knowable |
|---|---|---|---|
| 1 | **Swing occurrence** | the pivot bar | — |
| 2 | **Swing confirmation** | — | close of bar `pivot + right` (R2-02) |
| 3 | **Structure classification** (HH/HL/LH/LL) | the pivot bar | when the swing confirms |
| 4 | **Break occurrence** | the breaking bar | — |
| 5 | **Break confirmation** | — | close of the breaking bar |

The detector **never** consumes a raw swing. It consumes only swings already observable through the shared contract, and the tests prove that removing that filter changes the output.

## 1. Inputs — the hard dependency on R2-02

`StructureDetector` takes confirmed `SwingPoint`s from `SwingDetector`. At any bar *j*, the usable swing set is exactly

```
filter_observable(swings, bar_j.close_time)
```

**A swing that has not confirmed cannot classify, cannot become a reference level, and cannot be broken.** This is enforced by construction: the detector walks bars forward and admits a swing only when `swing.confirmation_timestamp <= bar.close_time`.

Consequence, and it is the point: structure inherits the swing confirmation lag. With `left=right=3` on 5m bars a pivot is knowable 20 minutes after it prints, so a BOS of that pivot cannot be reported before then either.

## 2. HH / HL / LH / LL

**Definition.** A confirmed swing is classified against the **previous confirmed swing of the same type**.

| Comparison | Label |
|---|---|
| swing high > previous swing high | `HIGHER_HIGH` |
| swing high < previous swing high | `LOWER_HIGH` |
| swing low > previous swing low | `HIGHER_LOW` |
| swing low < previous swing low | `LOWER_LOW` |
| equal (within `equal_level_tolerance_points`) | **no label** — see below |

1. **Required swing inputs** — the current confirmed swing and the previous confirmed swing of the same type. Nothing else.
2. **Required price break** — none. Classification is a comparison, not a break.
3. **Direction** — `BULLISH` for HH/HL, `BEARISH` for LH/LL.
4. **Candle close required?** — no, beyond the close that confirms the swing.
5. **`event_timestamp`** — the pivot bar's open time.
6. **`confirmation_timestamp`** — `max(current.confirmation, previous.confirmation)`. In practice the current swing's, since the previous one confirmed earlier; the `max` guards the gap case where ordering could invert.
7. **Reference** — the previous same-type swing's price.
8. **Invalidation** — none. A classification is a historical fact about two pivots.
9. **Immutable?** — **yes.** Both inputs are confirmed and immutable, so the label is too.
10. **Edge cases** — the very first swing of each type has no predecessor and is **unlabelled** (not an error). Equal levels produce no label.

**Equal highs/lows are deliberately unlabelled.** They are neither higher nor lower, and equal highs are a *liquidity* concept — R2-04 owns them. Inventing an `EQUAL_HIGH` structure label here would pre-empt that story and duplicate the concept. The tolerance is configurable so "equal" is not a floating-point accident.

**Classification never uses future swings.** The label for swing *S* depends only on *S* and the swing before it. A later swing cannot change it.

## 3. Reference levels — which level is "protected"

The detector tracks two references:

- `active_high` — the **most recent** confirmed swing high, by event time
- `active_low` — the **most recent** confirmed swing low, by event time

A bullish break is a break of `active_high`; a bearish break, of `active_low`. After a break the reference is **consumed** (`None`) until a newer swing of that type confirms — so one level cannot be broken twice.

### Ordering decision: swings are absorbed *before* the break check

At bar *j*'s close two things can become known simultaneously: the bar's price, and a swing whose confirming bar is *j*. The detector absorbs the swing **first**, then evaluates the break.

This matters. Suppose `active_high` is H1 at 1.0500, and at bar *j* a new swing high H2 at 1.1000 confirms while bar *j* closes at 1.0600. Absorbing first makes H2 the reference, and 1.0600 does not break it — no BOS. Evaluating the break first would report a "break of 1.0500", but price had **already** traded to 1.1000 at H2's pivot bar, which is earlier. Reporting a break of a level price had long since exceeded would be wrong.

**Consequence to be aware of:** in `CLOSE` mode, a level exceeded only by a wick before a higher pivot forms may never produce a BOS. That is correct under this reading, and it is why the mode is configurable.

**Alternative not adopted:** "break of the *highest unbroken* swing high" rather than the most recent. That survives sideways structure better but ignores recent lower highs entirely. Configurable extension, not the default.

## 4. BOS — Break of Structure

**Definition.** A confirmed break of the active reference level **in the direction of the prevailing structural state**. A continuation.

1. **Required swing inputs** — the active reference swing, which **must already be confirmed and observable**. Non-negotiable.
2. **Required price break** — per `break_mode`:
   - **`CLOSE` (default)** — the bar's **close** must exceed the level: `close > level + tolerance` (bullish), `close < level − tolerance` (bearish).
   - **`WICK`** — the bar's **high/low** suffices.
3. **Direction** — `BULLISH` breaking a high, `BEARISH` breaking a low.
4. **Candle close required?** — **yes, in both modes.** With bar data intrabar timing is unknowable, so even a wick break is only *knowable* at the bar's close. This is an honest limitation of the data, not a modelling choice.
5. **`event_timestamp`** — the breaking bar's **open** time (when the structural condition occurred).
6. **`confirmation_timestamp`** — the breaking bar's **close** time (when it became knowable).
7. **Reference** — the broken swing: its price, its pivot timestamp, and its own confirmation timestamp, all carried on the event.
8. **Invalidation** — none. A break is a historical fact. Price returning below the level later is a *new* structural event, not a retraction.
9. **Immutable?** — **yes.** Every input (the confirmed swing, the closed bar) is final at confirmation time.
10. **Edge cases** — equality is **not** a break (strict comparison plus tolerance). A gap that opens beyond the level still breaks it on that bar's close. From the `UNDEFINED` state the first break is labelled **BOS** and establishes the trend — there is no prior character to change.

**`CLOSE` is the default** for two reasons. First, a wick break fires on every stop-run and liquidity sweep, which R2-04 will model as *sweeps* rather than structural breaks — defaulting to `WICK` would conflate two distinct concepts.

Second, and less obvious: **in `WICK` mode the bar that prints a higher swing high necessarily breaks the previous swing high**, because its high exceeds it by construction. So `WICK` emits a BOS at nearly every HH formation, collapsing the distinction between *forming* a pivot and *breaking* one. Pinned by `test_wick_mode_also_breaks_on_the_bar_that_forms_a_higher_swing_high`.

## 5. MSS / CHoCH — and an explicit statement about their equivalence

**Definition.** A confirmed break of the active reference level **against** the prevailing structural state. A potential reversal.

The detection is *identical* to BOS. **The only difference between BOS and MSS is the prior state.** Same break, different label:

| Prior state | Break direction | Label | Resulting state |
|---|---|---|---|
| `UNDEFINED` | bullish | `BOS` | `BULLISH` |
| `UNDEFINED` | bearish | `BOS` | `BEARISH` |
| `BULLISH` | bullish | `BOS` (continuation) | `BULLISH` |
| `BULLISH` | bearish | **`MSS`** (reversal) | `BEARISH` |
| `BEARISH` | bearish | `BOS` (continuation) | `BEARISH` |
| `BEARISH` | bullish | **`MSS`** (reversal) | `BULLISH` |

1. **Prior structural state** — `BULLISH` or `BEARISH` (never `UNDEFINED`; that yields BOS).
2. **Required opposite-side break** — bearish break of `active_low` while `BULLISH`, or bullish break of `active_high` while `BEARISH`.
3. **Required confirmation** — the breaking bar's close, exactly as for BOS.
4. **Resulting state** — flipped.
5. **Immutable?** — yes, on the same grounds as BOS.

### CHoCH: stated plainly

**In the default configuration, MSS and CHoCH are the same event, and we emit only `MSS`.** CHoCH is documented as a widely-used synonym. We do not pretend they are different algorithms.

The `ChochPolicy` setting makes this explicit rather than implicit:

| Policy | Behaviour |
|---|---|
| **`SYNONYM`** *(default)* | Counter-trend breaks emit `MSS`. `CHOCH` is never emitted. CHoCH ≡ MSS. |
| `DISTINCT_BY_DISPLACEMENT` | A counter-trend break emits `MSS` **if the breaking bar shows displacement**, else `CHOCH`. |

Under `DISTINCT_BY_DISPLACEMENT`, displacement is defined deterministically:

```
displacement_ratio = breaking_bar_range / mean(range of previous N bars)
MSS   if displacement_ratio >= displacement_factor
CHoCH otherwise
```

`N` (`displacement_lookback`, default 20) and `displacement_factor` (default 1.5) are configuration. The mean uses only bars **strictly before** the breaking bar, so it introduces no look-ahead. `displacement_ratio` is recorded on **every** break event regardless of policy, so a downstream model can use it as a feature without changing the labelling.

**When displacement cannot be computed** — fewer than `N` prior bars — the ratio is `None` and the break is labelled **`CHoCH`**. The conservative choice: without evidence of displacement we do not claim the stronger label.

This is the one reading in circulation that is both deterministic and defensible — many sources require displacement for a "true" MSS. It is **off by default** because the base state-transition definition is the more common usage and the simpler claim.

### CHoCH after R2-05.2 — reviewed, and deliberately unchanged

The R2-05.2 brief required CHoCH to be re-evaluated rather than preserved by inertia.
It was, and the outcome is **no behaviour change**. The reasoning, so it is not
re-litigated:

**The ICT material does not define CHoCH as a distinct algorithm.** What it does define
is the CISD/MSS relationship — *"CISD is a candle-close signal that prints early; MSS
is a structural break that confirms later"*, and *"MSS is based on wicks while CISD on
closing price"*.

That matters because the role usually invoked to justify splitting CHoCH from MSS —
"the earlier, weaker reversal hint" — is, in the source material, **CISD's role**. With
`CisdDetector` now implemented (`docs/ict/cisd.md`), that role is filled by a concept
that has a real, testable definition. There is no remaining pressure to manufacture a
distinct CHoCH, and manufacturing one because the terminology is common in
discretionary material would be exactly the kind of invented semantics this repo
refuses.

The four concepts, side by side:

| Concept | Reads | Confirms on | Owner |
|---|---|---|---|
| **BOS** | swing levels | the breaking bar's close | R2-03 |
| **MSS** | swing levels, against the prevailing state | the breaking bar's close | R2-03 |
| **CHoCH** | — *(alias of MSS by default)* | — | R2-03 |
| **CISD** | candle **opens and closes** only | the crossing bar's close | R2-05.2 |

So: `EventType.CHOCH` remains in the contract and remains emittable under
`DISTINCT_BY_DISPLACEMENT`, the architecture can represent it independently, and the
default stays `SYNONYM`. **No R2-03 behaviour changed in R2-05.2** — the existing
BOS/MSS tests pass unmodified.

## 6. State machine

```
                   bullish break                bearish break
   UNDEFINED ──────────────────► BULLISH ◄───────────────────── UNDEFINED
        │                          │  ▲                              │
        │ bearish break            │  │ bullish break (MSS)          │
        ▼                    (MSS) │  │                              ▼
     BEARISH ◄────────────────────-┘  └────────────────────────► BEARISH
        │  bearish break (BOS) ─► stays BEARISH
        └─ bullish  break (BOS when BULLISH) ─► stays BULLISH
```

- Start: `UNDEFINED`.
- Every confirmed break sets the state to its own direction.
- `previous_state` and `resulting_state` are recorded on every break event, so R2-07 can use the transition itself as a feature.

## 7. Timestamp semantics — summary

| Event | `event_timestamp` | `confirmation_timestamp` |
|---|---|---|
| HH / HL / LH / LL | the pivot bar's open | `max(swing.confirmation, previous_swing.confirmation)` |
| BOS / MSS / CHoCH | the **breaking bar's open** | the **breaking bar's close** |

`confirmation_timestamp < event_timestamp` is impossible — the shared contract's constructor refuses it.

**Nothing is ever exposed before confirmation.** `StructureDetector.observable_at(frame, as_of)` is proven equal to running the detector over only the bars visible at `as_of`.

## 8. Worked example

5-minute bars, `left=right=2`, `break_mode=CLOSE`.

```
bar  time    high     low      close
 0   09:00   1.1000   1.0990   1.0995
 1   09:05   1.1010   1.0995   1.1005
 2   09:10   1.1050   1.1000   1.1040     ← swing-high pivot (1.1050)
 3   09:15   1.1030   1.1010   1.1020
 4   09:20   1.1020   1.1000   1.1010     ← pivot confirms at 09:25
 5   09:25   1.1015   1.0980   1.0985
 6   09:30   1.1000   1.0970   1.0975     ← swing-low pivot (1.0970)
 7   09:35   1.1010   1.0990   1.1005
 8   09:40   1.1030   1.1000   1.1025     ← low pivot confirms at 09:45
 9   09:45   1.1070   1.1040   1.1065     ← close 1.1065 > 1.1050  ⇒ BOS bullish
```

The swing high **occurs** at 09:10 and **confirms** at 09:25 (close of bar 4). The break **occurs** at bar 9 (`event_timestamp` 09:45) and **confirms** at 09:50 (its close). State: `UNDEFINED → BULLISH`.

Had bar 9 merely wicked to 1.1070 and closed at 1.1045, `CLOSE` mode would report **no** break — while `WICK` mode would.

## 9. Edge cases

| Case | Behaviour |
|---|---|
| Fewer swings than needed | No labels, no breaks. Too early, not an error |
| First swing of a type | Unlabelled — no predecessor to compare against |
| Equal swing levels | No HH/HL/LH/LL label; still becomes the active reference |
| Break exactly at the level | **Not** a break (strict comparison + tolerance) |
| Same level broken twice | Impossible — the reference is consumed on break |
| Reference superseded before a break | The newer swing becomes the reference (§3) |
| Break from `UNDEFINED` | Labelled `BOS`; establishes the trend |
| Pending candidate | A break whose bar has not closed is **not emitted** |
| Market gap / weekend | The reference swing must confirm first; if its confirming bars fall after the weekend, no break can be reported during the weekend |
| Empty frame / no swings | Empty analysis |

## 10. Leakage rules

1. Swings enter **only** via `filter_observable(swings, bar.close_time)`.
2. A break requires its reference swing to be **already confirmed**, never merely present.
3. Classification uses the previous swing only — never a later one.
4. A break confirms at its own bar's close, never its open.
5. Confirmed events are **immutable**: a later bar cannot revise one.
6. Cross-timeframe assembly is R2-07's job via `align_htf_context()`; this module never joins timeframes.

## 11. Significance filter

R2-02 emits many minor pivots. Rather than a ranking model, one deterministic knob:

```
min_swing_strength_points: float = 0.0     # 0 = keep every swing
```

A swing whose R2-02 prominence (in instrument points) is below the threshold is **excluded from structure entirely** — it cannot classify, become a reference, or be broken. Simple, deterministic, configurable, and easy to sweep as a hyperparameter in Phase 4. Anything more elaborate is deferred.

## 12. Configuration

```bash
export ICT_STRUCTURE_BREAK_MODE=close          # close | wick
export ICT_STRUCTURE_BREAK_TOLERANCE_POINTS=0
export ICT_STRUCTURE_EQUAL_TOLERANCE_POINTS=0
export ICT_STRUCTURE_MIN_SWING_STRENGTH=0
export ICT_STRUCTURE_CHOCH_POLICY=synonym      # synonym | distinct_by_displacement
export ICT_STRUCTURE_DISPLACEMENT_LOOKBACK=20
export ICT_STRUCTURE_DISPLACEMENT_FACTOR=1.5
```

Defaults: `CLOSE` break mode, zero tolerances, no significance filter, CHoCH ≡ MSS.

## 13. What R2-07 gets

Every break event carries enough to be a feature without re-deriving anything:

| Field | Meaning |
|---|---|
| `event_type` | `bos` / `mss` / `choch` |
| `direction` | bullish / bearish |
| `event_timestamp` | when the break occurred |
| `confirmation_timestamp` | when it became knowable |
| `price_level` | the breaking bar's close (or extreme, in `WICK` mode) |
| `reference_level` | the level that was broken |
| `previous_state` / `resulting_state` | the structural transition |
| `reference_swing_timestamp` | which swing was referenced |
| `reference_swing_confirmation` | when that swing became knowable |
| `break_distance_points` | how far beyond the level price closed |
| `displacement_ratio` | breaking bar range ÷ mean of previous N |
| `strength` | `break_distance_points` (documented magnitude, not a score) |

## 14. Test coverage

**169 tests** across three files (56 + 29 + 84, plus 2 data-dependent skips).

| File | Tests | Covers |
|---|---|---|
| `tests/test_structure.py` | 56 | Config validation, HH/HL/LH/LL, equal & repeated levels, BOS both directions, break modes, MSS both directions, CHoCH policies, state machine, boundaries, significance filter, event contract |
| `tests/test_structure_leakage.py` | 29 | The R2-02 observability dependency (including a direct proof that removing the filter changes the result), contract-level leakage, immutability, batch == streaming (prefix + candle-by-candle), `observable_at`, market gaps, HTF non-leakage |
| `tests/test_structure_real_data.py` | 84 (+2 skips) | Real EURUSD + XAUUSD across 1M/5M/15M: detection, all four labels, both break directions, BOS *and* MSS occurring, referenced levels being real swing prices, coherent transitions, configuration effects, leakage, prefix replay, `observable_at`, the weekend closure, sessions and the US DST transition |

Story-mandated cases: HH ✅ HL ✅ LH ✅ LL ✅ · BOS bullish ✅ bearish ✅ · MSS bullish ✅ bearish ✅ · CHoCH ✅ · wick vs close ✅ · equal levels ✅ · repeated levels ✅ · insufficient history ✅ · pending structure ✅ · weekend gaps ✅ · DST/session boundaries ✅ · confirmation timestamps ✅ · leakage ✅ · streaming replay ✅ · real data ✅

The two skips are honest data dependencies: no break in this four-day window references a pre-weekend swing, for either instrument.

## 15. Known limitations

1. **Most-recent-swing reference** (§3), not highest-unbroken. Documented alternative, not implemented.
2. **No internal vs external structure.** Only swing (external) structure exists. Internal structure needs a second, finer swing configuration — deferred.
3. **`WICK` mode still confirms at bar close.** Intrabar sequencing is unknowable from bar data, so wick mode does not reduce the confirmation lag; it only loosens the trigger.
4. **No displacement-based BOS filter.** Displacement is computed and exposed on every break, and gates MSS/CHoCH only under the non-default policy.
5. **Structure is per-timeframe.** Running on 1m and 15m gives two independent state machines; reconciling them is R2-07's job.
