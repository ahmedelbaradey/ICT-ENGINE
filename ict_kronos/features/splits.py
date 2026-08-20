"""Chronological train / validation / test splitting, with an explicit embargo (R2-08).

Full semantics in ``docs/features/splits.md``.

A financial time series is never shuffled (CLAUDE.md rule 6), so there is no random
split here and no way to ask for one. That much is obvious. The part that is not
obvious, and that this module exists to make impossible to forget:

    **A row's features end at ``as_of``; its target ends H bars later.**

So a row sitting a few bars before the train/validation boundary has a target that was
resolved by bars belonging to validation. Training on it leaks the validation period
into the model — quietly, with no error and no NaN, and it inflates the validation score
that is then used to decide the experiment worked.

The fix is an **embargo**: rows whose future window crosses their split's end are
labelled ``EMBARGOED`` and excluded from every split rather than silently kept.

The embargo width is not guessed here. ``DatasetSpec`` refuses to pair a split with an
embargo shorter than its longest target horizon, so the two cannot drift apart — and
when the measured window ends are supplied, a row is embargoed because its window
*demonstrably* crosses the boundary rather than because it sits near one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

#: Bumped when the split RULE changes, not when boundaries move.
SPLIT_SCHEMA_VERSION = "r2-08.1"


class SplitLabel(StrEnum):
    """Where a row belongs. ``EMBARGOED`` is a real label, not an error state."""

    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"
    #: Inside a split by timestamp, but its target window reaches into the next one.
    EMBARGOED = "embargoed"


class SplitError(ValueError):
    """Raised when a split specification cannot describe a valid partition."""


@dataclass(frozen=True)
class SplitSpec:
    """Two boundaries and an embargo. Immutable, serialisable, auditable.

    Boundaries are **exclusive upper bounds**: a row with ``as_of < train_end`` is TRAIN,
    ``train_end <= as_of < validation_end`` is VALIDATION, and anything at or after
    ``validation_end`` is TEST. Stated once, here, so no caller has to guess whether an
    instant exactly on a boundary belongs to the earlier or the later side.
    """

    train_end: datetime
    validation_end: datetime
    #: Bars of future information each row's target may consume. Rows whose window
    #: crosses their split's end are embargoed. ``0`` disables the embargo, which is
    #: only correct for a dataset with no forward-looking target at all.
    embargo_bars: int = 0
    version: str = SPLIT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.train_end.tzinfo is None or self.validation_end.tzinfo is None:
            raise SplitError("split boundaries must be timezone-aware UTC")
        if self.validation_end <= self.train_end:
            raise SplitError(
                f"validation_end {self.validation_end.isoformat()} must be after train_end "
                f"{self.train_end.isoformat()}; splits are chronological"
            )
        if self.embargo_bars < 0:
            raise SplitError(f"embargo_bars must be >= 0; got {self.embargo_bars}")

    def label_of(self, as_of: datetime) -> SplitLabel:
        """The split an instant falls in, ignoring the embargo."""
        if as_of < self.train_end:
            return SplitLabel.TRAIN
        if as_of < self.validation_end:
            return SplitLabel.VALIDATION
        return SplitLabel.TEST

    def end_of(self, label: SplitLabel) -> datetime | None:
        """The exclusive upper bound of a split. ``None`` for TEST — it is open-ended."""
        return {
            SplitLabel.TRAIN: self.train_end,
            SplitLabel.VALIDATION: self.validation_end,
            SplitLabel.TEST: None,
        }.get(label)

    def as_dict(self) -> dict:
        return {
            "train_end": self.train_end.isoformat(),
            "validation_end": self.validation_end.isoformat(),
            "embargo_bars": self.embargo_bars,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> SplitSpec:
        return cls(
            train_end=datetime.fromisoformat(payload["train_end"]),
            validation_end=datetime.fromisoformat(payload["validation_end"]),
            embargo_bars=payload["embargo_bars"],
            version=payload["version"],
        )

    @classmethod
    def by_proportion(
        cls,
        instants: list[datetime],
        *,
        train: float = 0.6,
        validation: float = 0.2,
        embargo_bars: int = 0,
    ) -> SplitSpec:
        """Boundaries from proportions of the ORDERED instants — never of the clock.

        Proportions cut the observation sequence, not the calendar, so a weekend does
        not silently hand one split more rows than intended. The remainder after
        ``train`` and ``validation`` is TEST; it is not a third parameter, so the three
        can never fail to sum to one.
        """
        if not instants:
            raise SplitError("cannot derive split boundaries from an empty sequence")
        if train <= 0 or validation <= 0 or train + validation >= 1:
            raise SplitError(
                f"train {train} and validation {validation} must be positive and leave room "
                "for a test period"
            )

        ordered = sorted(instants)
        total = len(ordered)
        first = max(1, int(total * train))
        second = max(first + 1, int(total * (train + validation)))
        if second >= total:
            raise SplitError(
                f"{total} instants cannot be split {train}/{validation} and still leave a "
                "non-empty test period"
            )
        return cls(
            train_end=ordered[first],
            validation_end=ordered[second],
            embargo_bars=embargo_bars,
        )


@dataclass(frozen=True)
class SplitAssignment:
    """One row's placement, and — when embargoed — the reason it was withheld."""

    as_of: datetime
    label: SplitLabel
    #: The split the row would have joined had its target window not crossed out of it.
    natural_label: SplitLabel
    #: Close time of the last bar the row's target consumes, when it consumes any.
    target_window_end: datetime | None = None

    @property
    def embargoed(self) -> bool:
        return self.label is SplitLabel.EMBARGOED

    def as_dict(self) -> dict:
        return {
            "as_of": self.as_of.isoformat(),
            "label": self.label.value,
            "natural_label": self.natural_label.value,
            "target_window_end": (
                None if self.target_window_end is None else self.target_window_end.isoformat()
            ),
        }


