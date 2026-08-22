# R2-13 — Feature Vector v2 — tasks

Story: [R2-13-FEATURE-VECTOR-V2-STORY.md](../../docs/features/R2-13-FEATURE-VECTOR-V2-STORY.md) ·
Concept map: [R2-13-CONCEPT-MAP.md](../../docs/features/R2-13-CONCEPT-MAP.md) ·
Master: [Phase-2-Market-Intelligence-STORY.md](../../docs/Phase-2-Market-Intelligence-STORY.md)

**Status: SPECIFIED — awaiting approval. No code written.**
Modifies `ict_kronos/ict/feature_vector.py` **append-only**. `FEATURE_VERSION`: `r2-07.1` →
`r2-13.1`. **56 → 127 columns.**

## Prerequisites

| # | Prerequisite | Status |
|---|---|---|
| 1 | R2-12 complete and approved | ⛔ **BLOCKING** |
| 2 | Explicit approval of this story | ⛔ **BLOCKING** |

## Tasks

| ID | Task | Notes | Status |
|---|---|---|---|
| R2-13-a | `FEATURE_NAMES_V2` declared as an **explicit literal tuple**, never derived from `fields()` | The existing test banning a comprehension is extended to the v2 tuple | ⬜ |
| R2-13-b | **`FEATURE_NAMES_V2[:56] == FEATURE_NAMES`** — asserted | **The story's central guarantee, in one line** | ⬜ |
| R2-13-c | `FEATURE_NAMES` kept exported and unchanged as the **v1** schema | Never redefined to mean "current", which would make the invariant unstateable | ⬜ |
| R2-13-d | `column_names()` returns `FEATURE_NAMES_V2` | The accessor every consumer must call | ⬜ |
| R2-13-e | Group 11 — liquidity pools **and significance components**, **26** columns | Story §5.1. The composite `significance_score` is **NOT** projected — a weighted sum is a hypothesis | ⬜ |
| R2-13-f | Group 12 — **Normalized** COT, **16** columns — one normalised column set, family as provenance | Story §5.2. **Legacy EXCLUDED.** A role absent in a family is `None`, never filled from another role | ⬜ |
| R2-13-g | Group 13 — multi-timeframe, **23** columns (10 × `h4_`, 10 × `d1_`, 3 cross) | Story §5.3. `_bars_since_break_htf_bars` carries its unit in its name | ⬜ |
| R2-13-h | Group 14 — data quality, **6** columns | Story §5.4. **Features, not filters** | ⬜ |
| R2-13-i | Six new code tables as module constants, never fitted from data | `POOL_ZONE_CODES`, `MTF_ALIGNMENT_CODES`, `BAR_QUALITY_CODES`, `GAP_CAUSE_CODES`, `COT_UNAVAILABLE_CODES`, `MTF_UNAVAILABLE_CODES` | ⬜ |
| R2-13-j | `COT_UNAVAILABLE_CODES` starts at `1`, leaving `0` unused | So "available" is unambiguously the **absence** of a reason | ⬜ |
| R2-13-k | Every new categorical uses `None` for unavailable; the `bias_code` collapse is **not extended** | Every integer in these tables is a real category | ⬜ |
| R2-13-l | `bar_quality_code` / `bar_gap_cause_code` documented as **nominal, not ordinal** | `boundary_incomplete` is not "worse than" `degraded_unknown` on any supported scale | ⬜ |
| R2-13-m | A `None` v2 context ⇒ **every derived feature `None`**, never `0`, never a default | The tri-state rule at column level | ⬜ |
| R2-13-n | **No scaler anywhere.** Points, counts, dimensionless ratios; ranges documented, not harmonised | Story §6 | ⬜ |
| R2-13-o | `FEATURE_VERSION → r2-13.1` | | ⬜ |
| R2-13-p | **`features/dataset.py`: 4 lines** — `FEATURE_NAMES` → `ICTFeatureVector.column_names()` | Lines 30, 269, 282, 305. **Left undone, `rows_to_frame` silently emits 56 columns from a 127-column vector** | ⬜ |
| R2-13-q | **`features/audit.py`: 4 lines** — same | Lines 21, 147, 148, 157 | ⬜ |
| R2-13-r | `DATASET_SCHEMA_VERSION` stays **`r2-08.1`** | The ROW shape did not change. The row already records `feature_schema_version` separately — which is exactly what carrying three versions was designed for | ⬜ |
| R2-13-s | Serialisation: `as_dict()`, `as_row()`, exact `from_dict` round trip including `None` | | ⬜ |
| R2-13-t | **`as_row()[:56]` equals the v1 row element for element**, on every real instant | The compatibility proof on real data | ⬜ |
| R2-13-u | **The 127-feature marker-substitution sweep** | Mutate the state field a feature derives from ⇒ it changes; mutate an unrelated field ⇒ it does not. With 71 new fields arriving in near-identical buy/sell pairs, a copy-paste error is close to certain without it | ⬜ |
| R2-13-v | **Leakage L1 … L8** (master §6.3); **L8**: the whole-dataset z-score built, and L1/L2/L4 proven to **fail** against it | So §6's absence is *demonstrated*, not asserted | ⬜ |
| R2-13-w | Guard tests | No frame access · no timestamp comparison · no detector call · no `*_id` column · no model-training import (`xgboost`, `lightgbm`, `sklearn`, `torch`, `optuna`, `kronos`) | ⬜ |
| R2-13-x | Regression: `rows_to_frame` emits 127 feature columns; `audit_dataset().feature_count == 127` | | ⬜ |
| R2-13-y | **Only the four test edits of story §11 were needed**; every other existing test passes unmodified | Any edit outside that table means the change was not append-only | ⬜ |
| R2-13-z | Real-data: both symbols × 1H/4H/1D, `production-native-2026-02_08` (six months, DST included) | 8 assertions, story §12 | ⬜ |
| R2-13-1 | Missing ≠ zero: for every feature with a non-trivial "missing means", at least one real instant `None` **and** one real `0` — or the report says so | | ⬜ |
| R2-13-2 | Constant features **reported, never dropped**; `audit.py`'s ban on selection/imputation/correlation extends to the new columns | | ⬜ |
| R2-13-3 | Performance: vector construction per row; end-to-end dataset build per symbol × timeframe on a fresh month | | ⬜ |
| R2-13-4 | Documentation: `docs/ict/features.md` extended to 127 with the v1/v2 boundary marked; HANDOFF in the same commit | | ⬜ |
| R2-13-5 | Full suite + ruff + black; one local commit; **STOP** | No push | ⬜ |

