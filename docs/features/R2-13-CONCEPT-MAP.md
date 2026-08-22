# R2-13 — Feature Vector v2 — CONCEPT MAP

**Specification checkpoint. Written before any change to `feature_vector.py`.**
Story: [R2-13-FEATURE-VECTOR-V2-STORY.md](R2-13-FEATURE-VECTOR-V2-STORY.md)

---

## 1. The shape of the problem

R2-13 computes nothing that could leak — it reads a state built point-in-time, so *"there is
no future for it to reach"*.

**All of the risk is in schema discipline:**

| Risk | Failure |
|---|---|
| Column order | Insert one column in the middle and every existing dataset's column *k* now means something else. Nothing errors |
| Silent column loss | `rows_to_frame` imports `FEATURE_NAMES` directly. Add 71 features and it emits 56 columns — no error, no warning, just a smaller dataset |
| Normalisation creep | A z-score "for the model's convenience" fitted over the dataset is a leak wearing a preprocessing step's clothes |
| Missing-value drift | One new field emitting `0` for absent undoes the engine's most carefully-held rule |

R2-13 is a **schema-discipline problem wearing a feature-engineering problem's clothes.**

---

## 2. Dependency graph

```
ICTMarketState v2 (R2-12) ──► ICTFeatureVector v2 ──► DatasetRow (R2-08, two call sites change)
      │                              │
      └── enums only ────────────────┘
          (LiquidityType, PoolZone, CotUnavailableReason,
           MtfUnavailableReason, BarQuality, GapCause)
```

The module imports **enums and a state**, never a frame, a detector, a provider or a
resampler. A guard test asserts it.

---

## 3. v1 compatibility — four candidates

### K1 — Append-only, one class, two name tuples *(SELECTED)*

```python
FEATURE_NAMES_V2[:len(FEATURE_NAMES)] == FEATURE_NAMES
```

| | |
|---|---|
| ✅ | **The compatibility claim is one tuple comparison.** Not a mapping, not a convention, not a docstring — a test |
| ✅ | `as_row()[:56]` is bit-identical to a v1 row for the same state |
| ✅ | A v1-trained model applies to a v2 dataset by taking the first 56 columns |
| ✅ | One `from_state`, one `as_row`, one round-trip suite |
| ⚠️ | New features can only ever be appended, so the file's field order and the schema tuple diverge cosmetically over time. Accepted — `FEATURE_NAMES` is already declared explicitly *"rather than derived from the dataclass fields, so reordering a field cannot silently renumber an existing dataset's columns"* |

**Verdict: selected.**

### K2 — A separate `ICTFeatureVectorV2` class

❌ Two `from_state`s, two `as_row`s, two round-trip suites, and every consumer branches. The
compatibility claim becomes a hand-maintained mapping that rots — exactly what K1's one-line
invariant avoids.

### K3 — Regroup all 127 columns logically (liquidity features together, etc.)

❌ **The most tempting and the most damaging.** It produces a tidier schema and silently
renumbers every existing dataset's columns. A model trained on v1 applied to v2 would read
`bos_count` where it expects `close`, produce plausible numbers, and be wrong. Tidiness is
not worth a silent failure mode.

### K4 — Version the vector by content hash rather than a name tuple

❌ Detects change but does not *prevent* it, and gives no compatibility guarantee at all — it
would only tell you, afterwards, that you had broken something.

---

## 4. Feature selection — what was cut from ~110 candidates to 71

The brief asks that not everything be included. The interesting cuts:

