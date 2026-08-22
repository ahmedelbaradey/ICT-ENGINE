# R2-12 — Market State v2 — STORY

**Specification. Written before any change to `ict_kronos/ict/market_state.py`.**
Master story: [Phase-2-Market-Intelligence-STORY.md](../Phase-2-Market-Intelligence-STORY.md)
· Concept map: [R2-12-CONCEPT-MAP.md](R2-12-CONCEPT-MAP.md)
· Tasks: [R2-12-TASKS.md](../../tasks/Phase-2-ICT-Engine/R2-12-TASKS.md)

> **SPECIFICATION ONLY — implementation NOT started, NOT approved.**
>
> **Production timeframes: 1H / 4H / 1D only.** No dependency below 1H or above 1D, direct
> or indirect. `assert_production_pair` is called first on every production path and
> **raises** rather than converting.
>
> **HARD STOP at the end. R2-13 does not begin without explicit approval.**

---

## 1. The question this layer answers

R2-07 answers: *"What could a decision at instant `t` have known about ICT structure?"*

R2-12 changes exactly one word:

> **What could a decision at instant `t` have known about the market?**

Structure, liquidity **pools**, **positioning**, and **higher timeframes** — composed, not
merged, and never re-derived.

---

## 2. `ICTMarketState` is not replaced

The brief says *"Do NOT replace it blindly. Define exactly what changes."* Here is exactly
what changes.

### 2.1 The decision: extend, additively

| Option | Verdict |
|---|---|
| **A — extend `ICTMarketState` with new optional context fields defaulting to `None`** | ✅ **CHOSEN** |
| B — a new `ICTMarketStateV2` class composing a v1 instance | ❌ rejected |
| C — replace `ICTMarketState` | ❌ rejected outright |

**Why A.** It is the pattern the repository already uses — `IctEvent` carries optional
fields *"where the concept supports them… and stay `None` elsewhere rather than being
faked."* Every existing consumer keeps working unchanged. The compatibility claim becomes
mechanically testable: *for the same inputs, every v1 field of a v2 state equals the v1
state's field, value for value.*

**Why not B.** Two classes force every consumer to branch, and a `_section()` helper,
`source_ids()` and `as_dict()` would each need two implementations. The R2-06 lesson —
*"two live definitions would mean every downstream result has to name which one it used,
and the first person to forget makes the results unreproducible"* — applies to record types
as much as to algorithms.

**Why not C.** Replacement invalidates every R2-07 and R2-08 test as a matter of course,
which destroys the regression evidence at exactly the moment it is most needed.

### 2.2 What changes, exhaustively

| Element | Change |
|---|---|
| `ObservationBar` | **unchanged** |
| `StructureContext` | **unchanged** |
| `LiquidityContext` | **unchanged** — R2-09 pools go in a *new* context, not into this one |
| `ImbalanceContext` | **unchanged** |
| `InstitutionalContext` | **unchanged** |
| `CompositeContext` | **unchanged** |
| `DailyOpenContext` | **unchanged** |
| `PremiumDiscountContext` | **unchanged** |
| `SessionContext` | **unchanged** |
| `BiasContext` | **UNCHANGED — frozen. See §5** |
| `ICTMarketState` | **four new optional fields**, all defaulting to `None` |
| `MarketStateConfig` | **three new booleans**, all defaulting to `False` |
| `ICTEngineView` | **three new optional injected collaborators** |
| `MarketStateBuilder` | **three new optional injected collaborators** |
| `STATE_VERSION` | `r2-07.1` → **`r2-12.1`** |
| `as_dict()` | Gains four keys. **Every existing key keeps its exact position and value** |
| `source_ids()` | Gains four groups. Every existing group unchanged |

### 2.3 The four new contexts

