"""Dataset quality diagnostics — deterministic description, never selection (R2-08).

Full semantics in ``docs/features/dataset.md`` §8.

This module **describes** a dataset. It does not choose features, drop columns, impute
anything, or rank anything by usefulness. That distinction is the whole point: feature
selection driven by outcome is a modelling decision, it belongs to Phase 4 where it can
be ablated against a baseline, and doing it here — before a single result exists — would
bake an unmeasured hypothesis into the data itself.

Everything below is reproducible from the rows alone: same rows, same report, byte for
byte, on any machine.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from ..ict import FEATURE_NAMES
from .dataset import Dataset, DatasetRow
from .splits import SplitLabel


@dataclass(frozen=True)
class FeatureDiagnostic:
    """One feature's shape across the dataset. Description only."""

    name: str
    #: Rows where the value is ``None`` — "this cannot exist here", R2-07 §7.
    missing_count: int
    #: Rows where the numeric projection is NaN. Equals ``missing_count`` unless a
    #: genuine NaN ever reaches a feature, which R2-07's audit made impossible.
    nan_count: int
    present_count: int
    unique_count: int
    minimum: float | None
    maximum: float | None
    mean: float | None
    #: A feature that never varies carries no information. Reported, never dropped.
    constant: bool

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "missing_count": self.missing_count,
            "nan_count": self.nan_count,
            "present_count": self.present_count,
            "unique_count": self.unique_count,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "mean": self.mean,
            "constant": self.constant,
        }


@dataclass(frozen=True)
class TargetDiagnostic:
    """One target specification's coverage and class balance."""

    name: str
    target_type: str
    horizon_bars: int
    total: int
    resolved_count: int
    unresolved_count: int
    #: Unresolved rows grouped by reason. "Ran off the end of the data" and "both
    #: barriers in one bar" are different problems and never pooled.
    unresolved_by_reason: dict[str, int]
    #: DIRECTION / TP_BEFORE_SL class counts. Empty for continuous targets.
    class_counts: dict[str, int]

    @property
    def coverage(self) -> float:
        return 0.0 if self.total == 0 else self.resolved_count / self.total

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "target_type": self.target_type,
            "horizon_bars": self.horizon_bars,
            "total": self.total,
            "resolved_count": self.resolved_count,
            "unresolved_count": self.unresolved_count,
            "coverage": self.coverage,
            "unresolved_by_reason": dict(sorted(self.unresolved_by_reason.items())),
            "class_counts": dict(sorted(self.class_counts.items())),
        }


@dataclass(frozen=True)
class DatasetAudit:
    """The full report. Sorted throughout, so two runs produce identical output."""

    row_count: int
    symbols: tuple[str, ...]
    timeframes: tuple[str, ...]
    feature_count: int
    feature_names: tuple[str, ...]
    dataset_schema_versions: tuple[str, ...]
    feature_schema_versions: tuple[str, ...]
    target_schema_versions: tuple[str, ...]
    first_as_of: str | None
    last_as_of: str | None
    duplicate_as_of_count: int
    chronological: bool
    split_counts: dict[str, int]
    features: tuple[FeatureDiagnostic, ...]
    targets: tuple[TargetDiagnostic, ...]

    def constant_features(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.features if f.constant)

    def fully_missing_features(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.features if f.present_count == 0)

    def as_dict(self) -> dict:
        return {
            "row_count": self.row_count,
            "symbols": list(self.symbols),
            "timeframes": list(self.timeframes),
            "feature_count": self.feature_count,
            "feature_names": list(self.feature_names),
            "dataset_schema_versions": list(self.dataset_schema_versions),
            "feature_schema_versions": list(self.feature_schema_versions),
            "target_schema_versions": list(self.target_schema_versions),
            "first_as_of": self.first_as_of,
            "last_as_of": self.last_as_of,
            "duplicate_as_of_count": self.duplicate_as_of_count,
            "chronological": self.chronological,
            "split_counts": dict(sorted(self.split_counts.items())),
            "features": [f.as_dict() for f in self.features],
            "targets": [t.as_dict() for t in self.targets],
        }


