# R2-11 — Multi-Timeframe Context — STORY

**Specification. Written before `ict_kronos/ict/mtf.py` exists.**
Master story: [Phase-2-Market-Intelligence-STORY.md](../Phase-2-Market-Intelligence-STORY.md)
· Concept map: [R2-11-CONCEPT-MAP.md](R2-11-CONCEPT-MAP.md)
· Tasks: [R2-11-TASKS.md](../../tasks/Phase-2-ICT-Engine/R2-11-TASKS.md)

> **SPECIFICATION ONLY — implementation NOT started, NOT approved.**
>
> **Production timeframes: 1H / 4H / 1D only.** No dependency below 1H or above 1D, direct
> or indirect. `assert_production_pair` is called first on every production path and
> **raises** rather than converting.
>
> **HARD STOP at the end. R2-12 does not begin without explicit approval.**
>
> **Data prerequisite: SATISFIED.** `production-native-2026-02_08` is already on disk and
> contains March 2026 with both the US and EU DST transitions (§9.2). No backfill needed.

---

## 1. The question this layer answers

> **At 1H timestamp `T`, what 4H and 1D information was actually known at `T`?**
> **At 4H timestamp `T`, what 1D information was actually known at `T`?**

This is the highest-leakage-risk story in the phase, and the risk is not subtle in
mechanism — it is subtle in *appearance*. The resampler's module docstring already names
it:

> *"A 4H bar timestamped 08:00 is not knowable until 12:00. Joining it onto a 5M
> observation at 09:15 — which every naive `merge` or `reindex(method='ffill')` on the
> open timestamp will happily do — leaks four hours of future information into the feature
> vector. The model looks brilliant in backtest and is worthless."*

---

## 2. What already exists

| Component | Status | R2-11 disposition |
|---|---|---|
| `align_htf_context()` | ✅ **the only sanctioned MTF join.** Joins on `close_time`, never `timestamp`; `merge_asof(direction="backward", allow_exact_matches=True)` | **reuse** for bar-level context |
| `latest_closed_bar(frame, as_of)` | ✅ point-in-time bar selection, `close_time <= as_of` | **reuse** for instant-level selection |
| `with_close_time()` | ✅ attaches the observability anchor | **reuse** |
| `build_timeframe_stack()` | ✅ builds every TF from one base frame | **NOT used** — it calls `resample()`. R2-11 reads persisted **production** partitions, whose 1H and 1D are provider-native (master story §4.1–4.2) |
| `Timeframe.is_higher_than()` | ✅ ordering | **reuse** |
| `ICTEngineView.state_at(as_of)` | ✅ the full per-timeframe point-in-time state | **reuse — this is the core of the design** |
| `test_leakage.py::test_naive_join_on_open_timestamp_would_leak` | ✅ demonstrates the wrong answer beside the right one | **extend** to the R2-11 shape |
| Multi-timeframe **state** assembly | ❌ *"the largest known gap"* — HANDOFF open item 2 | **NEW — R2-11** |
| HTF ICT structure, liquidity, premium/discount on an LTF row | ❌ | **NEW — R2-11** |
| Timeframe-locality guard tests in R2-07 | ✅ `R2-07-16` bans HTF joins in `market_state.py` | **must be relaxed *precisely***, §10.4 |

**R2-07 was deliberately timeframe-local** ([market_state.md](../ict/market_state.md) §10):
the execution brief directed it, the divergence from the story text was recorded rather
than silently resolved, and the closing note is the licence this story operates under —
*"nothing in this layer needs restructuring to accept it."*

---

## 3. The design in one paragraph

> **An HTF context is not a set of copied columns. It is an `ICTMarketState`, built by
> R2-07 from HTF bars, at an HTF bar close that is `<= T`.**

That single decision is what makes R2-11 small, and it is the whole reason this story
carries almost no new ICT logic:

```
for base instant T:
    htf_close   := latest close_time in the HTF frame with close_time <= T
    htf_state   := htf_engine_view.state_at(htf_close)       # R2-07, unchanged
    htf_pools   := liquidity_model.picture_at(htf_close)     # R2-09, unchanged
    MtfContext  := a flat, id-carrying projection of those two
```