| Field | Type | Source | `None` when |
|---|---|---|---|
| `liquidity_context` | `LiquidityPoolContext \| None` | R2-09 `LiquidityPicture` | the model was not injected |
| `cot_context` | `CotContext \| None` | R2-10 — a **tuple of `CotSnapshot`, one per family** | the model was not injected |
| `htf_context` | `MtfStateContext \| None` | R2-11 `MtfPicture` | the builder was not injected |
| `data_quality` | `DataQualityContext \| None` | R2-08 `BarCoverage` | no coverage report was supplied |

**Field names are the revised brief's** — `liquidity_context`, `cot_context`,
`htf_context` — so the state's vocabulary matches the specification it was written from.

**All four default to `None` and all three config switches default to `False`.** So
`MarketStateBuilder()` with no arguments produces a state that is byte-identical to R2-07's
in every existing field, and whose four new fields are `None`. That is the property the
compatibility test asserts, and it is what makes this change safe to land before R2-13.

---

## 3. The composition rule

> **R2-12 contains no analysis. It reads three point-in-time APIs and copies ids.**

R2-07's module docstring already states the rule this layer inherits:

> *"It is an aggregation, not a detector. It contains no pattern logic, no thresholds, no
> geometry and no lifecycle rules of its own… **There is no `confirmation_timestamp <=
> as_of` comparison anywhere in this file**, and a source-level guard enforces it."*

R2-12 extends the guard rather than weakening it. The three calls it is permitted to make:

```python
liquidity_context := liquidity_model.picture_at(as_of)               # R2-09
cot_context       := cot_model.snapshots_at(symbol, as_of)           # R2-10, ONE PER FAMILY
htf_context       := mtf_builder.picture_at(symbol, timeframe, as_of) # R2-11
```

Each already applied the gate internally. R2-12 applies nothing.

**What R2-12 may compute:** arithmetic over values that were returned, using the existing
`_points()` and `_bars_since()` helpers. Nothing else.

---

## 4. Evidence versus interpretation

This is the story where the temptation is greatest, so the rule is concrete.

```
EVIDENCE (kept in full, always)
  the nearest untaken buy-side pool: id, price, distance, cardinality, member types,
  internal/external, the range it was classified against
  the applicable COT report: id, date, release, raw contracts, open interest
  the aligned 4H bar: close time, staleness, structure direction, zone, break id

INTERPRETATION (minimised, named, separable)
  "price is in discount, which is bullish-leaning"   <- ALREADY in R2-07's bias, one of four
  "the 4H is bullish so the 1H pullback is a buy"    <- NOT BUILT. A Phase 4 hypothesis
  "commercials are extreme so a reversal is due"     <- NOT BUILT. A Phase 4 hypothesis
```

**No new interpretation is added by R2-12.** Not one. The three new layers contribute
*evidence only*, and the single interpretive value in the state — `bias` — is frozen (§5).

Every new context therefore contains: ids, timestamps, counts, prices, distances, ratios,
categorical states and explicit availability reasons. It contains **no** score, weight,
strength, quality, confidence or ranking.

---

## 5. Bias — frozen, and why that is the harder choice

`BiasContext` counts four sources: structure direction, delivery state (CISD), the
dealing-range zone, and the latest sweep's side. `bullish_score` and `bearish_score` are
documented in [features.md](../ict/features.md) §10 with range **0–4**.

The obvious move is to add three more sources — pool asymmetry, COT positioning, HTF
alignment — and let the count run to 0–7.

**That move is rejected.**

| Reason | Detail |
|---|---|
| It silently changes a shipped feature's meaning | `bullish_evidence_count` currently has range 0–4. Extending it to 0–7 makes the *same column name* mean something different, in datasets that already record `feature_version = r2-07.1`. Two datasets would carry the same column with two meanings |
| It changes `bias_code` for the same market | A market that was `BULLISH` under four sources could become `NEUTRAL` under seven, with no market change whatsoever. Every downstream result would have to name which counting rule produced it — the R2-06 lesson verbatim |
| The new sources are not commensurable with the old | "The 4H structure is bullish" and "price is in discount" are not one vote each in any sense the source material supports. Treating them as equal votes is a weighting — and *"a weight is a hypothesis and this story does not test hypotheses"* (market_state.md §9) |
| There is nothing to gain | Any model can compute any weighting from the raw evidence, which is fully exposed |