| Candidate | Why cut |
|---|---|
| **All 56 local features duplicated per HTF** (112 columns) | Most are counts whose HTF unit is ambiguous — "4H `bars_since_cisd`" on a 1H row is 4H bars or 1H bars, and the name says neither. Ten curated fields per HTF instead, with `bars_since_break_htf_bars` carrying its unit **in the name** |
| **A "liquidity pressure" or "pool strength" score** | No source defines one. The raw material a score would need — distance, cardinality, member types, internal/external — is all exposed, so Phase 4 can test any score against a baseline |
| **`cot_positioning_extreme`** (`index > 80`) | A threshold **is** a hypothesis. `cot_index_*` carries the full information | 
| **z-scored / min-max / quantile versions of anything** | Requires fitting a scaler. §6 of the story; and it is R2-13's deliberately broken implementation (L8) |
| **Ratios of ratios** (e.g. `liquidity_asymmetry / mtf_alignment`) | Composite arithmetic a model can do itself, with no representational content of its own |
| **`*_id` columns** | The vector deliberately carries no ids — provenance lives on the state and on `DatasetRow.feature_provenance`. A test asserts no `*_id` appears in `FEATURE_NAMES_V2` |
| **Rolling means of existing features** | A lookback window is a modelling choice; and a sequence model reads the sequence directly |

### 4.1 What was kept despite an argument against it

| Kept | The argument against | Why kept anyway |
|---|---|---|
| Raw COT contract counts beside the ratios | Not comparable across instruments | They are the primitives the ratios are computed from. Ratios without their primitives are unauditable. A model may ignore them; a reviewer cannot |
| Data-quality columns | "Metadata, not features" | The brief asks for explicit quality features *"rather than silently hiding degraded observations"*. A filtered dataset cannot express "degraded rows behave differently" |
| `cot_*` on 1H rows despite ~120 identical consecutive rows | Near-constant within a week | That repetition is a **real property of the data**, not a defect. `cot_report_age_days` makes it legible. Hiding it — by restricting COT to daily rows — would be a silent transformation |

---

## 5. Missing values — three candidates

| # | Candidate | Verdict |
|---|---|---|
| M1 | **`None` in `as_dict()`, `math.nan` in `as_row()`, real zeros stay zeros** | ✅ **selected** — the existing rule, extended verbatim to all 71 new columns |
| M2 | A reserved sentinel integer per categorical | ❌ *"Every integer in these tables is a real category, and reusing one for 'missing' would make the two indistinguishable to a model"* |
| M3 | Impute | ❌ Forbidden. `audit.py` already ships a guard test banning selection, imputation and correlation, and it extends to the new columns |

**The one existing exception is not extended.** `bias_code` collapses `unknown` and `neutral`
to `0` — *"the one place the projection is deliberately lossier than the truth"* — and every
new categorical uses `None` for unavailable instead.

`COT_UNAVAILABLE_CODES` deliberately starts at `1`, leaving `0` unused, so "available" is
unambiguously the *absence* of a reason rather than a reason with value zero.

---

## 6. Normalisation — three candidates

| # | Candidate | Verdict |
|---|---|---|
| N1 | **None. Points, counts, and dimensionless ratios; no scaler anywhere** | ✅ **selected** |
| N2 | Fit a scaler on the train split | ❌ Correct *practice* — in Phase 4, where it is recorded in an experiment. In a representation layer it silently ties the dataset to one split definition |
| N3 | Fit a scaler on the whole dataset | ❌ **The leak.** This is L4 |

N2 is worth naming carefully: it is not wrong, it is **in the wrong place**. A dataset
carrying a train-fitted scaler cannot be re-split without being rebuilt, which breaks the
walk-forward validation Phase 8 exists to do.

**The one exception, and why it is not one:** `cot_index_*` and `cot_percentile_*` *are*
normalised — over a rolling window of **observable reports only**, with an explicit warm-up
of `None` and `cot_reports_in_history` published beside them. That is a point-in-time
statistic, not a fitted scaler: nothing is stored, nothing is reused across rows, and every
input is provable via `history_report_ids`.

---

## 7. Ranges are documented, not harmonised — two candidates

The 71 new columns span at least four scales: points (ℝ), counts (ℕ), ratios (`[−1,1]` or
`[0,1]`), and an index (`[0,100]`).