Every observability decision inside `htf_state` was already made, correctly, by R2-07 —
against `as_of = htf_close`, which is itself `<= T`. **The alignment is therefore
conservative by construction**: it can only ever show *less* than a live observer at `T`
would have, never more.

The alternative — re-deriving HTF structure inside R2-11 — is rejected in
[the concept map](R2-11-CONCEPT-MAP.md) §3 candidate **M4**. It would duplicate ten
detectors and create a second definition of every ICT concept.

**Consequence for §3's dependency question:** because an `MtfContext` projects an
`ICTMarketState` *and* a `LiquidityPicture`, R2-11 hard-depends on R2-09. Dropping HTF pool
proximity from scope would demote that to optional; it is kept in scope, because "where is
the 4H liquidity relative to price" is the single most-cited reason for wanting HTF context
at all.

---

## 4. HTF alignment — the definitions

### 4.1 The core rule

> At base instant `T`, the **usable** HTF bar is the last HTF bar whose `close_time <= T`.
> A bar closing exactly at `T` **is** usable — its final price is known at that instant.

Identical to `latest_closed_bar` and to `is_observable_at`. No new predicate.

### 4.2 Terms, defined once

| Term | Definition |
|---|---|
| **Completed HTF bar** | An HTF bar with `close_time <= T`. The only kind R2-11 reads |
| **Forming HTF bar** | An HTF bar with `timestamp <= T < close_time`. **Never read.** §4.4 |
| **Observable HTF event** | An `IctEvent`/record from the HTF analysis with `confirmation_timestamp <= aligned_htf_close` |
| **Alignment timestamp** | `aligned_htf_close` — the `close_time` of the usable HTF bar. Carried on every context |
| **Alignment lag** | `T − aligned_htf_close`, in minutes. `0` when the HTF bar closes exactly at `T` |
| **Staleness (bars)** | Base bars strictly between `aligned_htf_close` and `T`. `0` at an exact boundary |
| **Timezone** | **UTC throughout.** R2-11 defines no timezone and imports no `zoneinfo`. A guard test asserts it |

### 4.3 Why `close_time` and never `timestamp`

`timestamp` is the bar's **open**. Joining on it makes a 4H bar visible four hours before
it exists. This is the repository's single most-guarded rule and R2-11 inherits the guard
verbatim: a source-level test asserts `mtf.py` contains no `merge` or `merge_asof` on
`timestamp`, and the L4 naive-divergence test builds the wrong join and proves it disagrees.

### 4.4 Forming HTF bars — excluded in v1

Master story **G6**. The brief's default favours completed information *"unless the
repository already has an explicit contract for forming-bar information."*

It does have such contracts — `RunningSessionState` (R2-01) and `PendingPeriod` (R2-04) —
but **neither has ever been fed into `ICTMarketState`**, and R2-04's documentation is
explicit about why: *"'the current day's high so far' is real information a live system has
— but it is not a previous-day high, and conflating them is exactly the leak this module
prevents."*

Exposing a forming 4H bar's running high on a 1H row is the same conflation one level up.

**Decision: excluded from v1**, recorded as the leading v2 candidate. If it is ever added,
it must arrive as *separately named* fields (`h4_forming_running_high`, …) that no existing
field's meaning depends on — never by making `h4_high` mean something different.

### 4.5 Boundary behaviour

