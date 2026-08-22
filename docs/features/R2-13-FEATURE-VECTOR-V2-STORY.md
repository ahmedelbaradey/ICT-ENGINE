# R2-13 — Feature Vector v2 — STORY

**Specification. Written before any change to `ict_kronos/ict/feature_vector.py`.**
Master story: [Phase-2-Market-Intelligence-STORY.md](../Phase-2-Market-Intelligence-STORY.md)
· Concept map: [R2-13-CONCEPT-MAP.md](R2-13-CONCEPT-MAP.md)
· Tasks: [R2-13-TASKS.md](../../tasks/Phase-2-ICT-Engine/R2-13-TASKS.md)

> **SPECIFICATION ONLY — implementation NOT started, NOT approved.**
>
> **Production timeframes: 1H / 4H / 1D only.** No dependency below 1H or above 1D, direct
> or indirect. `assert_production_pair` is called first on every production path and
> **raises** rather than converting.
>
> **HARD STOP at the end. This is the last story of the phase; nothing follows it without
> explicit approval.**

---

## 1. The four questions, answered up front

The brief requires these four to be settled explicitly. They are:

| # | Question | Answer |
|---|---|---|
| 1 | Does R2-13 **add** features? | **Yes** — appended, never inserted |
| 2 | Does it create a **v2 vector**? | **Yes, by extending the same class**, not by creating a second one |
| 3 | Does it keep **v1 compatibility**? | **Yes, mechanically**: `FEATURE_NAMES_V2[:56] == FEATURE_NAMES`, so `v2.as_row()[:56]` equals a v1 row **element for element** |
| 4 | Does it change the **schema version**? | **Yes** — `FEATURE_VERSION`: `r2-07.1` → **`r2-13.1`** |

### 1.1 The append-only invariant

```python
FEATURE_NAMES_V2[:len(FEATURE_NAMES)] == FEATURE_NAMES        # exact tuple equality
```

Everything else follows from it:

- No existing column is reordered, renamed, removed, or given a new meaning.
- `as_row()[:56]` is bit-identical to the v1 row for the same state.
- A model trained on v1 columns can be applied to a v2 dataset by taking the first 56
  columns, with no lookup table and no renaming.
- A v1 dataset and a v2 dataset can be compared column-for-column over their shared prefix.

**This is a single-line test and it is the story's central guarantee.**

`FEATURE_NAMES` remains exported, unchanged, as the v1 schema. It is never redefined to
mean "the current schema" — that would make the invariant unstateable.

---

## 2. Why extend rather than build a second class

| Option | Verdict |
|---|---|
| **A — extend `ICTFeatureVector`, append to a new `FEATURE_NAMES_V2`** | ✅ **CHOSEN** |
| B — a new `ICTFeatureVectorV2` class | ❌ Two `from_state`s, two `as_row`s, two round-trip suites, and every consumer branches |
| C — replace the 56 with a redesigned set | ❌ Destroys every R2-07/R2-08 result and the regression evidence |

Master story **G8**. The decisive argument is testability: option A's compatibility claim is
one tuple comparison; option B's is a hand-maintained mapping that rots.

---

## 3. What R2-13 reads

**Only `ICTMarketState` v2.** The vector *"reads a state that was already built
point-in-time, which is why it cannot leak: there is no future to reach for"*
(feature_vector.py module docstring). R2-13 preserves that property exactly:

```
feature_vector.py imports:  market_state, liquidity_model (enums only),
                            cot contract (enums only), mtf (enums only), coverage (enums only)
feature_vector.py does NOT import:  pandas frames, detectors, providers, resampler
```

A guard test asserts the module reads no frame, performs no timestamp comparison, and calls
no detector.

When a v2 context is `None` (the layer was not enabled), **every feature derived from it is
`None`** — never `0`, never a default. That is the tri-state rule of master story §5.2
reaching the column level.

---

## 4. The encoding rules — unchanged, and one addition

The three rules from [features.md](../ict/features.md) §1 carry through verbatim:

1. **The observation timestamp is the anchoring bar's `close_time`**, and every value is
   computable from information confirmed at or before it.
2. **`0` is a value, not "missing".** `None` in `as_dict()`, `math.nan` in `as_row()` —
   never `0`.