### 5.1 What is added instead: an evidence ledger with no verdict

```python
@dataclass(frozen=True)
class EvidenceContext:
    """Named directional evidence from EVERY layer. No verdict, no score, no weight."""
    structure_evidence:   tuple[str, ...]   # e.g. ("local_structure_bullish",)
    liquidity_evidence:   tuple[str, ...]   # ("sell_side_liquidity_taken", "buy_side_pool_nearer")
    range_evidence:       tuple[str, ...]   # ("price_in_discount",)
    delivery_evidence:    tuple[str, ...]
    mtf_evidence:         tuple[str, ...]   # ("h4_structure_bullish", "d1_structure_bearish")
    cot_evidence:         tuple[str, ...]   # ("noncommercial_net_long",)
    bullish_labels:       tuple[str, ...]   # every label above judged bullish-leaning
    bearish_labels:       tuple[str, ...]
```

Note what is **absent**: no `bias`, no `score`, no `code`. There is no verdict field,
because a verdict is the thing being refused.

The bullish/bearish partition is a **labelling of evidence**, not an aggregation — each
label's direction is fixed and documented at the point it is emitted, exactly as R2-07
already documents "taking sell-side liquidity is a bullish-leaning fact".

`bias` and `EvidenceContext` coexist. `bias` stays exactly what it is: four sources, range
0–4, unchanged verdict. `EvidenceContext` is strictly richer and strictly non-authoritative.

### 5.1a `extended_bias` — permitted, optional, and clearly distinct

The revised brief permits *"an optional new `extended_bias`… clearly distinct from the
original bias"*. An earlier draft of this specification refused **any** second verdict. The
brief supersedes that refusal (master story **G7**), and the refusal's substance is kept by
five constraints that make the two impossible to confuse:

| Constraint | Detail |
|---|---|
| **Different name, different type** | `extended_bias: ExtendedBias \| None` — a distinct enum, never `MarketBias`, so the two cannot be assigned to each other even by accident |
| **Different field, never a replacement** | `bias` remains present, unchanged, and is never derived from `extended_bias` or vice versa |
| **Off by default** | `MarketStateConfig.extended_bias = False`. A state that does not ask for it gets `None` |
| **`None` ≠ `UNKNOWN`** | `None` means *"not computed"*; `UNKNOWN` means *"computed, and there was no evidence"*. Two different facts, never collapsed |
| **Its inputs are named on the record** | `extended_bias_sources: tuple[str, ...]` — exactly which layers contributed, so a value can never be interpreted without knowing what it counted |

Four states are preserved in **both** verdicts, and neither is collapsed:

```
UNKNOWN   no evidence existed to weigh        (or the layer was unavailable)
NEUTRAL   evidence existed and conflicts
BULLISH   strictly more bullish evidence
BEARISH   strictly more bearish evidence
```

**The counting rule for `extended_bias` is the same counting rule** — one item per
contributing source, strictly-more wins, ties are `NEUTRAL`, nothing is `UNKNOWN` unless
there was nothing to count. **No weights**, for the reason that has not changed: a weight is
a hypothesis, and this phase tests none.

> **Recorded concern, not an objection.** Two verdicts in one record means every downstream
> result must name which it used. The five constraints above make that mechanically
> checkable rather than a matter of discipline — `extended_bias` is a different type, in a
> different field, absent unless requested, and carries its own source list. A test asserts
> that a state with `extended_bias` computed has a **byte-identical** `bias` to one without,
> which is the property that keeps the original trustworthy.

### 5.2 UNKNOWN, NEUTRAL, BULLISH, BEARISH