| Case | Behaviour |
|---|---|
| `T` exactly equals an HTF `close_time` | That bar **is** the aligned bar. `alignment_lag_minutes = 0`, `staleness_bars = 0` |
| `T` one microsecond earlier | The **previous** HTF bar is aligned |
| `T` before every HTF close | `available = False`, reason `NO_CLOSED_HTF_BAR`. **All fields `None`, nothing backfilled.** `align_htf_context` produces NaN here; R2-11 converts NaN → `None` at the boundary (the R2-07 audit's NaN-sentinel lesson) |
| A 1H bar at 13:00–14:00 and a 4H bar closing at 12:00 | Aligned to the 12:00-close bar; staleness 1 base bar |
| 1D on a 1H row | Aligned to the last 00:00-UTC daily close ≤ `T`, so a 1H bar at 09:00 uses **yesterday's** daily bar. Correct, and it is why `d1_staleness_bars` matters |
| Base timeframe == HTF | **Refused.** `MtfSpecError`. A timeframe is not its own higher timeframe |
| Base timeframe higher than HTF | **Refused.** R2-11 never projects downward |

### 4.6 DST

R2-11 does **no** timezone conversion — every timestamp is UTC and every comparison is UTC.
DST reaches this layer only *through* the detectors it consumes (R2-01 sessions, R2-04's
17:00-NY day, R2-05.1's 00:00-NY daily open), each of which owns its own, already-tested
conversion.

What R2-11 must prove is that **the alignment itself is unaffected** by a DST transition:
a UTC-anchored 4H grid does not shift, so an alignment that is correct in January is
correct in July. That sounds trivial and is exactly the kind of thing that is quietly wrong
when someone converts to local time "to be safe". March 2026 inside `production-native-2026-02_08` is the evidence.

### 4.7 Weekend, missing HTF bars and provider gaps

| Case | Behaviour |
|---|---|
| Weekend: no HTF bar closes | The last Friday HTF bar remains aligned across the whole weekend. **This is correct point-in-time behaviour, not forward-filling a price** — the last known 4H bar genuinely is the last known 4H bar. `staleness_bars` grows and makes it visible |
| A missing HTF bar (provider gap, **or a withheld 4H window** — §9.3) | The previous existing HTF bar is aligned. Same reasoning; staleness grows. **No bar is invented** |
| A missing *base* bar | No row exists for it, so nothing to align |
| An HTF bar was dropped as `BOUNDARY_INCOMPLETE` | It never entered the frame, so it can never be aligned. Correct — its high/low were meaningless |
| A whole HTF timeframe absent from disk | `available = False`, reason `HTF_SERIES_MISSING`, logged as a **warning**. Never silently `None` |

**Staleness is the honest representation of every gap in this table**, which is why it is
mandatory on every context and is projected as a feature (§7).

---

## 5. The context hierarchy

### 5.1 The hierarchy, and why it is what it is

```
1D  ->  no higher timeframe.  MTF fields are None.  (COT is NOT an MTF layer -- §5.3)
4H  ->  1D
1H  ->  4H, 1D
```

Not hard-coded by fiat. It is derived from two facts already fixed in the repository:

1. **`PRODUCTION_TIMEFRAMES = (H1, H4, D1)`** — a module constant, not configuration
   (`production.py`). There is no fourth production timeframe to include.
2. **`Timeframe.is_higher_than()`** gives the ordering, and *"higher"* means strictly more
   minutes.

So the hierarchy is *"every production timeframe strictly higher than the base"*, computed
rather than listed. A test asserts the computed hierarchy equals the table above, so if
`PRODUCTION_TIMEFRAMES` ever changes, the hierarchy follows and the test says so.

**Research timeframes are refused, not filtered.** `assert_production_pair` is called on
the base pair first. A 5m base asking for 1H context raises.

### 5.2 What each level exposes

| Base | Local (R2-07 + R2-09, already present) | HTF context added by R2-11 |
|---|---|---|
| **1H** | structure · liquidity · pools · premium/discount · imbalance · institutional · composites · daily open · session | **4H** structure, pools, premium/discount, bias, staleness · **1D** same |
| **4H** | same | **1D** structure, pools, premium/discount, bias, staleness |
| **1D** | same | *none* — `available = False`, reason `NO_HIGHER_TIMEFRAME` |

`NO_HIGHER_TIMEFRAME` is a **distinct reason code** from `NO_CLOSED_HTF_BAR` and from
`HTF_SERIES_MISSING`. "There is no such thing", "it does not exist yet" and "the data is
missing" are three different facts (master story §5.2) and collapsing them would make a 1D
row indistinguishable from a broken 1H row.

### 5.3 COT is not a timeframe

The brief's hierarchy sketch places "COT context" under 1D. **R2-11 does not touch COT.**

COT aligns by *release timestamp*, not by *bar close*. It is not higher-timeframe market
structure; it is a different dataset on a different clock. Routing it through the MTF layer
would mean one alignment mechanism pretending to be two, and the first person to add a
second non-bar dataset would have to break it apart again. R2-12 composes the two layers
side by side, which is what a composition layer is for.

### 5.4 Which fields are projected

Deliberately a **narrow, curated subset** of the HTF state — not the whole thing.

Copying all 56 local features per HTF would give 112 extra columns, most of which are
counts whose HTF meaning is ambiguous (what does *"4H `bars_since_cisd`"* mean on a 1H row
— 4H bars or 1H bars?). Ambiguity **C5**.

Projected per HTF, and nothing else:

| Group | Fields |
|---|---|
| Availability | `available`, `unavailable_reason`, `aligned_htf_close`, `alignment_lag_minutes`, `staleness_bars`, `htf_bar_close_time` |
| Structure | `structure_state`, `structure_direction`, `latest_break_id`, `latest_break_type`, `latest_break_direction`, `bars_since_break` **(in HTF bars, named so)** |
| Premium/discount | `range_id`, `zone`, `percentage_position` *(unclamped)*, `distance_from_equilibrium_points`, `width_points` |
| Liquidity (R2-09) | `nearest_buy_side_pool_id`, `nearest_buy_side_points`, `nearest_sell_side_pool_id`, `nearest_sell_side_points`, `buy_side_pool_count`, `sell_side_pool_count`, `liquidity_asymmetry` |
| Bias | `bias` — R2-07's existing four-source count, computed on the HTF state. **Not a new rule** |
| Bar | `htf_open`, `htf_high`, `htf_low`, `htf_close` — the aligned bar itself |
| Provenance | `source_state_version`, every id above |

**`bars_since_break` is renamed to `bars_since_break_htf_bars`** in the projection so the
unit is in the name. HANDOFF/R2-07 established that `bars_since_*` counts bars, not elapsed
time; across timeframes the *which bars* becomes load-bearing.

### 5.5 Distances are re-measured against the base close

`nearest_buy_side_points` inside a 4H context is measured from **the base bar's close at
`T`**, not from the 4H bar's close. The 4H bar closed hours ago and its close is stale;
"how far is price from that pool" is a question about price *now*.

Both are available (`htf_close` is projected), so nothing is lost, but the default is the
one a decision at `T` would use. Ambiguity **C6**.

---

## 6. Alignment and disagreement across timeframes

Derived, dimensionless, and carefully **not** a verdict.

| Field | Definition | Missing means |
|---|---|---|
| `structure_alignment_code` | `+1` if every available timeframe's `structure_direction` is BULLISH; `−1` if every one is BEARISH; `0` if they differ | **any required timeframe unavailable** — never `0` |
| `structure_disagreement_count` | Number of *pairs* of available timeframes whose directions differ | as above |
| `zone_alignment_code` | Same rule over premium/discount zone | no range on one or more timeframes |
| `timeframes_available_count` | How many HTF contexts are available at `T` | never missing |

**`0` means "they disagree", `None` means "we could not ask".** Collapsing them would make
a warm-up row look like a contested market. This is the `UNKNOWN ≠ NEUTRAL` rule
([market_state.md](../ict/market_state.md) §9) applied to a new field.

Local direction participates: for a 1H row, alignment is computed over `{1H, 4H, 1D}`.

**No weighting. No "higher timeframe wins".** That would be a hypothesis. The raw
per-timeframe directions are all exposed, so Phase 4 can test any weighting it likes.

---

## 7. Output schema

### 7.1 `MtfContext` (frozen) — one base instant, one HTF

| Field | Type | Notes |
|---|---|---|
| `context_id` | `str` | `mtf:{base_tf}:{htf}:{aligned_htf_close ISO}` |
| `base_timeframe`, `higher_timeframe` | `str` | |
| `symbol`, `as_of` | `str`, `datetime` | `as_of` is the **base** instant |
| `available` | `bool` | |
| `unavailable_reason` | `MtfUnavailableReason \| None` | `NO_HIGHER_TIMEFRAME` · `NO_CLOSED_HTF_BAR` · `HTF_SERIES_MISSING` |
| `aligned_htf_close` | `datetime \| None` | **The alignment timestamp.** `confirmation_timestamp` for the `Confirmable` protocol |
| `alignment_lag_minutes` | `float \| None` | `(as_of − aligned_htf_close)` in minutes |
| `staleness_bars` | `int \| None` | base bars since the HTF bar closed |
| …the §5.4 projection… | | all `None` when `available = False` |
| `schema_version` | `str` | `MTF_SCHEMA_VERSION = "r2-11.1"` |

`confirmation_timestamp` is `aligned_htf_close`, so an `MtfContext` satisfies `Confirmable`
and `filter_observable` accepts it — the engine's one gate, again with no new code.

### 7.2 `MtfPicture` — every HTF for one base instant

`symbol` · `base_timeframe` · `as_of` · `contexts: tuple[MtfContext, ...]` (ordered by
`higher_timeframe.minutes` ascending) · the four §6 alignment fields · `schema_version`,
plus `context_for(timeframe) -> MtfContext | None`.

### 7.3 `MtfContextBuilder`

```python
builder = MtfContextBuilder(config=MtfConfig(), views={H4: view_h4, D1: view_d1},
                            pictures={H4: pic_h4, D1: pic_d1})
picture = builder.picture_at(symbol, Timeframe.H1, as_of=T)
```

The HTF `ICTEngineView`s and `LiquidityModel`s are **injected**, not built inside — for the
same reason `ICTEngineView` caches its analyses: building them per instant would be
quadratic and there is no reason for it. A convenience constructor loads the persisted
partitions and builds them once.

### 7.4 Memoisation — a design property, not an optimisation

All 240 one-hour instants inside one 1D bar align to the **same** `aligned_htf_close` and
therefore to the **same** `htf_state`. The builder caches `htf_state` keyed by
`(higher_timeframe, aligned_htf_close)`.

This is specified as design rather than left to a later optimisation because the naive
version calls `state_at()` once per HTF per base bar — roughly a 3× cost — and the cache
makes it ~1.03×. It changes **nothing** observable: the states are pure functions of
`(frame, as_of)`, exactly the argument `ICTEngineView` already makes for caching its
analyses.

A test asserts memoised and non-memoised builders produce **identical** pictures.

---

## 8. Leakage contract

### 8.1 The five ways this layer can leak

| # | Leak | Defence |
|---|---|---|
| 1 | Join on `timestamp` instead of `close_time` | `align_htf_context` / `latest_closed_bar` only; source guard; **broken implementation **B1** (L8)** |
| 2 | Read the forming HTF bar's running high/low | Forming bars excluded entirely (§4.4); **broken implementation **B2** (L8)** |
| 3 | Ask the HTF view for a state at `T` instead of at `aligned_htf_close` | `state_at()` returns `None` off a bar close, so this fails loudly rather than leaking — but a test pins it |
| 4 | Build the HTF frame by resampling the *base* frame including bars after `T` | Persisted partitions only; no `resample` import (master story §4.2) |
| 5 | Compute alignment/disagreement using a timeframe not yet available, treating missing as neutral | `None`, never `0` (§6) |

### 8.2 The leakage matrix (master story §6.3 — authoritative L1–L8)

| # | R2-11 instantiation |
|---|---|
| **L1** | **No future bars.** Truncate or append base and HTF bars after `T`; the picture at `T` is identical |
| **L2** | **Future OHLC mutation.** Violently modify every base and HTF bar after `T`; byte-identical at `T` |
| **L3** | **Dependency declared.** `htf_high`/`htf_low` depend on **wick**; `htf_close` on **close**; `aligned_htf_close`, `staleness_bars`, structure state, break ids, zone and pool ids on **confirmed events** |
| **L4** | **Point-in-time lifecycle.** The aligned HTF bar is the last with `close_time <= T`; a forming HTF bar never contributes |
| **L5** | **Prefix equivalence** at every base instant, 1H every cut, 4H true bar-by-bar |
| **L6** | **Identity stability.** A `context_id` is invariant across prefix and batch; a 4H and a 1D bar sharing a `close_time` (daily, at 00:00 UTC) yield **distinct** ids |
| **L7** | **External inputs.** Mutate **and append** HTF bars closing after `T` — the cross-timeframe form |
| **L8** | **Non-vacuous control.** Mutate an HTF bar closing **before** `T`; the picture **must change**. Run against both incorrect implementations, which must **fail** |

**Provenance integrity** is contracted in §8.4's three-link chain.

### 8.3 The mandatory MTF audit tests

Named individually because the brief requires each:

| Test | Assertion |
|---|---|
| Future HTF mutation | L7 above |
| Forming HTF bar mutation | Mutating the bar that has opened but not closed at `T` changes nothing |
| Incomplete HTF bar | A `BOUNDARY_INCOMPLETE` HTF bar never appears as aligned |
| Boundary crossing | At `T = aligned close`, the new bar is used; at `T − 1 µs`, the old one is |
| DST | On March 2026 within `production-native-2026-02_08`, alignment is identical in structure either side of both transitions; no context shifts by an hour |
| Weekend | Friday's HTF bar stays aligned through the weekend; `staleness_bars` grows monotonically |
| Missing HTF candle | The previous bar aligns; nothing is invented; staleness reflects the gap |
| Provider gap | Same, on a real gap found in the data rather than a synthetic one |

### 8.4 Multi-timeframe provenance

> **Never copy an HTF value without being able to prove it existed at the base `as_of`.**

Every projected field carries: `higher_timeframe`, the source object's **id**,
`aligned_htf_close`, and the source's own confirmation via the HTF analysis. The mechanical
proof:

```
for every id in context.source_ids():
    source := htf_analysis.resolve(id)
    assert source is not None                                  # L6a resolves
    assert source.confirmation_timestamp <= aligned_htf_close   # L6b observable in HTF
    assert aligned_htf_close <= as_of                           # L6c observable at base
```

Three links, each checkable. `assert_provenance_resolves` and
`assert_sources_observable_first` (R2-05.2) are reused for the first two.

---

## 9. Streaming contract

| Regime | Behaviour |
|---|---|
| Batch | Analyse both frames fully; query at `T` |
| Prefix | Analyse `base[:n]` and `htf[:m]` where `m` is the HTF bars closing `<= t_n`; query at `t_n` |
| Bar-by-bar | Feed base bars one at a time, HTF bars as they close |

**All three must be equal, with exactly one inherited exception.**

### 9.1 The one permitted asymmetry, inherited from R2-05.1

An HTF prefix cannot contain an HTF bar that has **opened** but not **closed** — yet the
True Daily Open is a zero-lag event read from a bar's *open*
([market_state.md](../ict/market_state.md) §10a).

So at an instant where an HTF bar has just opened, a batch analysis may know that bar's
True Daily Open while a prefix does not.

```
prefix sees LESS (staler)  <- safe, and what happens here
prefix sees MORE           <- a leak, and it never happens
```

**The asymmetry is inherited, not created.** R2-11 adds no new one, and the test asserts
the same three things R2-07's real-data suite asserts: the contexts must be equal; where
they are not, the difference must be confined to the daily-open-derived fields; and the
prefix's must be the empty or staler one.

**A test asserts the asymmetry never points the other way** — that is the assertion that
matters, and it must fail loudly if it ever does.

### 9.2 Real-data requirement — already satisfied

| Dataset | Role |
|---|---|
| **`production-native-2026-02_08`** | ✅ **On disk, and it is the primary evidence.** Six contiguous months (2026-02-01 → 2026-08-01), both symbols × 1H/4H/1D, native-sourced, hashed, manifested. EURUSD 3120/754/156 bars; XAUUSD 2955/642/155 |
| `real-2026-07` | Retained, tick-derived. Superseded as primary evidence |

**The DST prerequisite is met by data already on disk.** March 2026 — the only month
exercising **both** the US transition (2026-03-08) and the EU transition (2026-03-29) — is
inside that window, with **531 EURUSD and 508 XAUUSD hourly bars**, 128/110 4H bars and
27/27 daily bars. No backfill is required and R2-11 is **unblocked on data**.

Read it from the production store, never re-ingested:

```python
ParquetCandleStore("data/production").read(symbol, timeframe, start=..., end=...)
```

Synthetic DST fixtures remain a **supplement, never a substitute**, and both kinds of
evidence are required: R2-04 §13 records a real bug that *"real FX data hides
completely"* and that only synthetic 24/7 data exposed, while only real data exposes a
genuine provider gap or a withheld 4H window.

### 9.3 The 4H series has legitimate holes — and R2-11 must treat them as ordinary

R2-08.2 emits a 4H bar only when **all four native 1H bars are present**, or when every
absent hour is a proven market closure. Everything else is **withheld** —
`WITHHELD_BOUNDARY`, `WITHHELD_MARKET_CLOSED`, `WITHHELD_UNDETERMINED` — because *"three
traded hours labelled `4h` would be a different candle wearing the same name"*.

The measured consequence: EURUSD has 754 4H bars over six months where an unbroken grid
would hold ~1090. **Roughly a third of 4H windows are withheld**, overwhelmingly weekends
and closed hours.

For R2-11 this is not an error condition — it is the §4.7 missing-HTF-bar path, exercised
constantly rather than rarely:

- The previous existing 4H bar stays aligned, and `staleness_bars` grows across the hole.
  **That is correct**, and the growth is the honest signal.
- **No bar is invented** to fill a withheld window.
- A test asserts that alignment across a withheld window behaves identically to alignment
  across a weekend — they are the same mechanism, and neither is special-cased.

This makes R2-11's staleness features load-bearing rather than decorative: on 1H rows, a
non-trivial fraction of observations sit inside a withheld 4H window.

---

## 10. Identity, configuration and guards

### 10.1 Identity stress cases

| # | Case | Required outcome |
|---|---|---|
| 1 | 240 1H instants inside one 1D bar | **One** `context_id`, referenced 240 times. Not 240 ids |
| 2 | A 1H row's 4H and 1D contexts | **Two distinct** `context_id`s (the `higher_timeframe` is in the id) |
| 3 | Two HTF timeframes with numerically identical projected values | Distinct ids and distinct `aligned_htf_close`; a test asserts identity does not collapse on value |
| 4 | Two base instants aligning to the same HTF bar | Same `context_id`, different `as_of`, different `staleness_bars`. Proves `as_of` is *not* in the id and staleness *is* per-instant |
| 5 | An HTF bar with the same `close_time` in two different HTF timeframes (a 4H and a 1D both closing at 00:00 UTC) | Distinct ids — the timeframe discriminates |

Case 5 is real and recurs daily: the 20:00–00:00 4H bar and the daily bar both close at
00:00 UTC. Identity built on `close_time` alone would collide every single day.

### 10.2 Configuration

```bash
ICT_MTF_ENABLED=1
ICT_MTF_MEMOISE_HTF_STATES=1        # design property; off only for the equivalence test
ICT_MTF_INCLUDE_HTF_POOLS=1         # the R2-09 dependency switch (§3)
ICT_MTF_DISTANCE_REFERENCE=base_close   # base_close | htf_close  -- §5.5
```

The hierarchy itself is **not** configurable — it is computed from
`PRODUCTION_TIMEFRAMES`, which is deliberately a module constant.

### 10.3 Guard tests

| Guard | Asserts |
|---|---|
| No hand-rolled observability | No `confirmation_timestamp <=` in the module (docstrings and comments stripped first) |
| No `timestamp` join | No `merge`/`merge_asof`/`reindex` on `timestamp` |
| No resampling | `mtf.py` does not import `resample` or `build_timeframe_stack` |
| No timezone | No `zoneinfo`, no timezone literal |
| No detector re-implementation | Imports `market_state` and `liquidity_model`, not the ten individual detectors |
| No downward projection | An HTF lower than the base raises |

### 10.4 The R2-07 timeframe-locality guard must be relaxed *precisely*

R2-07 task `R2-07-16` ships guard tests that *"ban HTF joins and `D1`/`W1`"* in
`market_state.py`. R2-11 does **not** modify `market_state.py`, so those guards stay green
and must not be touched by this story.

They are revisited in **R2-12**, and only in the narrowest possible way: the ban on a
*fabricated* timeframe stays; the ban on `market_state.py` performing a join stays (R2-12
composes an already-built `MtfPicture`, it does not join anything). This note exists so
that the guard is not casually deleted when a test goes red.

---

## 11. Ambiguity register

| # | Ambiguity | Interpretations | Chosen | Why | Kind |
|---|---|---|---|---|---|
| **C1** | Whether HTF context means copied bars or a full HTF state | bars only · curated state · full state | **Curated HTF state (§5.4)** | Bars alone throw away every ICT concept, which is the point of having the engine. The full state adds 112 ambiguous columns | **Engineering** |
| **C2** | Whether forming HTF bars may be read | yes · no · labelled | **No, v1** | §4.4; master story G6 | **Engineering** |
| **C3** | Whether the hierarchy is hard-coded | listed · computed | **Computed from `PRODUCTION_TIMEFRAMES`** | One source of truth; the hierarchy follows automatically if the universe changes | **Engineering** |
| **C4** | Whether COT belongs in the MTF layer | yes · no | **No (§5.3)** | Different clock, different alignment mechanism. R2-12 composes them side by side | **Engineering** |
| **C5** | Which HTF fields to project | all · curated · bars only | **Curated**, listed exhaustively in §5.4 | Counts whose HTF unit is ambiguous are excluded or renamed to carry their unit | **Engineering** |
| **C6** | Whether HTF distances measure from the HTF close or the base close | HTF close · base close · both | **Base close (default), configurable; `htf_close` also projected** | A decision at `T` acts on price at `T`. Nothing is lost — both numbers are available | **Engineering** |
| **C7** | Whether "aligned but stale" should be marked unavailable | yes · no | **No — available, with staleness published** | The last known 4H bar genuinely is the last known 4H bar. Marking it unavailable would discard true information; publishing its age is the honest representation | **Engineering** |
| **C8** | Whether HTF disagreement should produce a verdict | yes · no | **No — code plus raw per-timeframe directions** | Master story §9. A weighting is a hypothesis | **Engineering** |
| **C9** | Whether 1D rows should carry an "empty" MTF context or none | empty context · omit | **An explicit context with `available=False`, `NO_HIGHER_TIMEFRAME`** | A missing field and a field that is legitimately empty must be distinguishable; and the row shape stays constant across timeframes, which matters for the vector | **Engineering** |

---

## 12. Files

### New

`ict_kronos/ict/mtf.py` (`MtfConfig`, `MtfUnavailableReason`, `MtfContext`, `MtfPicture`,
`MtfContextBuilder`, `MTF_SCHEMA_VERSION`) · `docs/ict/mtf.md` ·
`tests/test_mtf.py` · `tests/test_mtf_leakage.py` · `tests/test_mtf_real_data.py`

### Modified

`ict_kronos/ict/__init__.py` · `ict_kronos/app/config.py` · `.env.example` ·
`docs/ict/README.md` · `docs/dev/HANDOFF.md` (open item 2 → resolved) · `tasks/README.md`

### MUST NOT change

`ict_kronos/ict/market_state.py` · `feature_vector.py` · every detector module ·
`ict_kronos/data/resampler.py` — **especially `align_htf_context`**, which is consumed
exactly as it is · anything under `ict_kronos/features/` · every existing test, including
R2-07's timeframe-locality guards (§10.4).

---

## 13. Definition of done, and the hard stop

1. `production-native-2026-02_08` read from the production store (already on disk)
2. Every task in [R2-11-TASKS.md](../../tasks/Phase-2-ICT-Engine/R2-11-TASKS.md) ✅
3. `pytest -q` green; `ruff` and `black` clean; no silent skip
4. Both broken implementations (**B1** `timestamp` join, **B2** forming bar) built, with L1/L2/L4 proven to fail against each, and
   disagree, with the number of differing instants reported
5. All eight §8.3 audit tests passing on **real** data
6. The single permitted streaming asymmetry documented, pinned, and proven not to invert
7. Performance measured: MTF alignment cost per row, memoised vs not, batch per month
8. `docs/ict/mtf.md` written; HANDOFF open item 2 closed in the same commit
9. One local commit. **No push.**

```
=> R2-11 complete
=> audit
=> completion report
=> STOP
=> explicit approval required before R2-12 begins
```