3. **Distances are in instrument points**, named `*_points`. Prices and points are never
   mixed. **No ATR or volatility normalisation exists, and none is added.**

**The one addition, made explicit because v2 introduces genuine ratios:**

4. **Dimensionless features are named without a unit suffix and are documented with their
   range.** A ratio in `[−1, 1]` (`liquidity_asymmetry`), a share in `[0, 1]`
   (`cot_noncommercial_long_share`) and an index in `[0, 100]` (`cot_index_*`) are three
   different scales, and each column's range is part of its contract. **None of them is
   rescaled to a common range**, because rescaling would require a choice this layer has no
   basis to make.

### 4.1 Categorical encodings

Existing tables (`DIRECTION_CODES`, `STRUCTURE_STATE_CODES`, `DELIVERY_STATE_CODES`,
`ZONE_CODES`, `BIAS_CODES`, `BREAK_TYPE_CODES`) are **unchanged**. New tables, declared as
module constants and never fitted from data:

| Table | Mapping |
|---|---|
| `POOL_ZONE_CODES` | `internal 0`, `external 1` — **`unknown` is `None`, not a third integer** |
| `MTF_ALIGNMENT_CODES` | `bearish −1`, `mixed 0`, `bullish 1` — **unavailable is `None`** |
| `BAR_QUALITY_CODES` | `complete 0`, `market_gap 1`, `degraded_unknown 2`, `boundary_incomplete 3` |
| `GAP_CAUSE_CODES` | `none 0`, `market_closed 1`, `undetermined 2`, `boundary 3` |
| `COT_UNAVAILABLE_CODES` | `no_history 1`, `not_yet_released 2`, `symbol_unmapped 3`, `report_type_unavailable 4`, `dataset_missing 5` — **`0` is not used**, so "available" is unambiguously the absence of a reason |
| `MTF_UNAVAILABLE_CODES` | `no_higher_timeframe 1`, `no_closed_htf_bar 2`, `htf_series_missing 3` |

**Sign remains meaningful where a direction exists** (negative bearish-leaning, positive
bullish-leaning), so a linear model reads the sign without one-hot expansion.

`BAR_QUALITY_CODES` and `GAP_CAUSE_CODES` are deliberately **ordinal-looking but not
ordinal**: `boundary_incomplete = 3` is not "worse than" `degraded_unknown = 2` on any scale
the data supports. Documented as nominal codes; a model that wants one-hot can build it.

**`unknown`/`neutral` collapsing to `0` happens in exactly one place — `bias_code` — and is
not extended.** Every new categorical uses `None` for unavailable.

---

## 5. The v2 feature catalogue

**56 existing + 71 new = 127 columns.** Counts per group are stated so a reviewer can check
the total.

### 5.0 Groups 1–10 — the existing 56, unchanged

Price/dealing range (9) · structure (12) · liquidity (8) · imbalance (7) · institutional &
composites (11) · session & temporal (6) · derived bias (3). Catalogued in
[features.md](../ict/features.md) §§4–10. **Not restated here, because restating them
invites divergence.** That document remains the definition of record for the first 56
columns, and a test asserts the names still match it.

### 5.1 Group 11 — liquidity pools and significance, R2-09 (26)