The four remain four, everywhere in the state, for every new categorical field:

```
UNKNOWN   no evidence existed to weigh          (or: the layer was unavailable)
NEUTRAL   evidence existed and conflicts
BULLISH   strictly more bullish evidence
BEARISH   strictly more bearish evidence
```

**`UNKNOWN` is never collapsed into `NEUTRAL` in the state.** The only place the two are
collapsed is `ICTFeatureVector`'s documented lossy projection of `bias_code` to `0`
(features.md §2), which R2-13 leaves exactly as it is and **does not extend to any new
field** (see [R2-13](R2-13-FEATURE-VECTOR-V2-STORY.md) §5.2).

---

## 6. The four new contexts

### 6.1 `LiquidityPoolContext` (R2-09)

Fields: `buy_side_pool_ids`, `sell_side_pool_ids` (sorted tuples) · `buy_side_pool_count`,
`sell_side_pool_count` · `nearest_buy_side_pool_id`, `nearest_buy_side_pool_price`,
`nearest_buy_side_pool_points`, `nearest_buy_side_pool_cardinality`,
`nearest_buy_side_pool_zone` · the four sell-side mirrors · `internal_buy_side_count`,
`external_buy_side_count`, `internal_sell_side_count`, `external_sell_side_count`,
`unknown_zone_count` · `taken_buy_side_count`, `taken_sell_side_count` ·
`touched_level_ids`, `confirmed_taken_level_ids` · `bars_since_buy_side_sweep`,
`bars_since_sell_side_sweep` · `liquidity_asymmetry`, `nearest_side_code`,
`nearest_relative_position` · `source_range_id`.

**Why a separate context rather than extra fields on `LiquidityContext`:** R2-04 levels and
R2-09 pools have different lifetimes, different identities and different provenance
registries. Merging them would make `LiquidityContext.active_buy_side_ids` and a pool id
resolve against different registries from one record — and `source_ids()` groups by
originating detector precisely so that cannot happen.

### 6.2 `CotContext` (R2-10)

A flat projection of `CotSnapshot` (see [R2-10](R2-10-COT-STORY.md) §6.2), carrying
`available`, `unavailable_reason`, `report_id`, `previous_report_id`, `history_report_ids`,
`report_date`, `release_timestamp`, `report_age_days`, `period_age_days`,
`release_is_derived`, the raw category nets, open interest, the dimensionless ratios, the
historical index/percentile, `reports_in_history`, and the `mapping` approximation record.

**`mapping` is carried on every state, not looked up.** A state that says "non-commercial
net is +140 000" without saying "this is CME Euro FX futures, an approximation for spot
EURUSD" is a state that invites a false reading months later.

### 6.3 `MtfContext_` (R2-11)

Named with a trailing underscore in the state namespace **only if** it would otherwise
shadow R2-11's per-timeframe `MtfContext`; the state field holds the *picture* (all HTFs),
so the cleaner resolution — and the recommended one — is to name the state field `mtf` and
the type `MtfStateContext`, holding:

