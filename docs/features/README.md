# `ict_kronos/features` — targets, dataset rows and splits (R2-08)

This package owns the **second half** of the engine's temporal contract. The first half
lives in [`ict_kronos/ict`](../ict/README.md) and never looks forward.

```
FEATURES(T)  <-  observable at T only          ict_kronos/ict       R2-01 … R2-07
TARGET(T)    <-  may use information after T   ict_kronos/features  R2-08
```

The two are separated by module boundary, not by convention: `targets.py` imports nothing
from the feature layer, the feature layer imports nothing from here, and guard tests
assert both directions. `DatasetBuilder` is the only place they meet, and it only puts
their answers side by side.

| Document | Covers |
|---|---|
| [targets.md](targets.md) | The four target types, every convention and the alternative it was chosen over, unresolved reasons, the same-bar TP/SL ambiguity |
| [dataset.md](dataset.md) | Row schema, reproducibility, the four-part leakage contract, provenance, the flat frame, the quality audit |
| [splits.md](splits.md) | Chronological boundaries, and the embargo that stops a target window crossing one |
| [production_universe.md](production_universe.md) | The 1H/4H/Daily production lock, the Daily-vs-True-Daily-Open discrepancy, per-pair target parameters |
| [data_coverage.md](data_coverage.md) | Why a bar is missing observations — market closure, provider loss, dataset boundary — and which of those disqualifies it |

## What this package does not do

No model training · no Kronos · no XGBoost / LightGBM / sklearn / PyTorch · no
hyperparameter search · no backtesting · no execution · no feature selection · no
imputation · no class balancing.

The output of R2-08 is a **dataset contract**: rows, targets, a split definition and a
quality report that a later phase can use *and can audit*.
