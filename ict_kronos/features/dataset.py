"""The dataset row — features, targets and provenance joined at one instant (R2-08).

Full semantics in ``docs/features/dataset.md``.

A row is the join of two things built under *opposite* temporal rules:

.. code-block:: text

    row.features   <- ICTFeatureVector, point-in-time, R2-07's contract
    row.targets    <- TargetValue, future-dependent, R2-08's contract

The join happens here and nowhere else, and it is one-directional: the builder hands
the feature layer a frame and an instant, hands the target engine the same frame and
the same instant, and puts the two answers side by side. **No value crosses.** The
feature layer has no reference to a target and could not read one if it tried.

Reproducibility is the other reason this class exists. A row records the three schema
versions that produced it, so a result can always be traced back to the definitions in
force when it was built — not to whatever the code says today.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from ..domain import Symbol, Timeframe
from ..ict import FEATURE_NAMES, FEATURE_VERSION, ICTFeatureVector, MarketStateBuilder
from .splits import SplitError, SplitLabel, SplitPlan, SplitSpec, assign_splits
from .targets import TARGET_SCHEMA_VERSION, TargetEngine, TargetSpec, TargetValue

#: Bumped when the ROW shape changes — a new column, a renamed field, a different join.
DATASET_SCHEMA_VERSION = "r2-08.1"


@dataclass(frozen=True)
class DatasetRow:
    """One instant: what was knowable, what happened next, and where both came from."""

    symbol: str
    timeframe: str
    as_of: datetime

    features: ICTFeatureVector
    targets: tuple[TargetValue, ...]
    #: R2-07's ``ICTMarketState.source_ids()`` verbatim — ids, never geometry. Reused
    #: rather than recomputed, so feature provenance has exactly one definition.
    feature_provenance: dict[str, tuple[str, ...]] = field(default_factory=dict)

    split: SplitLabel | None = None

    dataset_schema_version: str = DATASET_SCHEMA_VERSION
    feature_schema_version: str = FEATURE_VERSION
    target_schema_version: str = TARGET_SCHEMA_VERSION

    def target(self, name: str) -> TargetValue | None:
        return next((t for t in self.targets if t.spec_name == name), None)

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "as_of": self.as_of.isoformat(),
            "dataset_schema_version": self.dataset_schema_version,
            "feature_schema_version": self.feature_schema_version,
            "target_schema_version": self.target_schema_version,
            "split": None if self.split is None else self.split.value,
            "features": self.features.as_dict(),
            "targets": [t.as_dict() for t in self.targets],
            "feature_provenance": {k: list(v) for k, v in sorted(self.feature_provenance.items())},
        }

    @classmethod
    def from_dict(cls, payload: dict) -> DatasetRow:
        return cls(
            symbol=payload["symbol"],
            timeframe=payload["timeframe"],
            as_of=datetime.fromisoformat(payload["as_of"]),
            features=ICTFeatureVector.from_dict(payload["features"]),
            targets=tuple(TargetValue.from_dict(t) for t in payload["targets"]),
            feature_provenance={k: tuple(v) for k, v in payload["feature_provenance"].items()},
            split=None if payload["split"] is None else SplitLabel(payload["split"]),
            dataset_schema_version=payload["dataset_schema_version"],
            feature_schema_version=payload["feature_schema_version"],
            target_schema_version=payload["target_schema_version"],
        )


@dataclass(frozen=True)
class DatasetSpec:
    """Everything needed to rebuild a dataset from source data alone.

    Deliberately holds no data. A dataset is reproducible from ``(symbol, timeframe,
    this spec, the source bars)`` and from nothing else — which is the difference
    between an experiment that can be re-run and one that can only be believed.
    """

    targets: tuple[TargetSpec, ...]
    split: SplitSpec | None = None
    dataset_schema_version: str = DATASET_SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Refuse a split whose embargo is shorter than the targets it must protect.

        This is the one place contamination could enter by accident: a split configured
        with ``embargo_bars=0`` alongside an 8-bar target lets every row near a boundary
        train on the next period's bars, silently and with no NaN to notice. Raising is
        the only option that neither leaks nor quietly overrides what the caller asked
        for -- ``with_split`` exists to derive the right value.
        """
        if self.split is None:
            return
        needed = self.max_horizon_bars
        if self.split.embargo_bars < needed:
            raise SplitError(
                f"embargo_bars={self.split.embargo_bars} is shorter than the longest target "
                f"horizon ({needed} bars), so rows near a split boundary would resolve their "
                "targets from the next period. Use DatasetSpec.with_split(...) or set "
                f"embargo_bars={needed}."
            )

    @property
    def max_horizon_bars(self) -> int:
        """The longest future window any target opens. Drives the embargo."""
        return max((s.horizon_bars for s in self.targets), default=0)

    def with_split(self, split: SplitSpec) -> DatasetSpec:
        """The same spec with ``split``, its embargo widened to cover every horizon."""
        from dataclasses import replace as dc_replace

        needed = max(split.embargo_bars, self.max_horizon_bars)
        return dc_replace(self, split=dc_replace(split, embargo_bars=needed))

    def as_dict(self) -> dict:
        return {
            "targets": [s.as_dict() for s in self.targets],
            "split": None if self.split is None else self.split.as_dict(),
            "dataset_schema_version": self.dataset_schema_version,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> DatasetSpec:
        return cls(
            targets=tuple(TargetSpec.from_dict(t) for t in payload["targets"]),
            split=None if payload["split"] is None else SplitSpec.from_dict(payload["split"]),
            dataset_schema_version=payload["dataset_schema_version"],
        )


@dataclass
class Dataset:
    """Rows plus the specification that produced them, plus the split plan."""

    symbol: Symbol
    timeframe: Timeframe
    spec: DatasetSpec
    rows: list[DatasetRow] = field(default_factory=list)
    split_plan: SplitPlan | None = None

    def __len__(self) -> int:
        return len(self.rows)

    def of(self, label: SplitLabel) -> list[DatasetRow]:
        return [r for r in self.rows if r.split is label]

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol.value,
            "timeframe": self.timeframe.value,
            "spec": self.spec.as_dict(),
            "rows": [r.as_dict() for r in self.rows],
            "split_plan": None if self.split_plan is None else self.split_plan.as_dict(),
        }


