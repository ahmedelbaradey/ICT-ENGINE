# R2-08 — Prediction Target & Dataset Engine — tasks

Docs: [targets.md](../../docs/features/targets.md) ·
[dataset.md](../../docs/features/dataset.md) ·
[splits.md](../../docs/features/splits.md) ·
[package README](../../docs/features/README.md)

**Status: ready for review.** New package `ict_kronos/features/` —
`targets.py`, `dataset.py`, `splits.py`, `audit.py`.
`target_schema_version` / `dataset_schema_version` / `split_schema_version` = `r2-08.1`.

R2-08 is the **hard gate before model training**. Its output is a dataset contract, not
a model: rows, targets, a split definition and a quality report that a later phase can
use *and can audit*.

| ID | Task | Notes | Status |
|---|---|---|---|
| R2-08-a | Explicit, versioned, serialisable target specifications | `TargetSpec`; every convention named in [targets.md](../../docs/features/targets.md) §2 | ✅ |
| R2-08-b | `future_return` with a declared reference-price convention | Close-to-close; reference is the close of the observed bar | ✅ |
| R2-08-c | `DIRECTION` with configured, never-fitted thresholds | Points, `>=` boundary, zero-threshold degeneracy documented | ✅ |
| R2-08-d | MFE / MAE without a hidden trade side | Up and down excursions reported separately, signed, unclamped | ✅ |
| R2-08-e | TP-before-SL, with same-bar ambiguity **unresolved** | No intrabar order is ever invented | ✅ |
| R2-08-f | Explicit horizons; no target depends on an implicit one | `horizon_bars` required on the spec and carried on every value | ✅ |
| R2-08-g | Strongly defined dataset row with three schema versions | `DatasetRow`; reproducible from spec + source bars alone | ✅ |
| R2-08-h | `None` / `NaN` / `0` never conflated | R2-07's rule extended, not a second policy | ✅ |
| R2-08-i | `from_dict(as_dict()) == value` throughout | Spec, value, row, dataset spec, split spec | ✅ |
| R2-08-j | Leakage audit: truncate / mutate / wick / **control** | The control is what makes the other three non-vacuous | ✅ |
| R2-08-k | Boundary tests | One future bar, final bar, insufficient history, zero movement, exact and zero threshold, invalid threshold and horizon, NaN inputs, timezones | ✅ |
| R2-08-l | Deterministic chronological splitting | No random split exists or can be asked for | ✅ |
| R2-08-m | Target-window contamination handled explicitly | `EMBARGOED`; an under-specified embargo is **refused**, not silently widened | ✅ |
| R2-08-n | Deterministic data-quality diagnostics | Describes only — a guard test bans selection, imputation and correlation | ✅ |
| R2-08-o | Real-data validation on EURUSD + XAUUSD | 1m/5m/15m/1H/4H, weekend, DST, sparse 4H history | ✅ |
| R2-08-p | Point-in-time equivalence for features; targets exempt by definition | Prefix replay; a prefix may resolve fewer targets, never more | ✅ |
| R2-08-q | Provenance reused, never reconstructed | `feature_provenance` is R2-07's `source_ids()` verbatim | ✅ |
| R2-08-r | No model training anywhere in the package | Guard test bans xgboost / lightgbm / sklearn / torch / optuna / kronos | ✅ |
| R2-08-s | Performance measured, not prematurely optimised | Reported in the completion report | ✅ |
| R2-08-t | Documentation | Three documents plus a package README, every ambiguity classified | ✅ |
| R2-08-u | Regression: R2-01 → R2-07 untouched | No detector, state or feature-vector source changed | ✅ |

## Decisions that change what is being predicted

These are flagged rather than buried, because each one silently changes the question a
model is asked. All are recorded in [targets.md](../../docs/features/targets.md) §2 with
the alternative not taken.

1. **Reference price = close of the observed bar.** Not the next open.
2. **Return = simple close-to-close.** Not log, not open-to-close, not high/low based.
3. **Threshold unit = instrument points.** Not a return fraction and not ATR multiples —
   an ATR unit would smuggle a volatility model into a label.
4. **Future window = bars `i+1 … i+H` inclusive.** Bar `i` is never readable by a target.
5. **Same-bar TP + SL = `UNRESOLVED`.** An honest limitation of OHLC, not a preference.
6. **Excursions are two signed values, never one "MFE".** Folding them would encode a
   trade side inside the label.
7. **Points are rounded to 6 decimals before comparison** — numerical safety so the
   declared `>=` boundary rule is actually true at the boundary, not a precision claim.

## Not implemented, and why

| Item | Reason |
|---|---|
| Walk-forward / expanding-window validation | R2-08 needs one partition. Expanding-window validation belongs to the phase that runs experiments |
| Symmetric purge around a boundary | The contamination is forward-looking, so the protection is. Nothing has yet demonstrated a backward purge is needed |
| Parquet dataset writer | The storage layer exists; wiring it is a separate, explicit step rather than an assumed one |
| Cross-symbol or cross-timeframe pooling | A modelling decision, not a data one |
| Cost / spread / slippage | A target is a market fact; execution is Phase 7 |