## Decisions that change what is being predicted

1. **Append-only. Never reordered, never regrouped.** A logically regrouped 127-column schema
   is genuinely tidier and would silently renumber every existing dataset. A v1-trained model
   applied to v2 would read `bos_count` where it expects `close`, produce plausible numbers,
   and be wrong. **Tidiness never justifies a silent failure mode.**
2. **`FEATURE_NAMES` stays the v1 schema; `FEATURE_NAMES_V2` is the current one.**
3. **No scaler, anywhere.** Not global, not per-split. Scaling is a Phase 4 decision recorded
   in an experiment; a dataset carrying a train-fitted scaler cannot be re-split without
   being rebuilt, which breaks Phase 8's walk-forward validation.
4. **`cot_index_*` / `cot_percentile_*` are point-in-time statistics, not fitted scalers** —
   rolling over observable reports only, warm-up `None`, inputs provable via
   `history_report_ids`.
5. **Ranges are documented, not harmonised.** Rescaling needs a target range this layer has
   no basis to choose.
6. **Raw COT contract counts ship beside the ratios.** Ratios without their primitives are
   unauditable.
7. **Data quality is a feature group.** A filtered dataset cannot express "degraded rows
   behave differently".
8. **`DATASET_SCHEMA_VERSION` does not move.**

## Not implemented, and why

| Item | Reason |
|---|---|
| Scalers, imputation, class balancing | Modelling decisions; Phase 4 |
| Feature selection or ranking | `audit.py`'s guard already bans it, and it would bake an unmeasured hypothesis into the data |
| A logically regrouped schema | Decision 1 |
| A separate `ICTFeatureVectorV2` class | Two of everything; the compatibility claim becomes a mapping that rots |
| All 56 local features per HTF | 112 columns with ambiguous units |
| `cot_positioning_extreme` and similar thresholds | A threshold **is** a hypothesis |
| Rolling means / composite ratios of ratios | A lookback is a modelling choice; a sequence model reads the sequence |
| `*_id` columns | Provenance lives on the state and on `DatasetRow.feature_provenance` |

## Deliverables (every story, no exceptions)

| # | Deliverable |
|---|---|
| 1 | Implementation |
| 2 | Tests — unit · boundary · leakage · provenance · identity/collision · real-data · serialisation · guard/contract · streaming/point-in-time |
| 3 | Documentation |
| 4 | Completion report |
| 5 | Performance measurements |
| 6 | **Leakage matrix** — one row per L-ID × component, **no blank cells**; every `n/a` carries a reason |
| 7 | **Provenance matrix** — one row per emitted id field: id kind, registry it resolves against, observable-by check |
| 8 | Real-data results |
| 9 | Limitations and ambiguities |
| 10 | Git status and commit information |

**If provider reality conflicts with this specification, STOP and report the conflict.
Do not invent data and do not weaken the rule.**

## Hard stop — end of phase

```
R2-13 complete -> audit -> completion report -> COMMIT (local)
               -> STOP
               -> Phase 2 Market Intelligence complete
               -> Phase 4 requires SEPARATE approval
```

**No model training follows automatically.** The output of this phase is a dataset contract,
exactly as R2-08's was: rows, targets, a split definition and a quality report that a later
phase can use *and can audit*.

**No feature in this vector is claimed to be predictive.** Whether this representation
carries information is exactly what Phases 4 and 6 exist to answer. R2-13 makes the question
askable; it does not answer it, and a rigorous negative answer remains a successful outcome.