@dataclass
class DatasetBuilder:
    """Builds rows by asking two independent layers the same question.

    The builder is the join, and it is intentionally dull: no formula, no threshold, no
    lifecycle. Everything interesting already happened in R2-07 or in the target engine,
    and putting logic here would put it on both sides of the temporal boundary at once.
    """

    state_builder: MarketStateBuilder = field(default_factory=MarketStateBuilder)

    def build(
        self,
        frame: pd.DataFrame,
        symbol: Symbol,
        timeframe: Timeframe,
        spec: DatasetSpec,
        *,
        instants: list[datetime] | None = None,
    ) -> Dataset:
        engine = self.state_builder.analyse(frame, symbol, timeframe)
        targets = TargetEngine(symbol=symbol, timeframe=timeframe, frame=frame)

        chosen = engine.observation_instants() if instants is None else sorted(instants)
        states = {s.as_of: s for s in engine.states(chosen)}

        window_ends: dict[datetime, datetime | None] = {}
        drafts: list[tuple[datetime, ICTFeatureVector, tuple[TargetValue, ...], dict]] = []

        for moment in chosen:
            state = states.get(moment)
            if state is None:
                # Not a bar close. R2-07 refuses to invent a state between closes and
                # this layer does not invent a row either.
                continue

            values = tuple(targets.value_at(s, moment) for s in spec.targets)
            drafts.append((moment, ICTFeatureVector.from_state(state), values, state.source_ids()))
            ends = [v.future_window_end for v in values if v.future_window_end is not None]
            window_ends[moment] = max(ends) if ends else None

        plan = None
        if spec.split is not None:
            plan = assign_splits(
                [moment for moment, _, _, _ in drafts], spec.split, target_window_ends=window_ends
            )

        rows = [
            DatasetRow(
                symbol=symbol.value,
                timeframe=timeframe.value,
                as_of=moment,
                features=vector,
                targets=values,
                feature_provenance=provenance,
                split=None if plan is None else plan.label_at(moment),
            )
            for moment, vector, values, provenance in drafts
        ]
        return Dataset(symbol=symbol, timeframe=timeframe, spec=spec, rows=rows, split_plan=plan)


def rows_to_frame(rows: list[DatasetRow], target_name: str | None = None):
    """A flat DataFrame: identity, split, ``FEATURE_NAMES`` in order, then target columns.

    Column order is fixed here so every consumer gets the same frame rather than each
    assembling its own — the order IS the schema, exactly as in R2-07.
    """
    identity = [
        "symbol",
        "timeframe",
        "as_of",
        "split",
        "dataset_schema_version",
        "feature_schema_version",
        "target_schema_version",
    ]
    target_columns = [
        "target_name",
        "target_type",
        "target_horizon_bars",
        "target_resolved",
        "target_unresolved_reason",
        "target_future_return",
        "target_future_move_points",
        "target_direction",
        "target_up_excursion_points",
        "target_down_excursion_points",
        "target_outcome",
    ]
    if not rows:
        return pd.DataFrame(columns=[*identity, *FEATURE_NAMES, *target_columns])

    records = []
    for row in rows:
        record: dict = {
            "symbol": row.symbol,
            "timeframe": row.timeframe,
            "as_of": row.as_of,
            "split": None if row.split is None else row.split.value,
            "dataset_schema_version": row.dataset_schema_version,
            "feature_schema_version": row.feature_schema_version,
            "target_schema_version": row.target_schema_version,
        }
        for name in FEATURE_NAMES:
            record[name] = getattr(row.features, name)

        chosen = row.target(target_name) if target_name else (row.targets[0] if row.targets else None)
        record.update(
            {
                "target_name": chosen.spec_name if chosen else None,
                "target_type": chosen.target_type.value if chosen else None,
                "target_horizon_bars": chosen.horizon_bars if chosen else None,
                "target_resolved": chosen.resolved if chosen else None,
                "target_unresolved_reason": (
                    chosen.unresolved_reason.value if chosen and chosen.unresolved_reason else None
                ),
                "target_future_return": chosen.future_return if chosen else None,
                "target_future_move_points": chosen.future_move_points if chosen else None,
                "target_direction": chosen.direction.value if chosen and chosen.direction else None,
                "target_up_excursion_points": chosen.up_excursion_points if chosen else None,
                "target_down_excursion_points": chosen.down_excursion_points if chosen else None,
                "target_outcome": chosen.outcome.value if chosen and chosen.outcome else None,
            }
        )
        records.append(record)

    return pd.DataFrame(records, columns=[*identity, *FEATURE_NAMES, *target_columns])


__all__ = [
    "DATASET_SCHEMA_VERSION",
    "Dataset",
    "DatasetBuilder",
    "DatasetRow",
    "DatasetSpec",
    "rows_to_frame",
]