| Feature | Unit | Range | Missing means |
|---|---|---|---|
| `buy_side_pool_count` | count | ≥ 0 | pool layer disabled |
| `sell_side_pool_count` | count | ≥ 0 | as above |
| `nearest_buy_side_pool_points` | points, absolute | ≥ 0 | **no untaken buy-side pool exists** |
| `nearest_sell_side_pool_points` | points, absolute | ≥ 0 | no untaken sell-side pool |
| `nearest_buy_side_pool_cardinality` | count | ≥ 2 | no such pool |
| `nearest_sell_side_pool_cardinality` | count | ≥ 2 | no such pool |
| `nearest_buy_side_pool_zone_code` | code | 0/1 | no such pool, **or no dealing range** |
| `nearest_sell_side_pool_zone_code` | code | 0/1 | as above |
| `internal_buy_side_count` | count | ≥ 0 | no dealing range |
| `external_buy_side_count` | count | ≥ 0 | no dealing range |
| `internal_sell_side_count` | count | ≥ 0 | no dealing range |
| `external_sell_side_count` | count | ≥ 0 | no dealing range |
| `taken_buy_side_count` | count | ≥ 0 | pool layer disabled |
| `taken_sell_side_count` | count | ≥ 0 | as above |
| `liquidity_asymmetry` | ratio | `[−1, 1]` | **both counts zero** |
| `nearest_side_code` | code | −1/0/+1 | **either side missing** |
| `bars_since_buy_side_sweep` | bars | ≥ 0 | no observable buy-side sweep |
| `bars_since_sell_side_sweep` | bars | ≥ 0 | no observable sell-side sweep |
| `nearest_buy_side_pool_approach_count` | count | ≥ 0 | no such pool |
| `nearest_sell_side_pool_approach_count` | count | ≥ 0 | no such pool |
| `nearest_buy_side_pool_age_bars` | bars | ≥ 0 | no such pool |
| `nearest_sell_side_pool_age_bars` | bars | ≥ 0 | no such pool |
| `nearest_buy_side_pool_distinct_types` | count | ≥ 1 | no such pool |
| `nearest_sell_side_pool_distinct_types` | count | ≥ 1 | no such pool |
| `nearest_buy_side_pool_density` | ratio | `(0, cardinality]` | no such pool |
| `nearest_sell_side_pool_density` | ratio | `(0, cardinality]` | no such pool |

**The measurable significance components ship; the composite `significance_score` does
not.** R2-09 §5a.4 makes the composite optional, off by default and weight-driven — and a
weighted sum is exactly the hypothesis the vector must not encode. Ambiguity **E11**.

**Two candidates are deliberately absent**, matching R2-09: `type_rank` (an invented
level-type ordinal, removed) and any `reaction` column (**AD-2 — no deterministic
definition; `reaction_status = NOT_AVAILABLE` lives on the state, not in the vector**).
`structural_context` reaches the vector as `nearest_*_pool_zone_code`, and
`distance_to_current_price` as `nearest_*_pool_points`, so all five approved components are
represented without duplication.

**`nearest_*_points` is absolute, matching the existing `nearest_buy_side_points`.** Sign is
already carried by the side, and mixing an absolute and a signed distance under similar
names is how a sign error hides.

### 5.2 Group 12 — Normalized COT, R2-10 (16)

**Legacy COT is excluded entirely.** Columns project the **normalised** representation, so
one column set serves both instruments; the raw family travels as provenance on the state
and as one projected column here.

| Feature | Unit | Range | Missing means |
|---|---|---|---|
| `cot_available` | flag | 0/1 | **never missing** — the witness |
| `cot_unavailable_reason_code` | code | 1–5 | a report **is** available |
| `cot_report_family_code` | code | 1=TFF, 2=DISAGGREGATED | no report — **provenance, always exposed** |
| `cot_report_age_days` | days | ≥ 0 | no applicable report |
| `cot_period_age_days` | days | ≥ 0 | no applicable report |
| `cot_publication_is_derived` | flag | 0/1 | no applicable report |
| `cot_open_interest` | contracts | ≥ 0 | no report |
| `cot_speculative_net` | contracts, signed | ℝ | no report, or the role is absent in this family |
| `cot_speculative_net_pct_oi` | ratio | `[−1, 1]` | as above, or `OI == 0` |
| `cot_speculative_long_share` | ratio | `[0, 1]` | as above, or `L + S == 0` |
| `cot_intermediary_net_pct_oi` | ratio | `[−1, 1]` | role absent in this family |
| `cot_hedger_net_pct_oi` | ratio | `[−1, 1]` | **role absent** — `None` for a currency market |
| `cot_speculative_net_change` | contracts, signed | ℝ | fewer than two **published** reports |
| `cot_positioning_change_rate` | ratio | ℝ | as above, or `OI == 0` |
| `cot_speculative_historical_rank` | ratio | `[0, 1]` | warm-up not met, or `max == min` |
| `cot_reports_in_history` | count | ≥ 0 | **never missing** — the warm-up witness |

