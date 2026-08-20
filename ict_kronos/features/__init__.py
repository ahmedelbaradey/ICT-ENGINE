"""Prediction targets, dataset rows and chronological splits (R2-08).

This package owns the **second half** of the engine's temporal contract:

.. code-block:: text

    FEATURES(T)  <- observable at T only        ict_kronos/ict   (R2-07)
    TARGET(T)    <- may use information after T  ict_kronos/features (R2-08)

Nothing here trains, selects, scores or optimises anything. The output is a dataset
contract — rows, targets, splits and a quality report — that a later phase can use
*and can audit*.
"""

from .audit import (
    DatasetAudit,
    FeatureDiagnostic,
    TargetDiagnostic,
    audit_dataset,
    audit_rows,
)
from .dataset import (
    DATASET_SCHEMA_VERSION,
    Dataset,
    DatasetBuilder,
    DatasetRow,
    DatasetSpec,
    rows_to_frame,
)
from .splits import (
    SPLIT_SCHEMA_VERSION,
    SplitAssignment,
    SplitError,
    SplitLabel,
    SplitPlan,
    SplitSpec,
    assign_splits,
)
from .targets import (
    DEFAULT_HORIZONS,
    TARGET_SCHEMA_VERSION,
    TargetDirection,
    TargetEngine,
    TargetSpec,
    TargetSpecError,
    TargetType,
    TargetValue,
    TpSlOutcome,
    TradeSide,
    UnresolvedReason,
)

__all__ = [
    "DATASET_SCHEMA_VERSION",
    "DEFAULT_HORIZONS",
    "SPLIT_SCHEMA_VERSION",
    "TARGET_SCHEMA_VERSION",
    "Dataset",
    "DatasetAudit",
    "DatasetBuilder",
    "DatasetRow",
    "DatasetSpec",
    "FeatureDiagnostic",
    "SplitAssignment",
    "SplitError",
    "SplitLabel",
    "SplitPlan",
    "SplitSpec",
    "TargetDiagnostic",
    "TargetDirection",
    "TargetEngine",
    "TargetSpec",
    "TargetSpecError",
    "TargetType",
    "TargetValue",
    "TpSlOutcome",
    "TradeSide",
    "UnresolvedReason",
    "assign_splits",
    "audit_dataset",
    "audit_rows",
    "rows_to_frame",
]
