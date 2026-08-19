"""Resampler — lower-timeframe bars into higher-timeframe bars.

This module is the single most dangerous place in Phase 1, because it is where
look-ahead leakage is introduced in almost every retail ICT/ML system. The failure
mode is subtle and silent:

    A 4H bar timestamped 08:00 is not *knowable* until 12:00. Joining it onto a 5M
    observation at 09:15 — which every naive ``merge`` or ``reindex(method='ffill')``
    on the open timestamp will happily do — leaks four hours of future information
    into the feature vector. The model looks brilliant in backtest and is worthless.

Two defences are built in here and enforced by tests:

1. :func:`resample` labels every aggregated bar by its OPEN time **and** carries an
   explicit ``close_time`` column. ``close_time`` is the first instant the bar is
   observable, and it is the only column downstream alignment is allowed to join on.
2. :func:`latest_closed_bar` / :func:`align_htf_context` implement point-in-time
   selection: at time *t*, the usable HTF bar is the last one whose ``close_time``
   is ``<= t``. There is no code path here that returns a bar which has not closed.

CLAUDE.md rule 1. A leak here invalidates every result in every later phase.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from ..app.logging import get_logger
from ..domain import CANDLE_COLUMNS, Symbol, Timeframe, empty_frame

logger = get_logger(__name__)

#: Canonical columns plus the observability anchor.
RESAMPLED_COLUMNS: tuple[str, ...] = (*CANDLE_COLUMNS, "close_time")

_AGGREGATION = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
}


class ResampleError(ValueError):
    """Raised when an aggregation would straddle bar boundaries."""


def resample(
    frame: pd.DataFrame,
    source: Timeframe,
    target: Timeframe,
    symbol: Symbol,
    *,
    require_complete: bool = True,
) -> pd.DataFrame:
    """Aggregate ``source`` bars into ``target`` bars.

    ``require_complete`` (default True) drops any target bar that is not backed by a
    full complement of source bars. This matters at both ends of a window: a 4H bar
    built from only the first 5 minutes of its period has a real open but a
    meaningless high/low/close, and treating it as a finished bar would be a
    fabricated observation. Callers doing exploratory work can pass False, but the
    dataset builder never should.

    Raises :class:`ResampleError` if ``target`` is not an exact multiple of
    ``source`` — an aggregation that straddles boundaries produces silently wrong
    opens and closes, so it is refused rather than approximated.
    """
    if not target.can_aggregate_from(source):
        raise ResampleError(
            f"cannot aggregate {source.value} into {target.value}: "
            f"target must be a strict, exact multiple of source "
            f"({target.minutes} % {source.minutes} = {target.minutes % source.minutes})"
        )

    if len(frame) == 0:
        return _empty_resampled()

    work = frame.copy(deep=True)
    work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True)
    work = work.sort_values("timestamp", kind="mergesort").set_index("timestamp")

    # label='left', closed='left' => the bar is stamped with its OPEN time and
    # covers [open, open + duration). This is the CLAUDE.md convention, and the
    # premise every downstream close_time calculation rests on.
    grouped = work.resample(target.pandas_freq, label="left", closed="left")
    bars = grouped.agg(_AGGREGATION)
    source_counts = grouped.size()

    bars = bars.loc[source_counts > 0]
    source_counts = source_counts.loc[source_counts > 0]

    if require_complete:
        expected = target.minutes // source.minutes
        complete = source_counts >= expected
        dropped = int((~complete).sum())
        if dropped:
            logger.info(
                "resample %s %s->%s: dropped %d incomplete target bar(s) (expected %d source bars each)",
                symbol.value,
                source.value,
                target.value,
                dropped,
                expected,
            )
        bars = bars.loc[complete]

    if bars.empty:
        return _empty_resampled()

    bars = bars.reset_index()
    bars["symbol"] = pd.Series([symbol.value] * len(bars), dtype="string")
    bars["timeframe"] = pd.Series([target.value] * len(bars), dtype="string")
    bars["close_time"] = bars["timestamp"] + target.duration
    for col in ("open", "high", "low", "close", "volume"):
        bars[col] = bars[col].astype("float64")

    return bars[list(RESAMPLED_COLUMNS)].reset_index(drop=True)


def with_close_time(frame: pd.DataFrame, timeframe: Timeframe) -> pd.DataFrame:
    """Attach the observability anchor to a canonical candle frame.

    Any frame that is going to participate in cross-timeframe alignment must carry
    ``close_time``, including the base timeframe — otherwise the base frame is the
    one path that bypasses the point-in-time rule.
    """
    work = frame.copy(deep=True)
    work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True)
    work["close_time"] = work["timestamp"] + timeframe.duration
    return work[list(RESAMPLED_COLUMNS)]


def latest_closed_bar(frame: pd.DataFrame, as_of: datetime) -> pd.Series | None:
    """The most recent bar observable at ``as_of``, or ``None`` if there is none.

    "Observable" means ``close_time <= as_of``. A bar that closes exactly at
    ``as_of`` IS observable — its final price is known at that instant.

    ``frame`` must carry ``close_time`` (see :func:`with_close_time`).
    """
    _require_close_time(frame)
    if len(frame) == 0:
        return None
    if as_of.tzinfo is None:
        raise ValueError(f"as_of must be timezone-aware UTC; got naive {as_of!r}")

    observable = frame.loc[frame["close_time"] <= pd.Timestamp(as_of)]
    if observable.empty:
        return None
    return observable.iloc[-1]


def align_htf_context(
    base: pd.DataFrame,
    htf: pd.DataFrame,
    *,
    suffix: str,
    columns: tuple[str, ...] = ("open", "high", "low", "close", "volume"),
) -> pd.DataFrame:
    """Attach higher-timeframe context to every base-timeframe bar, point-in-time.

    The join key is the base bar's OWN ``close_time`` — the instant a decision on
    that bar could actually be made — matched against the HTF bar's ``close_time``.
    ``merge_asof`` with ``direction='backward'`` then selects the last HTF bar that
    had already closed.

    This is deliberately the *only* alignment helper in the codebase. Anything that
    joins on ``timestamp`` instead of ``close_time`` is leaking, and confining the
    join to one reviewed function is what makes that rule enforceable.

    Base bars before the first HTF close get NaN context — correctly, because no HTF
    bar had closed yet. Those rows must be dropped by the dataset builder, not
    backfilled.
    """
    _require_close_time(base)
    _require_close_time(htf)

    if len(base) == 0:
        return base.copy(deep=True)

    left = base.sort_values("close_time", kind="mergesort").reset_index(drop=True)

    if len(htf) == 0:
        # No HTF bar has ever closed, so every row's context is genuinely unknown.
        # NaN (not pd.NA) keeps the column float-typed and consistent with the
        # merge_asof path below, which also produces NaN for unmatched rows.
        for col in columns:
            left[f"{col}_{suffix}"] = np.nan
        return left

    right = (
        htf.sort_values("close_time", kind="mergesort")
        .loc[:, ["close_time", *columns]]
        .rename(columns={col: f"{col}_{suffix}" for col in columns})
        .reset_index(drop=True)
    )

    merged = pd.merge_asof(
        left,
        right,
        on="close_time",
        direction="backward",
        allow_exact_matches=True,
    )
    return merged


def build_timeframe_stack(
    base_frame: pd.DataFrame,
    base_timeframe: Timeframe,
    targets: tuple[Timeframe, ...],
    symbol: Symbol,
) -> dict[Timeframe, pd.DataFrame]:
    """Build every requested higher timeframe from one base frame.

    Deriving all timeframes from a single normalized base — rather than fetching
    each independently — guarantees they are mutually consistent: a 1H bar is
    exactly the twelve 5M bars beneath it, by construction. Independently fetched
    timeframes routinely disagree at the margins, and that disagreement shows up as
    phantom ICT structure.
    """
    stack: dict[Timeframe, pd.DataFrame] = {base_timeframe: with_close_time(base_frame, base_timeframe)}
    for target in targets:
        if target == base_timeframe:
            continue
        stack[target] = resample(base_frame, base_timeframe, target, symbol)
    return stack


def _empty_resampled() -> pd.DataFrame:
    frame = empty_frame()
    frame["close_time"] = pd.Series(dtype="datetime64[ns, UTC]")
    return frame[list(RESAMPLED_COLUMNS)]


def _require_close_time(frame: pd.DataFrame) -> None:
    if "close_time" not in frame.columns:
        raise ValueError(
            "frame is missing 'close_time'; call with_close_time() or resample() first. "
            "Point-in-time alignment must never join on the open timestamp."
        )