| # | Candidate | Verdict |
|---|---|---|
| R1 | **Document each column's range; rescale nothing** | ✅ **selected.** Rescaling requires choosing a target range, and this layer has no basis for that choice. Tree models are scale-invariant; anything else can scale in Phase 4 |
| R2 | Rescale everything to `[0,1]` or `[−1,1]` | ❌ Needs per-column min/max — which for an unbounded column means a fitted statistic, i.e. N3 |

---

## 8. Leakage criteria inherited

L1 … L8 from the master story §6.3. **The deliberately broken implementation (L8):**

> Compute a z-score for `cot_noncommercial_net` over the whole dataset and use it as a
> feature.

Built and proven to differ, so the *reason for §6's absence is demonstrated rather than
asserted*.

### 8.1 The 127-feature marker-substitution sweep

A parametrised test over **every** name: mutate the state field the feature derives from and
assert the feature changes; mutate an unrelated field and assert it does not.

This is the R2-07 audit technique applied per column:

> *"A provenance enumeration is only as good as its coverage test, and a value-based test is
> not one… stamps each field with a unique marker and asks whether the FIELD is read — not
> whether the value appears somewhere."*

It makes the classic copy-paste error — `from_state` reading
`nearest_buy_side_pool_points` into the sell-side column — impossible to miss. With 71 new
fields, most of them arriving in near-identical buy/sell pairs, that error is close to
certain without the sweep.

---

## 9. The silent-column-loss trap

`rows_to_frame` and `audit_dataset` both import `FEATURE_NAMES` **directly from the module**:

```python
from ..ict import FEATURE_NAMES          # dataset.py:30, audit.py:21
```

With 127 features and an unchanged import, both emit **56 columns**. No error. No warning.
Just a dataset missing every new feature — which looks exactly like a dataset where the new
features happened not to be enabled.

The fix is mechanical (`ICTFeatureVector.column_names()`, which already exists) and the
regression tests are explicit: `rows_to_frame` returns `len(FEATURE_NAMES_V2)` feature
columns, and `audit_dataset().feature_count == 127`.

**This is the single highest-consequence, lowest-visibility change in the story**, which is
why it is called out in the master story (§7.3), in the story (§7.1) and here.

---

## 10. Version discipline

```
STATE_VERSION            r2-07.1 -> r2-12.1     (R2-12; the state shape changed)
FEATURE_VERSION          r2-07.1 -> r2-13.1     (R2-13; the column set changed)
DATASET_SCHEMA_VERSION   r2-08.1 -> r2-08.1     (UNCHANGED; the ROW shape did not change)
TARGET_SCHEMA_VERSION    r2-08.1 -> r2-08.1     (UNCHANGED)
SPLIT_SCHEMA_VERSION     r2-08.1 -> r2-08.1     (UNCHANGED)
```

`DATASET_SCHEMA_VERSION` staying put is worth stating so nobody "helpfully" bumps it: a
`DatasetRow` records `feature_schema_version` *separately*, which is exactly what carrying
three versions on a row was designed for. Bumping the dataset version too would assert a row
shape change that did not happen.

---

## 11. Ambiguity register

Full register in [the story](R2-13-FEATURE-VECTOR-V2-STORY.md) §10 (E1 … E9). The decision
most likely to be questioned:

| Decision | Why it stands |
|---|---|
| **Append-only, never regrouped** | A logically regrouped 127-column schema is genuinely tidier and would silently renumber every existing dataset. The failure mode — a v1-trained model reading the wrong column and producing plausible numbers — is invisible in every automated check that does not compare against v1 directly. Tidiness never justifies a silent failure |

---

## 12. What R2-13 does not build

Scalers · imputation · feature selection · class balancing · rolling aggregates · composite
ratios of ratios · id columns · a regrouped schema · a second vector class · any change to
`market_state.py`, any detector, `targets.py`, `splits.py` or `production.py` · any ML,
probability or label · any backtest rule · **any claim that any of these 127 columns carries
information**. Whether it does is exactly what Phases 4 and 6 exist to answer; R2-13 makes
the question askable and does not answer it.
