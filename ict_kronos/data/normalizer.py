"""DataNormalizer — turns provider output into the canonical, trustworthy frame.

A provider may return duplicates, unsorted rows, misaligned timestamps, invalid
OHLC, or gaps. Every downstream stage — ICT detection, feature building, model
training, backtesting — assumes those problems are already gone. This module is
where that guarantee is created, and it is the only place allowed to make it.

Design commitments, each one load-bearing:

* **Gaps are recorded, never filled.** Forward-filling a missing bar fabricates
  price action that never occurred, and every ICT detector downstream would treat
  it as real structure. Absence is data. It is reported in
  :class:`NormalizationReport` and travels into the dataset manifest.
* **Invalid bars are quarantined, not repaired.** "Fixing" a bar whose high is
  below its close means inventing a price. Bad rows are dropped and counted.
* **Timestamps are floored to the bar grid.** A bar whose open time is not on a
  clean timeframe boundary cannot be aligned across timeframes, so misalignment is
  detected and either corrected by flooring or rejected — never ignored.
* **The output is immutable in spirit** (CLAUDE.md rule 7): normalization returns a
  new frame; it never mutates the input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from ..app.logging import get_logger
from ..domain import CANDLE_COLUMNS, Symbol, Timeframe, empty_frame, validate_frame

logger = get_logger(__name__)


@dataclass(frozen=True)
class Gap:
    """A run of missing bars on the timeframe grid.

    ``start`` is the open time of the first MISSING bar; ``end`` is the open time of
    the first bar present again. ``bar_count`` is how many bars are absent.
    """

    start: datetime
    end: datetime
    bar_count: int

    def as_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "bar_count": self.bar_count,
        }


@dataclass
class NormalizationReport:
    """What normalization did and what it found. Serialised into the dataset manifest.

    This is the audit trail that makes a dataset's quality inspectable after the
    fact, rather than a number nobody can reconstruct.
    """

    symbol: str
    timeframe: str
    input_rows: int = 0
    output_rows: int = 0
    duplicates_removed: int = 0
    invalid_removed: int = 0
    misaligned_floored: int = 0
    out_of_order_rows: int = 0
    gaps: list[Gap] = field(default_factory=list)
    significant_gaps: list[Gap] = field(default_factory=list)
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None

    @property
    def missing_bars(self) -> int:
        return sum(g.bar_count for g in self.gaps)

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "input_rows": self.input_rows,
            "output_rows": self.output_rows,
            "duplicates_removed": self.duplicates_removed,
            "invalid_removed": self.invalid_removed,
            "misaligned_floored": self.misaligned_floored,
            "out_of_order_rows": self.out_of_order_rows,
            "missing_bars": self.missing_bars,
            "gap_count": len(self.gaps),
            "significant_gap_count": len(self.significant_gaps),
            "significant_gaps": [g.as_dict() for g in self.significant_gaps],
            "first_timestamp": self.first_timestamp.isoformat() if self.first_timestamp else None,
            "last_timestamp": self.last_timestamp.isoformat() if self.last_timestamp else None,
        }


class DataNormalizer:
    """Normalizes raw provider frames into the canonical candle frame."""

    def __init__(self, *, max_gap_bars: int = 3) -> None:
        """``max_gap_bars`` is the threshold above which a gap is reported as
        *significant* — i.e. likely a data-quality problem rather than a routine
        weekend or holiday break. It is configuration, never a literal in logic."""
        self._max_gap_bars = max_gap_bars

    def normalize(
        self,
        frame: pd.DataFrame,
        symbol: Symbol,
        timeframe: Timeframe,
    ) -> tuple[pd.DataFrame, NormalizationReport]:
        """Return ``(canonical_frame, report)``. The input frame is never mutated."""
        report = NormalizationReport(symbol=symbol.value, timeframe=timeframe.value)
        report.input_rows = len(frame)

        if len(frame) == 0:
            return empty_frame(), report

        work = frame.copy(deep=True)

        missing = [c for c in CANDLE_COLUMNS if c not in work.columns]
        if missing:
            raise ValueError(f"cannot normalize: frame is missing columns {missing}")

        work = self._coerce_types(work, symbol, timeframe)
        work = self._floor_to_grid(work, timeframe, report)
        work = self._sort(work, report)
        work = self._drop_duplicates(work, report)
        work = self._drop_invalid(work, report)

        work = work[list(CANDLE_COLUMNS)].reset_index(drop=True)

        if len(work):
            report.first_timestamp = work["timestamp"].iloc[0].to_pydatetime()
            report.last_timestamp = work["timestamp"].iloc[-1].to_pydatetime()
            report.gaps = self.detect_gaps(work, timeframe)
            report.significant_gaps = [g for g in report.gaps if g.bar_count > self._max_gap_bars]

        report.output_rows = len(work)
        self._log(report)
        return work, report

    # ------------------------------------------------------------------ stages

    def _coerce_types(self, work: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> pd.DataFrame:
        work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True)
        # Symbol/timeframe are authoritative from the caller, not from the payload:
        # a provider must not be able to mislabel what it was asked for.
        work["symbol"] = pd.Series([symbol.value] * len(work), dtype="string", index=work.index)
        work["timeframe"] = pd.Series([timeframe.value] * len(work), dtype="string", index=work.index)
        for col in ("open", "high", "low", "close", "volume"):
            work[col] = pd.to_numeric(work[col], errors="coerce").astype("float64")
        return work

    def _floor_to_grid(
        self, work: pd.DataFrame, timeframe: Timeframe, report: NormalizationReport
    ) -> pd.DataFrame:
        """Snap open times onto the timeframe grid.

        Off-grid timestamps make multi-timeframe alignment impossible: a 15M bar
        opening at 10:03 cannot be placed inside a 1H bar without ambiguity.
        """
        floored = work["timestamp"].dt.floor(timeframe.pandas_freq)
        misaligned = int((floored != work["timestamp"]).sum())
        if misaligned:
            report.misaligned_floored = misaligned
            logger.warning(
                "%s %s: %d timestamp(s) were off the %s grid and have been floored",
                report.symbol,
                report.timeframe,
                misaligned,
                timeframe.value,
            )
            work["timestamp"] = floored
        return work

    def _sort(self, work: pd.DataFrame, report: NormalizationReport) -> pd.DataFrame:
        ts = work["timestamp"]
        out_of_order = int((ts.diff().dropna() < pd.Timedelta(0)).sum())
        if out_of_order:
            report.out_of_order_rows = out_of_order
        # mergesort is stable: for equal timestamps the provider's original order is
        # preserved, so "keep last" in dedup means "the most recently supplied value".
        return work.sort_values("timestamp", kind="mergesort")

    def _drop_duplicates(self, work: pd.DataFrame, report: NormalizationReport) -> pd.DataFrame:
        before = len(work)
        # Keep the LAST occurrence: a re-fetch of the same bar is treated as a
        # correction of the earlier one, which is the right default for a vendor
        # that revises recent bars.
        work = work.drop_duplicates(subset=["timestamp"], keep="last")
        removed = before - len(work)
        if removed:
            report.duplicates_removed = removed
            logger.info("%s %s: removed %d duplicate bar(s)", report.symbol, report.timeframe, removed)
        return work

    def _drop_invalid(self, work: pd.DataFrame, report: NormalizationReport) -> pd.DataFrame:
        ok = validate_frame(work, strict=False)
        removed = int((~ok).sum())
        if removed:
            report.invalid_removed = removed
            logger.warning(
                "%s %s: quarantined %d bar(s) violating OHLC invariants",
                report.symbol,
                report.timeframe,
                removed,
            )
        return work.loc[ok]

    # ------------------------------------------------------------------ gaps

    def detect_gaps(self, frame: pd.DataFrame, timeframe: Timeframe) -> list[Gap]:
        """Find runs of missing bars on the timeframe grid.

        Note that FX legitimately has a large weekly gap (Friday close to Sunday
        open) and holiday gaps. Those are still reported — this function does not
        try to guess which absences are "normal", because that judgement belongs to
        the session calendar in Phase 2, not to the normalizer. What it does do is
        separate routine small gaps from significant ones via ``max_gap_bars``.
        """
        if len(frame) < 2:
            return []

        ts = frame["timestamp"].reset_index(drop=True)
        step = timeframe.duration
        deltas = ts.diff()

        gaps: list[Gap] = []
        for idx in range(1, len(ts)):
            delta = deltas.iloc[idx]
            if delta <= step:
                continue
            missing = int(delta / step) - 1
            if missing <= 0:
                continue
            gaps.append(
                Gap(
                    start=(ts.iloc[idx - 1] + step).to_pydatetime(),
                    end=ts.iloc[idx].to_pydatetime(),
                    bar_count=missing,
                )
            )
        return gaps

    def _log(self, report: NormalizationReport) -> None:
        logger.info(
            "normalized %s %s: in=%d out=%d dupes=%d invalid=%d floored=%d gaps=%d (significant=%d)",
            report.symbol,
            report.timeframe,
            report.input_rows,
            report.output_rows,
            report.duplicates_removed,
            report.invalid_removed,
            report.misaligned_floored,
            len(report.gaps),
            len(report.significant_gaps),
        )