`cot_speculative_*` reads the `SPECULATIVE_LEVERAGED` **normalised role** — Leveraged Funds
for TFF, Managed Money for Disaggregated. **That mapping is a declared table with a
justification per row, not a name match**, and `source_category` is preserved on the state
so it is reversible and auditable ([R2-10](R2-10-COT-STORY.md) §3.4).

**A role absent in a family is `None`, never filled from another role.**
`cot_hedger_net_pct_oi` is structurally `None` for EURUSD because TFF reports no commercial
hedger category — explained by the role's absence, not by missing data.

`extreme_flag` is **deliberately not projected** while `cot_speculative_historical_rank`
is: the continuous rank carries strictly more information, and a threshold belongs to the
model. **The tri-state flag still exists on the state** for any consumer that wants it.
Ambiguity **E10**.

### 5.3 Group 13 — multi-timeframe, R2-11 (23)

Per higher timeframe (`h4_`, `d1_`), ten features each:

| Suffix | Unit | Range | Missing means |
|---|---|---|---|
| `_available` | flag | 0/1 | **never missing** |
| `_unavailable_reason_code` | code | 1–3 | the context **is** available |
| `_staleness_bars` | base bars | ≥ 0 | context unavailable |
| `_alignment_lag_minutes` | minutes | ≥ 0 | context unavailable |
| `_structure_state_code` | code | −1/0/1 | context unavailable |
| `_structure_direction_code` | code | −1/0/1 | context unavailable |
| `_bars_since_break_htf_bars` | **HTF** bars | ≥ 0 | no HTF break observable |
| `_percentage_position` | ratio, **unclamped** | ℝ | no HTF dealing range |
| `_distance_from_equilibrium_points` | points, signed | ℝ | no HTF dealing range |
| `_nearest_buy_side_pool_points` | points, absolute | ≥ 0 | no HTF buy-side pool |

Twenty for the two timeframes, plus three cross-timeframe:

| Feature | Unit | Range | Missing means |
|---|---|---|---|
| `mtf_structure_alignment_code` | code | −1/0/1 | **any required timeframe unavailable** — never `0` |
| `mtf_structure_disagreement_count` | count | ≥ 0 | as above |
| `mtf_timeframes_available_count` | count | 0–2 | **never missing** |

**On a 1D row every `h4_*` and `d1_*` feature is `None`**, with
`*_unavailable_reason_code = 1` (`no_higher_timeframe`). The row shape is identical across
timeframes; only the values differ. That is what lets a single schema serve all three.

**`_bars_since_break_htf_bars` carries its unit in its name** for the reason R2-11 §5.4
gives: on a 1H row, "bars since the 4H break" is ambiguous unless the name says which bars.

### 5.4 Group 14 — data quality, R2-08 coverage (6)

| Feature | Unit | Range | Missing means |
|---|---|---|---|
| `bar_quality_code` | code | 0–3 | no coverage report supplied |
| `bar_gap_cause_code` | code | 0–3 | as above |
| `bar_coverage_ratio` | ratio | `[0, 1]` | as above |
| `bar_missing_observations` | count | ≥ 0 | as above |
| `bar_longest_missing_run` | count | ≥ 0 | as above |
| `bar_undetermined_observations` | count | ≥ 0 | as above |

**These are features, not filters.** The brief asks for *"explicit quality features rather
than silently hiding degraded observations"*, and this is that: a `DEGRADED_UNKNOWN` bar
produces an ordinary, complete row whose degradation is a *column*. A model can learn that
such rows behave differently; a filtered dataset cannot express the question.

`bar_coverage_ratio` is projected **and is not a threshold**. No row is excluded on it,
here or anywhere (master story §5.1).

---

## 6. Normalisation

> **No scaler is fitted anywhere in R2-13.** Master story §6.5.

| Kind of feature | How it is scaled | Why it is safe |
|---|---|---|
| Distances | instrument **points** | A fixed instrument property, not a data-derived statistic |
| Counts | raw integers | No scaling |
| Ratios | dimensionless by construction | Both numerator and denominator known at `T` |
| `cot_index_*`, `cot_percentile_*` | rolling window over **observable reports only** | R2-10 §8; warm-up is `None`, and `cot_reports_in_history` shows the sample size |
| Everything else | **not scaled** | — |

