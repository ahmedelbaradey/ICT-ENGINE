# R2-07 — ICT feature integration (`ICTMarketState` / `ICTFeatureVector`)

- **Project:** ICT-Kronos
- **Phase:** Phase 2 — ICT Engine
- **Epic:** Deterministic ICT representation
- **Issue type:** Story
- **Story points:** 8
- **Labels:** `ict`, `features`, `leakage`, `mtf`
- **Depends on:** R2-01 … R2-06
- **Blocks:** Phase 3 (feature dataset), Phase 4 (baseline models)

## Description

As a quantitative researcher, I want the six detectors combined into one canonical, point-in-time market state and a flat feature vector, so that Phase 4 models can consume ICT information without any component re-deriving it — or re-introducing leakage.

This story is where §22's Model C (OHLCV + ICT) becomes possible, and therefore where the project's central research question becomes answerable.

## Scope

- `ICTMarketState` — the full structured state at an instant
- `ICTFeatureVector` — a flat, numeric, model-ready projection
- Multi-timeframe assembly using the existing `align_htf_context()`

## Acceptance criteria

1. Combines `SessionDetector`, `SwingDetector`, `StructureDetector`, `LiquidityDetector`, `FVGDetector` and `PremiumDiscountCalculator` — no detector logic is reimplemented here.
2. **Every feature has a clearly defined observation timestamp**, and the vector at time *t* is computable using only information whose `confirmation_timestamp <= t`.
3. The feature vector is **numeric and flat** — suitable as-is for XGBoost, LightGBM and Logistic Regression, and shaped so Kronos features can be concatenated later without restructuring.
4. Categorical values (session, zone, direction) use a documented, stable encoding. Missing/not-yet-observable values are explicit (NaN or a documented sentinel), **never silently zero** — zero is a real price-distance value and must not double as "unknown".
5. Multi-timeframe context uses **`align_htf_context()` only** — the existing single alignment helper. No new join path is introduced (CLAUDE.md rule 1).
6. **Every feature is documented** in `docs/ict/features.md`: name, meaning, units, range, observation timing, and missing-value semantics.
7. Feature-set output is versioned as `feature_version` so a dataset can be tied to the exact feature definitions that produced it (§29).
8. **No raw future information is included**, including via the target — labelling is Phase 3's concern and is kept strictly separate from feature assembly.
9. **LEAKAGE CRITERION (mandatory):** a whole-vector test proves that for every row, no contributing value has a `confirmation_timestamp` later than the row's observation timestamp; plus batch vs streaming replay on the full vector.

## Test coverage required

- Vector assembly with all detectors active
- Missing/not-yet-observable components early in a series
- Multi-timeframe assembly (5M observation with 15M/1H context)
- Encoding stability (same input → same column order and dtypes)
- Batch vs streaming replay for the whole vector
- Whole-vector leakage test
- Real data: EURUSD + XAUUSD 2024-03-08 → 2024-03-12

## Notes and decisions

- The vector is a **projection**, not a store: `ICTMarketState` stays the rich structured truth, and the flat vector is derived from it, so a model-shaped compromise never degrades the underlying representation.
- **No trading claim is made here.** This story produces a representation; whether it carries predictive information is the question Phases 4 and 6 exist to answer.

## Out of scope

Target/label definition (Phase 3), model training (Phase 4), Kronos fusion (Phase 5/6), setup-quality scoring.
