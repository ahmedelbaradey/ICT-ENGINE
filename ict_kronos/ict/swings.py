"""Swing highs and lows — n-bar fractal pivots with an explicit confirmation lag.

Full documentation: ``docs/ict/swings.md``.

**The whole point of this module is the lag.** A swing high at bar *i* is not a swing
until ``right`` further bars have failed to exceed it. Charting software draws the
pivot at bar *i*, and that is precisely the timestamp a naive implementation records —
making every downstream feature ``right`` bars early. This is the single most common
look-ahead bug in retail ICT research.

So two timestamps, always, and never collapsed:

* ``event_timestamp`` — the pivot bar's open time. Where it sits on the chart.
* ``confirmation_timestamp`` — the **close time of bar ``i + right``**. The earliest
  instant the pivot could be known.

**Immutability.** A confirmed swing never changes. The fractal rule evaluates the
bounded window ``[i - left, i + right]``, every bar of which has closed by the
confirmation instant, so no later candle can revise the verdict. In particular a
subsequent higher high does **not** invalidate an earlier swing high — swings are
*local* pivots, not running extremes. That is a deliberate, tested property, not an
accident of implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

import numpy as np
import pandas as pd

from ..app.logging import get_logger
from ..data.resampler import with_close_time
from ..domain import Symbol, Timeframe
from .contract import Direction, EventType, IctEvent, filter_observable

logger = get_logger(__name__)


class TiePolicy(StrEnum):
    """How to resolve a plateau — consecutive bars sharing the extreme price.

    Flat tops and bottoms are common in FX (and ubiquitous on illiquid bars), so this
    cannot be left to comparison-operator accident. Each policy is a different, valid
    reading; the default is stated and the rest are configuration.

    ``FIRST``  strict left, non-strict right → the plateau's **first** bar wins.
    ``LAST``   non-strict left, strict right → the plateau's **last** bar wins.
    ``STRICT`` strict both sides → a plateau yields **no** swing at all.
    ``ALL``    non-strict both sides → **every** plateau bar is a swing.
    """

    FIRST = "first"
    LAST = "last"
    STRICT = "strict"
    ALL = "all"


@dataclass(frozen=True)
class SwingConfig:
    """Fractal parameters. Configuration, never literals in the detector.

    ``left``/``right`` are bar counts, not durations — the window is positional over
    the bars present, which matters across market gaps (see ``docs/ict/swings.md``).
    """

    left: int = 2
    right: int = 2
    tie_policy: TiePolicy = TiePolicy.FIRST

    def __post_init__(self) -> None:
        if self.left < 1:
            raise ValueError(f"left must be >= 1; got {self.left}")
        if self.right < 1:
            # right=0 would mean a swing is confirmed by its own bar — no lag, and
            # therefore not a swing at all, just "this bar's high". Refused rather
            # than silently permitting a zero-lag pivot into the feature set.
            raise ValueError(
                f"right must be >= 1; got {self.right}. A swing requires at least one "
                f"subsequent bar to confirm it — right=0 would mean zero confirmation lag."
            )

    @property
    def window_size(self) -> int:
        """Bars needed before any swing can exist."""
        return self.left + self.right + 1

    def as_dict(self) -> dict:
        return {"left": self.left, "right": self.right, "tie_policy": self.tie_policy.value}


@dataclass(frozen=True)
class SwingPoint:
    """One confirmed fractal pivot."""

    symbol: str
    timeframe: str
    direction: Direction
    #: The pivot bar's open time — where the swing sits on the chart.
    event_timestamp: datetime
    #: Close time of bar ``index + right`` — when the swing became knowable.
    confirmation_timestamp: datetime
    price_level: float
    #: The most extreme neighbouring price inside the window that the pivot beat.
    reference_level: float
    #: Prominence in instrument points: how far the pivot stands clear of that
    #: neighbour. Zero on a plateau under FIRST/ALL — meaningful, not a defect.
    strength: float
    #: Positional index in the source frame. Diagnostics only; never a join key.
    index: int
    bars_to_confirm: int

    @property
    def is_high(self) -> bool:
        return self.direction is Direction.BULLISH

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction.value,
            "event_timestamp": self.event_timestamp.isoformat(),
            "confirmation_timestamp": self.confirmation_timestamp.isoformat(),
            "price_level": self.price_level,
            "reference_level": self.reference_level,
            "strength": self.strength,
            "index": self.index,
            "bars_to_confirm": self.bars_to_confirm,
        }


@dataclass
class SwingDetector:
    """Deterministic n-bar fractal swing detection."""

    config: SwingConfig = SwingConfig()

    # ------------------------------------------------------------------ core

    def _pivot_mask(self, series: pd.Series, *, find_high: bool) -> tuple[np.ndarray, np.ndarray]:
        """``(is_pivot, neighbour_extreme)`` for every position in ``series``.

        Rolling windows rather than a Python loop: a multi-year 1M series is millions
        of bars, and the vectorised form is checked against a naive reference
        implementation in the tests.
        """
        left, right = self.config.left, self.config.right

        if find_high:
            # left_extreme[i]  = max(series[i-left : i])
            # right_extreme[i] = max(series[i+1 : i+right+1])
            left_extreme = series.rolling(left).max().shift(1)
            right_extreme = series.rolling(right).max().shift(-right)
        else:
            left_extreme = series.rolling(left).min().shift(1)
            right_extreme = series.rolling(right).min().shift(-right)

        values = series.to_numpy(dtype="float64")
        left_arr = left_extreme.to_numpy(dtype="float64")
        right_arr = right_extreme.to_numpy(dtype="float64")

        # NaN at the edges means the window is incomplete: no pivot can be decided.
        complete = ~np.isnan(left_arr) & ~np.isnan(right_arr)

        policy = self.config.tie_policy
        if find_high:
            strict_left = values > left_arr
            loose_left = values >= left_arr
            strict_right = values > right_arr
            loose_right = values >= right_arr
        else:
            strict_left = values < left_arr
            loose_left = values <= left_arr
            strict_right = values < right_arr
            loose_right = values <= right_arr

        if policy is TiePolicy.FIRST:
            is_pivot = strict_left & loose_right
        elif policy is TiePolicy.LAST:
            is_pivot = loose_left & strict_right
        elif policy is TiePolicy.STRICT:
            is_pivot = strict_left & strict_right
        else:  # TiePolicy.ALL
            is_pivot = loose_left & loose_right

        is_pivot = is_pivot & complete

        # The neighbour the pivot had to beat — becomes reference_level.
        neighbour = np.fmax(left_arr, right_arr) if find_high else np.fmin(left_arr, right_arr)
        return is_pivot, neighbour

    # ---------------------------------------------------------------- public

    def detect(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[SwingPoint]:
        """Every swing **confirmed within the observed data**, in confirmation order.

        A pivot whose confirmation bar has not yet closed is not returned. That single
        rule is what makes batch detection equal streaming replay: the detector can
        never report something the live path could not have seen.
        """
        work = with_close_time(frame, timeframe) if len(frame) else with_close_time(frame, timeframe)
        if len(work) < self.config.window_size:
            # Insufficient history is not an error — it is simply too early.
            return []

        work = work.sort_values("timestamp", kind="mergesort").reset_index(drop=True)
        point = symbol.spec.point_value
        right = self.config.right

        timestamps = work["timestamp"].to_numpy()
        close_times = work["close_time"].to_numpy()

        swings: list[SwingPoint] = []
        for find_high, direction, column in (
            (True, Direction.BULLISH, "high"),
            (False, Direction.BEARISH, "low"),
        ):
            is_pivot, neighbour = self._pivot_mask(work[column], find_high=find_high)
            prices = work[column].to_numpy(dtype="float64")

            for index in np.flatnonzero(is_pivot):
                confirm_index = index + right
                if confirm_index >= len(work):  # pragma: no cover - mask already excludes
                    continue

                level = float(prices[index])
                reference = float(neighbour[index])
                prominence = (level - reference) if find_high else (reference - level)

                swings.append(
                    SwingPoint(
                        symbol=symbol.value,
                        timeframe=timeframe.value,
                        direction=direction,
                        event_timestamp=pd.Timestamp(timestamps[index]).to_pydatetime(),
                        confirmation_timestamp=pd.Timestamp(close_times[confirm_index]).to_pydatetime(),
                        price_level=level,
                        reference_level=reference,
                        strength=prominence / point if point else prominence,
                        index=int(index),
                        bars_to_confirm=right,
                    )
                )

        swings.sort(key=lambda s: (s.confirmation_timestamp, s.event_timestamp, s.direction.value))
        logger.info(
            "swings %s %s: %d pivot(s) from %d bar(s) (left=%d right=%d tie=%s)",
            symbol.value,
            timeframe.value,
            len(swings),
            len(work),
            self.config.left,
            self.config.right,
            self.config.tie_policy.value,
        )
        return swings

    def events(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[IctEvent]:
        """Contract events for every confirmed swing."""
        events = [
            IctEvent(
                symbol=swing.symbol,
                timeframe=swing.timeframe,
                event_type=EventType.SWING_HIGH if swing.is_high else EventType.SWING_LOW,
                direction=swing.direction,
                event_timestamp=swing.event_timestamp,
                confirmation_timestamp=swing.confirmation_timestamp,
                price_level=swing.price_level,
                reference_level=swing.reference_level,
                strength=swing.strength,
                metadata={
                    "left": self.config.left,
                    "right": self.config.right,
                    "tie_policy": self.config.tie_policy.value,
                    "bars_to_confirm": swing.bars_to_confirm,
                    "index": swing.index,
                },
            )
            for swing in self.detect(frame, symbol, timeframe)
        ]
        return events

    def observable_at(
        self, frame: pd.DataFrame, as_of: datetime, symbol: Symbol, timeframe: Timeframe
    ) -> list[SwingPoint]:
        """Swings a decision timestamped ``as_of`` may use.

        Equivalent to detecting over the full frame and dropping anything not yet
        confirmed — and provably equal to detecting over only the bars visible at
        ``as_of``, which the tests assert.
        """
        if as_of.tzinfo is None:
            raise ValueError(f"as_of must be timezone-aware UTC; got naive {as_of!r}")
        return filter_observable(self.detect(frame, symbol, timeframe), as_of)

    def with_config(self, config: SwingConfig) -> SwingDetector:
        """A detector with different fractal parameters. Configuration, not a subclass."""
        return replace(self, config=config)


def reference_pivots(frame: pd.DataFrame, config: SwingConfig, *, find_high: bool) -> list[int]:
    """Naive O(n·window) reference implementation, for testing only.

    The vectorised rolling-window path in :meth:`SwingDetector._pivot_mask` is fast but
    its correctness is not self-evident — `shift(-right)` in particular is easy to get
    off by one. This transparently-correct version exists purely so the tests can prove
    the two agree. **Never call it on a real series.**
    """
    column = "high" if find_high else "low"
    values = frame[column].to_numpy(dtype="float64")
    left, right, policy = config.left, config.right, config.tie_policy

    pivots: list[int] = []
    for i in range(left, len(values) - right):
        centre = values[i]
        lefts = values[i - left : i]
        rights = values[i + 1 : i + right + 1]

        if find_high:
            strict_l, loose_l = centre > lefts.max(), centre >= lefts.max()
            strict_r, loose_r = centre > rights.max(), centre >= rights.max()
        else:
            strict_l, loose_l = centre < lefts.min(), centre <= lefts.min()
            strict_r, loose_r = centre < rights.min(), centre <= rights.min()

        if policy is TiePolicy.FIRST:
            ok = strict_l and loose_r
        elif policy is TiePolicy.LAST:
            ok = loose_l and strict_r
        elif policy is TiePolicy.STRICT:
            ok = strict_l and strict_r
        else:
            ok = loose_l and loose_r

        if ok:
            pivots.append(i)
    return pivots