def audit_rows(rows: list[DatasetRow]) -> DatasetAudit:
    """Describe a list of rows. Pure function: no mutation, no I/O, no randomness."""
    ordered_as_of = [r.as_of for r in rows]
    seen = Counter(ordered_as_of)

    return DatasetAudit(
        row_count=len(rows),
        symbols=tuple(sorted({r.symbol for r in rows})),
        timeframes=tuple(sorted({r.timeframe for r in rows})),
        feature_count=len(FEATURE_NAMES),
        feature_names=FEATURE_NAMES,
        dataset_schema_versions=tuple(sorted({r.dataset_schema_version for r in rows})),
        feature_schema_versions=tuple(sorted({r.feature_schema_version for r in rows})),
        target_schema_versions=tuple(sorted({r.target_schema_version for r in rows})),
        first_as_of=ordered_as_of[0].isoformat() if rows else None,
        last_as_of=ordered_as_of[-1].isoformat() if rows else None,
        duplicate_as_of_count=sum(count - 1 for count in seen.values() if count > 1),
        chronological=all(a <= b for a, b in zip(ordered_as_of, ordered_as_of[1:], strict=False)),
        split_counts=_split_counts(rows),
        features=tuple(_feature_diagnostic(rows, name) for name in FEATURE_NAMES),
        targets=tuple(_target_diagnostics(rows)),
    )


def audit_dataset(dataset: Dataset) -> DatasetAudit:
    return audit_rows(dataset.rows)


def _split_counts(rows: list[DatasetRow]) -> dict[str, int]:
    counts = {label.value: 0 for label in SplitLabel}
    counts["unassigned"] = 0
    for row in rows:
        counts["unassigned" if row.split is None else row.split.value] += 1
    return counts


def _feature_diagnostic(rows: list[DatasetRow], name: str) -> FeatureDiagnostic:
    raw = [getattr(r.features, name) for r in rows]
    missing = sum(1 for v in raw if v is None)
    numeric = [float(v) for v in raw if v is not None and not isinstance(v, bool)]
    numeric += [float(int(v)) for v in raw if isinstance(v, bool)]
    nan = missing + sum(1 for v in numeric if math.isnan(v))
    finite = [v for v in numeric if math.isfinite(v)]

    return FeatureDiagnostic(
        name=name,
        missing_count=missing,
        nan_count=nan,
        present_count=len(finite),
        unique_count=len({v for v in finite}),
        minimum=min(finite) if finite else None,
        maximum=max(finite) if finite else None,
        mean=(sum(finite) / len(finite)) if finite else None,
        # A column that is entirely missing is NOT called constant: "always absent" and
        # "always the same number" are different findings.
        constant=bool(finite) and len({v for v in finite}) == 1,
    )


def _target_diagnostics(rows: list[DatasetRow]) -> list[TargetDiagnostic]:
    names: list[str] = []
    for row in rows:
        for value in row.targets:
            if value.spec_name not in names:
                names.append(value.spec_name)

    out: list[TargetDiagnostic] = []
    for name in sorted(names):
        values = [v for r in rows for v in r.targets if v.spec_name == name]
        if not values:
            continue
        resolved = [v for v in values if v.resolved]
        reasons = Counter(v.unresolved_reason.value for v in values if v.unresolved_reason is not None)

        classes: Counter = Counter()
        for value in values:
            if value.direction is not None:
                classes[value.direction.value] += 1
            elif value.outcome is not None:
                classes[value.outcome.value] += 1

        out.append(
            TargetDiagnostic(
                name=name,
                target_type=values[0].target_type.value,
                horizon_bars=values[0].horizon_bars,
                total=len(values),
                resolved_count=len(resolved),
                unresolved_count=len(values) - len(resolved),
                unresolved_by_reason=dict(reasons),
                class_counts=dict(classes),
            )
        )
    return out


__all__ = ["DatasetAudit", "FeatureDiagnostic", "TargetDiagnostic", "audit_dataset", "audit_rows"]