`base_timeframe` · `contexts: tuple[MtfContext, ...]` (R2-11's records, unchanged) ·
`structure_alignment_code`, `structure_disagreement_count`, `zone_alignment_code`,
`timeframes_available_count` · `higher_timeframes_expected` (from
`PRODUCTION_TIMEFRAMES`) · `higher_timeframes_available`.

R2-11's records are embedded **by value, unchanged** — not re-flattened — so there is one
definition of an MTF context in the codebase.

### 6.4 `DataQualityContext` (R2-08 coverage)

`bar_quality` (`BarQuality`) · `gap_cause` (`GapCause`) · `coverage_ratio` ·
`expected_source_observations`, `actual_source_observations`, `missing_observations`,
`market_closed_observations`, `undetermined_observations`, `longest_missing_run` ·
`boundary_incomplete`, `production_eligible` · `source_timeframe` (what the coverage was
measured against — 1M).

**This context describes; it never filters.** No state is withheld, no value is nulled and
no bar is dropped because of it. A `DEGRADED_UNKNOWN` bar produces a complete, ordinary
state — with its degradation *visible*. That is the R2-08 rule (*"a coverage ratio is a
quality signal, never a validity rule"*) carried one layer up.

It is `None` when no `CoverageReport` was supplied, which is a different fact from "the bar
is complete" and must not be confused with it.

---

## 7. Data quality and availability

Every new context is **tri-state**, and the three are never collapsed:

| State | `LiquidityPoolContext` | `CotContext` | `MtfStateContext` | `DataQualityContext` |
|---|---|---|---|---|
| Present | pools exist | a report is applicable | ≥ 1 HTF aligned | coverage supplied |
| Genuinely empty | counts `0`, `nearest_* = None` | `available=False`, `NOT_YET_RELEASED` | `NO_HIGHER_TIMEFRAME` (a 1D row) | — |
| Data missing | context is `None` (model not injected) | `DATASET_MISSING` | `HTF_SERIES_MISSING` | context is `None` |

**A count of `0` and a `None` distance are different facts** — the R2-07 rule, extended
verbatim. `buy_side_pool_count == 0` means "no resting pools"; `nearest_buy_side_points is
None` means "nothing to measure to". Emitting `0` for both would tell a model price is
sitting **on** a pool that does not exist.

---

## 8. Provenance

`source_ids()` gains four groups; **every existing group is unchanged**.

| Group | Contents | Resolves against |
|---|---|---|
| `liquidity_pool` | every pool id, plus the two nearest | `LiquidityPicture` |
| `cot` | `report_id`, `previous_report_id`, every `history_report_id` | the COT report registry |
| `mtf` | every `context_id`, plus every projected HTF source id | the per-HTF analyses |
| `dealing_range` *(existing)* | **gains** `liquidity_pools.source_range_id` | `DealingRangeAnalysis` |

That last row is the R2-07 audit's lesson applied *before* it bites: `source_ids()` once
omitted `premium_discount.source_break_id` for a whole story, and no test noticed because
the value usually equalled another field. The regression test that caught it —
**marker substitution**, which stamps each field with a unique value and asks whether the
*field* is read rather than whether the *value* appears — is mandatory for all four new
groups.

---

## 9. Leakage contract

### 9.1 The one way R2-12 can leak

Every input is already gated. The only new leak available is **recomputing** something
inside the state instead of reading the layer's point-in-time API — e.g. reaching into
`liquidity_model.analysis.pools` (all of them) instead of calling `picture_at(as_of)`.

That is R2-12's **deliberately broken implementation (L8)**, and it must be built and proven to
disagree.

### 9.2 The leakage matrix (master story §6.3 — authoritative L1–L8)

| # | R2-12 instantiation |
|---|---|
| **L1** | **No future bars.** No bar, COT report or HTF bar after `as_of` may affect the state |
| **L2** | **Future OHLC mutation.** Mutate every bar after `as_of`; the v2 state is byte-identical |
| **L3** | **Dependency declared** — the **union** of the composed layers' declarations and nothing more |
| **L4** | **Point-in-time lifecycle.** Every context reflects only what its layer's point-in-time API returned at `as_of` |
| **L5** | **Prefix equivalence** at every instant on 1H and 4H, for **states and `as_dict()` payloads** |
| **L6** | **Identity stability.** Embedded ids are invariant across prefix and batch; the five collision cases of §11 |
| **L7** | **External inputs.** Mutate future COT reports **and** future HTF bars together |
| **L8** | **Non-vacuous control**, three ways — mutate a past bar, a past COT report, a past HTF bar; each **must change** the state. Run against the §9.1 incorrect implementation, which must **fail** |

**Provenance integrity** is contracted in §8, with marker-substitution completeness for all
four new groups.

### 9.3 The guard that must keep passing

`market_state.py` contains **no** `confirmation_timestamp <=` comparison, enforced by a
source-level guard with a four-way mutation test of the guard itself (R2-07 audit). R2-12
adds code to this file and **must not** add the first such comparison.

The guard's docstring-and-comment stripper stays load-bearing: the new code will *mention*
observability in order to warn against it.

---

## 10. Streaming contract

`batch == prefix == bar-by-bar`, with **exactly the exceptions the composed layers already
declare and no others**:

| Source | Asymmetry | Status |
|---|---|---|
| R2-05.1 True Daily Open (local) | Prefix sees staler | **Inherited, already documented and pinned** (market_state.md §10a) |
| R2-11 HTF True Daily Open | Prefix sees staler, same shape | **Inherited from R2-11** §9.1 |
| R2-09 | none | claims none |
| R2-10 | none — revisions are append-only | claims none |

**R2-12 introduces no new asymmetry.** The real-data suite asserts states are equal and,
where they are not, that the difference is confined to the daily-open-derived fields (local
and HTF), that the prefix's is the staler one, and that the direction never inverts.

`NaN` never enters a state: R2-06's degenerate-range `NaN` is already translated to `None`
at that boundary, and the same translation is required for every new numeric field. A `NaN`
inside a record breaks `from_dict(as_dict()) == v` **and** would report a phantom streaming
difference (R2-07 audit defect 1).

---

## 11. Identity

Additional stress cases beyond those of R2-09/R2-10/R2-11:

| # | Case | Required outcome |
|---|---|---|
| 1 | One COT report across 500 hourly states | **One** `report_id`, referenced 500 times |
| 2 | Two states at different `as_of` sharing every id | Distinct states (`as_of` differs); identical id tuples; **`as_of` is not part of any embedded id** |
| 3 | A pool id and a level id that stringify similarly | Resolve against **different registries**; `source_ids()` groups them separately |
| 4 | A dealing range id reached from *two* paths (`premium_discount.range_id` and `liquidity_pools.source_range_id`) | **One** entry in the `dealing_range` group — `_ids()` deduplicates and sorts — and marker substitution proves both fields are read |
| 5 | An `MtfContext` id equal to a local record's id by coincidence | Distinct groups; no cross-registry resolution attempted |

Case 4 is the R2-07 audit defect reproduced deliberately, one layer up.

---

## 12. Performance

R2-07 measured **≈ 2 ms per instant**. R2-12 adds three lookups per instant:

| Addition | Expected cost | Design that keeps it there |
|---|---|---|
| R2-09 pool picture | O(active levels) — a single-linkage walk over an already-sorted list | Levels sorted once per analysis, not per instant |
| R2-10 snapshot | O(log n) selection + O(W) window | Reports sorted once by `release_timestamp`; bisect for selection |
| R2-11 picture | ~O(1) amortised | HTF states memoised by `(htf, aligned_close)` — R2-11 §7.4 |

Reported, not optimised. The completion report states per-instant cost with each layer
enabled and disabled, so the marginal cost of each is attributable. HANDOFF open item 3
(*"building a state for every bar of a multi-year 1m dataset would be hours"*) is a
**1m** concern; production runs 1H/4H/1D, where a month is 549 / 141 / 26 bars.

---

## 13. Ambiguity register

| # | Ambiguity | Interpretations | Chosen | Why | Kind |
|---|---|---|---|---|---|
| **D1** | Replace, parallel, or extend `ICTMarketState` | three | **Extend, additively** | §2.1 | **Engineering** |
| **D2** | Whether `bias` absorbs the new layers | extend it · a second, separately-named verdict · freeze it | **Freeze the original; add a verdict-free `EvidenceContext`; an OPTIONAL, clearly-distinct `extended_bias` is permitted (§5.1a)** | Changing a shipped feature's range and verdict for the same market is not a refinement, it is a different feature wearing the same name — so `bias` is untouched. `extended_bias` is **DIRECTED BY THE REVISED BRIEF**, superseding this document's earlier refusal of any second verdict; five constraints make the two mechanically impossible to confuse | **Engineering — directed** |
| **D3** | Whether pools extend `LiquidityContext` or get their own | extend · separate | **Separate context** | Different registries; `source_ids()` groups by originating detector | **Engineering** |
| **D4** | Whether data quality filters or describes | filter · describe | **Describe only** | R2-08's rule, carried up. Filtering here would hide degraded observations from the very model that should learn they are degraded | **Engineering** |
| **D5** | Whether the new contexts default on or off | on · off | **Off** (all three switches `False`, all four fields `None`) | Makes the v1-compatibility test trivially true and lets R2-12 land before R2-13 without changing a single existing number | **Engineering** |
| **D6** | Whether `EvidenceContext` should carry a verdict | yes · no | **No verdict field at all** | A verdict field would become a second bias by use, whatever the docstring says | **Engineering** |
| **D7** | Whether R2-11 records are embedded or re-flattened | embed · flatten | **Embed by value, unchanged** | One definition of an MTF context in the codebase | **Engineering** |
| **D8** | `state_version` bump | keep · minor · new | **`r2-12.1`** | The shape changed. A dataset records the version, so results stay tied to the definitions that produced them | **Engineering** |

---

## 14. Files

### Modified — the only production file this story changes

| File | Change |
|---|---|
| `ict_kronos/ict/market_state.py` | Four new frozen context dataclasses, `EvidenceContext`, four new optional `ICTMarketState` fields, three new `MarketStateConfig` switches, three new injected collaborators on `ICTEngineView` / `MarketStateBuilder`, four new `source_ids()` groups, `STATE_VERSION → r2-12.1` |

### Also modified

`ict_kronos/ict/__init__.py` (exports) · `ict_kronos/app/config.py` · `.env.example` ·
`docs/ict/market_state.md` (new sections; §10 timeframe-locality rewritten to point at
R2-11) · `docs/dev/HANDOFF.md` · `tasks/README.md`

### New

`tests/test_market_state_v2.py` · `tests/test_market_state_v2_leakage.py` ·
`tests/test_market_state_v2_real_data.py` · `tests/test_market_state_v1_compatibility.py`

### MUST NOT change

`ict_kronos/ict/feature_vector.py` (that is R2-13) · every detector module ·
`liquidity_model.py`, `mtf.py`, `data/cot/` (finished stories) · anything under
`ict_kronos/features/` · **every existing R2-07 test must still pass unmodified.**

That last clause is the story's strongest safety property: if `tests/test_market_state.py`
(788 lines) and `tests/test_market_state_real_data.py` (604 lines) pass **untouched**, the
extension is genuinely additive.

---

## 15. Definition of done, and the hard stop

1. R2-09, R2-10 and R2-11 complete and approved
2. Every task in [R2-12-TASKS.md](../../tasks/Phase-2-ICT-Engine/R2-12-TASKS.md) ✅
3. **`tests/test_market_state.py` and `tests/test_market_state_real_data.py` pass with zero
   edits** — the compatibility proof
4. `test_market_state_v1_compatibility.py`: for the same inputs, every v1 field of a v2
   state equals the v1 state's field, on real data, both symbols, all three timeframes
5. `pytest -q` green; `ruff` and `black` clean; no silent skip
6. The naive recompute implementation built and proven to disagree
7. Marker-substitution provenance completeness for all four new groups
8. No new streaming asymmetry; the two inherited ones pinned and proven not to invert
9. Performance measured per layer, enabled and disabled
10. `docs/ict/market_state.md` updated; HANDOFF updated in the same commit
11. One local commit. **No push.**

```
=> R2-12 complete
=> audit
=> completion report
=> STOP
=> explicit approval required before R2-13 begins
```