**No z-scores. No min-max. No quantile transforms. No per-split fitting.** Feature scaling
is a modelling decision; it belongs to Phase 4, where it is recorded in an experiment and
fitted on train alone.

R2-13's **deliberately broken implementation (L8)** is precisely the tempting version: compute a
z-score for `cot_noncommercial_net` over the whole dataset. It must be built and proven to
differ from the (absent) point-in-time alternative, so the reason for the absence is
demonstrated rather than asserted.

---

## 7. Serialisation and column order

```
FEATURE_NAMES          the V1 schema -- unchanged, still exported, still 56
FEATURE_NAMES_V2       the V2 schema -- FEATURE_NAMES + 71 appended names
FEATURE_VERSION        "r2-13.1"
column_names()         returns FEATURE_NAMES_V2      <- what consumers must call
as_dict()              identity columns, then FEATURE_NAMES_V2 order; missing -> None
as_row()               floats in FEATURE_NAMES_V2 order; missing -> math.nan
from_dict(as_dict())   round-trips EXACTLY, including None
vectors_to_frame()     a DataFrame in the same order, every time
```

`FEATURE_NAMES_V2` is declared **explicitly as a literal tuple**, not derived from the
dataclass fields — the existing rule, and there is already a test asserting the module does
not build it with a comprehension over `fields()`. That test is extended to the v2 tuple.

### 7.1 The two R2-08 call sites — the only change outside `ict/`

| File | Lines | Change |
|---|---|---|
| [`features/dataset.py`](../../ict_kronos/features/dataset.py) | 30, 269, 282, 305 | `FEATURE_NAMES` → `ICTFeatureVector.column_names()` |
| [`features/audit.py`](../../ict_kronos/features/audit.py) | 21, 147, 148, 157 | same |

**Left undone, `rows_to_frame` and `audit_dataset` silently emit a 56-column frame from a
127-column vector.** No error, no warning — just a smaller dataset. A regression test
asserts `rows_to_frame` returns `len(FEATURE_NAMES_V2)` feature columns and that
`audit_dataset().feature_count == 127`.

`DATASET_SCHEMA_VERSION` stays **`r2-08.1`**: the row *shape* is unchanged. The row already
records `feature_schema_version` separately, and it now records `r2-13.1`. That is exactly
what carrying three versions on a row was designed to do, and it is worth stating so nobody
"helpfully" bumps the dataset version too.

`STATE_VERSION` is `r2-12.1` (R2-12) and `feature_version` is `r2-13.1` (this story) — two
different numbers, deliberately, because the state and the projection are versioned
independently.

---

## 8. Leakage contract

R2-13 has **no future to reach for** — it reads a state that was already built
point-in-time. The proofs are therefore about *proving that property still holds*, not
about discovering a leak in new arithmetic.

| # | R2-13 instantiation |
|---|---|
| **L1** | **No future bars.** No information after `as_of` affects any of the 127 values |
| **L2** | **Future OHLC mutation.** Mutate every bar after `as_of`; all values byte-identical |
| **L3** | **Dependency declared** per feature, inherited from the composed layers; **no new column may join the wick-dependent set** without declaring it |
| **L4** | **Point-in-time lifecycle.** Every feature reads a state that was already built point-in-time; the vector performs no timestamp comparison of its own |
| **L5** | **Prefix equivalence** at every instant, for **vectors as well as states** |
| **L6** | **Identity stability.** No `*_id` column exists in `FEATURE_NAMES_V2`; column *positions* are invariant — `FEATURE_NAMES_V2[:56] == FEATURE_NAMES` |
| **L7** | **External inputs.** Mutate future COT reports and future HTF bars; the vector is byte-identical |
| **L8** | **Non-vacuous control.** Mutate past bars / past COT / past HTF — the vector **must change**. Run against the incorrect implementation (**inserting new features before the existing 56**), which must **fail** the compatibility invariant |

**Provenance integrity** is contracted in §8.1's marker-substitution sweep.

Plus the structural guards: no frame access, no timestamp comparison, no detector call, no
model-training import (`xgboost`, `lightgbm`, `sklearn`, `torch`, `optuna`, `kronos` — the
existing R2-08 guard list, extended to this module).