@dataclass
class SplitPlan:
    """A full, auditable assignment of every instant. Nothing is dropped silently."""

    spec: SplitSpec
    assignments: list[SplitAssignment] = field(default_factory=list)

    def label_at(self, as_of: datetime) -> SplitLabel | None:
        for item in self.assignments:
            if item.as_of == as_of:
                return item.label
        return None

    def of(self, label: SplitLabel) -> list[datetime]:
        return [a.as_of for a in self.assignments if a.label is label]

    def counts(self) -> dict[str, int]:
        return {label.value: sum(1 for a in self.assignments if a.label is label) for label in SplitLabel}

    def boundaries(self) -> dict:
        """Exactly what a reviewer needs to check the partition by eye."""
        out: dict = {"spec": self.spec.as_dict(), "counts": self.counts()}
        for label in (SplitLabel.TRAIN, SplitLabel.VALIDATION, SplitLabel.TEST):
            chosen = self.of(label)
            out[label.value] = {
                "count": len(chosen),
                "first": chosen[0].isoformat() if chosen else None,
                "last": chosen[-1].isoformat() if chosen else None,
            }
        return out

    def as_dict(self) -> dict:
        return {
            "spec": self.spec.as_dict(),
            "assignments": [a.as_dict() for a in self.assignments],
        }


def assign_splits(
    instants: list[datetime],
    spec: SplitSpec,
    *,
    target_window_ends: dict[datetime, datetime | None] | None = None,
) -> SplitPlan:
    """Label every instant, embargoing any whose target reaches into the next split.

    ``target_window_ends`` maps each instant to the close time of the last bar its
    target consumes. Passing it is what makes the embargo *measured* rather than
    assumed: a row is embargoed because its window demonstrably crosses the boundary,
    not because it happens to sit near one.

    Without it the embargo falls back to ``spec.embargo_bars`` positions from the end of
    each split, which is the same rule expressed in bars — correct, and coarser.
    """
    ordered = sorted(instants)
    ends = target_window_ends or {}
    assignments: list[SplitAssignment] = []

    for position, moment in enumerate(ordered):
        natural = spec.label_of(moment)
        boundary = spec.end_of(natural)
        window_end = ends.get(moment)

        crosses = False
        if boundary is not None and spec.embargo_bars > 0:
            if window_end is not None:
                crosses = window_end >= boundary
            else:
                # No measured window: embargo the last ``embargo_bars`` positions of the
                # split, which is the same rule counted in bars. A lookahead past the end
                # of the data is NOT embargoed -- such a row has an unresolved target
                # rather than a leaking one.
                lookahead = position + spec.embargo_bars
                crosses = lookahead < len(ordered) and ordered[lookahead] >= boundary

        assignments.append(
            SplitAssignment(
                as_of=moment,
                label=SplitLabel.EMBARGOED if crosses else natural,
                natural_label=natural,
                target_window_end=window_end,
            )
        )

    return SplitPlan(spec=spec, assignments=assignments)


__all__ = [
    "SPLIT_SCHEMA_VERSION",
    "SplitAssignment",
    "SplitError",
    "SplitLabel",
    "SplitPlan",
    "SplitSpec",
    "assign_splits",
]
