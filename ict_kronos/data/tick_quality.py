"""Tick-level integrity checks.

Runs BEFORE aggregation, because a malformed tick that reaches the bar builder
becomes a malformed bar, and a malformed bar becomes phantom ICT structure. The
policy mirrors the bar normalizer exactly:

  bad ticks are QUARANTINED and COUNTED, never repaired.

Repairing a tick means inventing a price that never traded. Every rejection is
attributed to a specific reason so the data-proof document can state exactly what
was thrown away and why.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..app.logging import get_logger

logger = get_logger(__name__)


@dataclass
class TickQualityReport:
    """What tick validation found. Aggregated across an ingest run and serialised
    into the dataset manifest."""

    input_ticks: int = 0
    output_ticks: int = 0
    non_positive_price: int = 0
    nan_price: int = 0
    crossed_book: int = 0
    negative_volume: int = 0
    duplicate_ticks: int = 0
    out_of_order: int = 0
    truncated_bytes: int = 0
    #: Widest bid/ask spread that survived validation, in price units. A wildly
    #: large value is the signature of a bad print that passed the cheap checks.
    max_spread: float = 0.0
    hours_requested: int = 0
    hours_empty: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    @property
    def rejected(self) -> int:
        return self.input_ticks - self.output_ticks

    def merge(self, other: TickQualityReport) -> None:
        self.input_ticks += other.input_ticks
        self.output_ticks += other.output_ticks
        self.non_positive_price += other.non_positive_price
        self.nan_price += other.nan_price
        self.crossed_book += other.crossed_book
        self.negative_volume += other.negative_volume
        self.duplicate_ticks += other.duplicate_ticks
        self.out_of_order += other.out_of_order
        self.truncated_bytes += other.truncated_bytes
        self.hours_requested += other.hours_requested
        self.hours_empty += other.hours_empty
        self.max_spread = max(self.max_spread, other.max_spread)
        for key, value in other.reasons.items():
            self.reasons[key] = self.reasons.get(key, 0) + value

    def as_dict(self) -> dict:
        return {
            "input_ticks": self.input_ticks,
            "output_ticks": self.output_ticks,
            "rejected_ticks": self.rejected,
            "non_positive_price": self.non_positive_price,
            "nan_price": self.nan_price,
            "crossed_book": self.crossed_book,
            "negative_volume": self.negative_volume,
            "duplicate_ticks": self.duplicate_ticks,
            "out_of_order": self.out_of_order,
            "truncated_bytes": self.truncated_bytes,
            "max_spread": round(float(self.max_spread), 6),
            "hours_requested": self.hours_requested,
            "hours_empty": self.hours_empty,
            "reasons": dict(self.reasons),
        }


def validate_ticks(ticks: pd.DataFrame) -> tuple[pd.DataFrame, TickQualityReport]:
    """Quarantine malformed ticks and report what was removed.

    Checks, in order:

    1. **NaN price** — a missing bid or ask.
    2. **Non-positive price** — zero or negative bid/ask. A zero price in a bi5 file
       is a padding or decode artefact, never a real quote.
    3. **Crossed book** — ``bid > ask``. Physically impossible in a normal feed and a
       reliable signature of a corrupt record.
    4. **Negative volume** — an impossible quantity.
    5. **Out-of-order** — ticks whose timestamp goes backwards. Counted, then fixed
       by a stable sort (the data is not wrong, only unordered).
    6. **Duplicates** — identical (timestamp, bid, ask). The same quote delivered
       twice is redundancy, not two trades, so it must not inflate tick-count volume.

    The input frame is never mutated.
    """
    report = TickQualityReport()
    report.input_ticks = len(ticks)

    if len(ticks) == 0:
        return ticks.copy(deep=True), report

    work = ticks.copy(deep=True)

    bid = work["bid"].to_numpy(dtype="float64", copy=False)
    ask = work["ask"].to_numpy(dtype="float64", copy=False)

    nan_mask = np.isnan(bid) | np.isnan(ask)
    # Comparisons against NaN are False, so combine explicitly rather than relying
    # on short-circuit ordering.
    non_positive_mask = ~nan_mask & ((bid <= 0.0) | (ask <= 0.0))
    crossed_mask = ~nan_mask & (bid > ask)

    report.nan_price = int(nan_mask.sum())
    report.non_positive_price = int(non_positive_mask.sum())
    report.crossed_book = int(crossed_mask.sum())

    bad = nan_mask | non_positive_mask | crossed_mask

    if "bid_volume" in work.columns and "ask_volume" in work.columns:
        volumes_bad = (work["bid_volume"] < 0) | (work["ask_volume"] < 0)
        report.negative_volume = int(volumes_bad.sum())
        bad = bad | volumes_bad.to_numpy(dtype=bool, copy=False)

    work = work.loc[~bad]

    # Order before dedup so "identical consecutive quote" is well defined.
    ts = work["timestamp"]
    report.out_of_order = int((ts.diff().dropna() < pd.Timedelta(0)).sum())
    work = work.sort_values("timestamp", kind="mergesort")

    before = len(work)
    work = work.drop_duplicates(subset=["timestamp", "bid", "ask"], keep="first")
    report.duplicate_ticks = before - len(work)

    if len(work):
        spread = (work["ask"] - work["bid"]).max()
        report.max_spread = float(spread) if pd.notna(spread) else 0.0

    report.output_ticks = len(work)
    for key in ("nan_price", "non_positive_price", "crossed_book", "negative_volume", "duplicate_ticks"):
        value = getattr(report, key)
        if value:
            report.reasons[key] = value

    if report.rejected:
        logger.warning(
            "tick validation rejected %d/%d tick(s): %s",
            report.rejected,
            report.input_ticks,
            report.reasons,
        )

    return work.reset_index(drop=True), report