### 8.1 The per-feature sweep

A parametrised test over **all 127 names**: for each, mutate the state field it derives from
and assert the feature changes; mutate an unrelated field and assert it does not.

This is the marker-substitution technique that caught the R2-07 `source_ids()` gap — *"a
value-based test is not one… the test stamps each field with a unique marker and asks
whether the FIELD is read"*. Applied per feature, it makes a copy-paste error in
`from_state` (reading `nearest_buy_side_pool_points` into the sell-side column) impossible
to miss.

---

## 9. Streaming, identity and data quality

**Streaming.** `batch == prefix == bar-by-bar` for vectors, with exactly the inherited
asymmetries (local and HTF True Daily Open, R2-12 §10). R2-13 introduces none.

**Identity.** The vector carries **no ids** — it is the flat numeric projection, and
provenance lives on the state and on `DatasetRow.feature_provenance`. That is deliberate and
unchanged: a test asserts no `*_id` column exists in `FEATURE_NAMES_V2`.

**Data quality.** Group 14 (§5.4). Describes, never filters.

**`NaN`.** Exists in `as_row()` and nowhere else. A `NaN` reaching a *field* breaks
`from_dict(as_dict()) == v` and would report a phantom streaming difference (R2-07 audit
defect 1). Every new numeric field is `float | None`, never `float` with a `NaN`.

---

## 10. Ambiguity register

| # | Ambiguity | Interpretations | Chosen | Why | Kind |
|---|---|---|---|---|---|
| **E1** | Extend, parallel class, or replace | three | **Extend, append-only** | §2; the invariant is one tuple comparison | **Engineering** |
| **E2** | Which of the candidate features to include | all · curated | **Curated — 69 of ~140 candidates**, each rejection recorded in the concept map | The set covers each new layer once, without ambiguous or hypothesis-bearing columns | **Engineering** |
| **E3** | Whether to rescale ratios/indices to a common range | yes · no | **No** | Rescaling needs a target range this layer has no basis to choose; each column's range is documented instead | **Engineering** |
| **E4** | Whether HTF features should be per-timeframe columns or one long-format table | wide · long | **Wide (`h4_*`, `d1_*`)** | Tree models take a fixed wide row; long format would change the dataset's grain and break R2-08's one-row-per-instant contract | **Engineering** |
| **E5** | Whether data-quality columns belong in the feature vector at all | features · metadata · filter | **Features** | The brief asks for explicit quality features; a filter cannot express "degraded rows behave differently" | **Engineering** |
| **E6** | Whether raw COT contract counts belong beside the ratios | ratios only · both | **Both** | Ratios without their primitives are unauditable. A model may ignore the raw columns | **Engineering** |
| **E7** | Whether `unknown` should get its own integer in the new categoricals | reserve one · use `None` | **`None`** | Every integer in these tables is a real category; reusing one for absent makes them indistinguishable. The existing `bias_code` collapse stays the sole exception and is **not** extended | **Engineering** |
| **E8** | Whether `bar_quality_code` is ordinal | ordinal · nominal | **Nominal, documented** | `boundary_incomplete` is not "worse than" `degraded_unknown` on any supported scale | **Engineering** |
| **E9** | Whether to bump `DATASET_SCHEMA_VERSION` | yes · no | **No — stays `r2-08.1`** | The row shape is unchanged; the row already records `feature_schema_version` separately, which is what changes | **Engineering** |

---

## 11. Files

### Modified

| File | Change |
|---|---|
| `ict_kronos/ict/feature_vector.py` | 71 new fields, 6 new code tables, `FEATURE_NAMES_V2`, `column_names()` → v2, `FEATURE_VERSION → r2-13.1`, `from_state` extended |
| `ict_kronos/features/dataset.py` | §7.1 — 4 lines |
| `ict_kronos/features/audit.py` | §7.1 — 4 lines |
| `ict_kronos/ict/__init__.py` | Export `FEATURE_NAMES_V2` and the new tables |
| `docs/ict/features.md` | Extended to 127 features, with the v1/v2 boundary marked |
| `docs/features/dataset.md`, `docs/features/README.md` | Version references |
| `docs/dev/HANDOFF.md`, `tasks/README.md` | Status |

### New

`tests/test_feature_vector_v2.py` · `tests/test_feature_vector_v2_leakage.py` ·
`tests/test_feature_vector_v1_compatibility.py` · `tests/test_dataset_v2_columns.py`

### MUST NOT change

`ict_kronos/ict/market_state.py` (R2-12 finished it) · every detector ·
`liquidity_model.py`, `mtf.py`, `data/cot/` · `features/targets.py` ·
`features/splits.py` · `features/production.py`.

**Existing tests that WILL need edits, and only these:**

| Test | Edit | Why it is legitimate |
|---|---|---|
| `tests/test_feature_vector.py:84` | `len(FEATURE_NAMES) == 56` → keep, and add `len(FEATURE_NAMES_V2) == 127` | The v1 assertion **must survive unchanged** — it is the compatibility proof |
| `tests/test_dataset.py:372-373` | `feature_count == len(FEATURE_NAMES)` → `column_names()` | The audit now reports 127 |
| `tests/test_dataset.py:302, 308` | column-order assertions → v2 tuple | Same |
| `tests/test_market_state_real_data.py:223-224, 275` | `FEATURE_NAMES` → `column_names()` | Same |

Every other existing test passes **unmodified**. Any test whose edit is not in this table is
a signal that the change was not append-only, and the story stops until that is explained.

---

## 12. Real-data validation

EURUSD and XAUUSD × 1H/4H/1D, on `production-native-2026-02_08` (six months, DST included).

| Assertion | Detail |
|---|---|
| Column count | `len(vector.as_row()) == 127` on every real vector |
| v1 prefix | `as_row()[:56]` equals the v1 vector's row for the same state, **element for element**, on every real instant |
| Round trip | `from_dict(as_dict()) == v` on every real vector, both symbols, all three timeframes |
| 1D behaviour | Every `h4_*`/`d1_*` feature `None`; `*_unavailable_reason_code == 1`; `distance_from_true_daily_open_points` still `None` (the inherited Daily discrepancy) |
| Missing ≠ zero | For every feature whose "missing means" is non-trivial, at least one real instant where it is `None` **and** at least one where it is a real `0`. If a feature never produces one of the two, the completion report says so |
| Constant features | `audit_dataset` reports them. **Reported, never dropped** |
| Dataset frame | `rows_to_frame` emits 127 feature columns in `FEATURE_NAMES_V2` order |
| Degraded bars | At least one `DEGRADED_UNKNOWN` bar in the month produces a complete row with the quality visible in `bar_quality_code` |

The completion report states, per symbol × timeframe: the missing-rate of every new
feature, which are constant, and the COT warm-up coverage. **Description only** — no
feature is selected, dropped or ranked by usefulness. `audit.py`'s guard test already bans
selection, imputation and correlation, and it extends to the new columns.

---

## 13. Definition of done, and the hard stop

1. R2-12 complete and approved
2. Every task in [R2-13-TASKS.md](../../tasks/Phase-2-ICT-Engine/R2-13-TASKS.md) ✅
3. `FEATURE_NAMES_V2[:56] == FEATURE_NAMES` — asserted
4. `as_row()[:56]` equals the v1 row on **every** real instant, both symbols, all three
   timeframes
5. Only the four test edits in §11 were needed; every other existing test passes unmodified
6. The whole-dataset z-score naive implementation built and proven to differ
7. The 127-feature marker-substitution sweep green
8. `rows_to_frame` and `audit_dataset` emit 127 columns
9. `pytest -q` green; `ruff` and `black` clean; no silent skip
10. Performance measured: vector construction per row, and end-to-end dataset build per
    symbol × timeframe on a fresh month
11. `docs/ict/features.md` extended to 127; HANDOFF updated in the same commit
12. One local commit. **No push.**

```
=> R2-13 complete
=> audit
=> completion report
=> STOP
=> Phase 2 Market Intelligence complete; Phase 4 requires separate approval
```

**No model training follows automatically.** The output of this phase is a dataset
contract, exactly as R2-08's was: *rows, targets, a split definition and a quality report
that a later phase can use and can audit.* Whether any of it is predictive is unanswered,
and answering it is a separate, approved phase.
