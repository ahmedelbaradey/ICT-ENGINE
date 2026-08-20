"""Liquidity levels and sweeps.

Definitions, the day/week calendar, the lifecycle and every ambiguity we did NOT
silently resolve are in ``docs/ict/liquidity.md``. Read that first.

**The architectural distinction this module exists to preserve:**

    A liquidity LEVEL is not a liquidity SWEEP.

A *level* is an observable price reference with a lifetime. A *sweep* is a later
price event interacting with an already-observable level. They are separate types
with separate timestamps and are never collapsed — otherwise "what liquidity exists
now?" and "what was just taken?" become the same unanswerable question.

**Reuse, not reimplementation.** Sessions come from R2-01's ``SessionDetector`` and
swings from R2-02's ``SwingDetector``. Session boundaries are not re-derived here, and
only *confirmed* swings and *completed* sessions are ever consumed.

**The observability chain.** A period's high is not knowable as that period's *final*
high until the period ends; an equal-highs pair is not knowable until the later of its
two swings confirms; a sweep is not knowable until its bar closes. Each is enforced by
construction rather than by convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from enum import StrEnum

import pandas as pd

from ..app.logging import get_logger
from ..data.resampler import with_close_time
from ..domain import Symbol, Timeframe
from .contract import Direction, EventStatus, EventType, IctEvent
from .sessions import SessionDefinition, SessionDetector, SessionKind, resolve_windows
from .swings import SwingConfig, SwingDetector, SwingPoint

logger = get_logger(__name__)


class LiquidityType(StrEnum):
    """What kind of resting liquidity a level represents."""

    SWING_HIGH = "swing_high"
    SWING_LOW = "swing_low"
    EQUAL_HIGHS = "equal_highs"
    EQUAL_LOWS = "equal_lows"
    PREVIOUS_DAY_HIGH = "previous_day_high"
    PREVIOUS_DAY_LOW = "previous_day_low"
    PREVIOUS_WEEK_HIGH = "previous_week_high"
    PREVIOUS_WEEK_LOW = "previous_week_low"
    SESSION_HIGH = "session_high"
    SESSION_LOW = "session_low"

    @property
    def is_high(self) -> bool:
        return self in _HIGH_TYPES


_HIGH_TYPES = frozenset(
    {
        LiquidityType.SWING_HIGH,
        LiquidityType.EQUAL_HIGHS,
        LiquidityType.PREVIOUS_DAY_HIGH,
        LiquidityType.PREVIOUS_WEEK_HIGH,
        LiquidityType.SESSION_HIGH,
    }
)

#: Level type -> the contract event type it is published as.
_EVENT_TYPE = {
    LiquidityType.SWING_HIGH: EventType.SWING_HIGH,
    LiquidityType.SWING_LOW: EventType.SWING_LOW,
    LiquidityType.EQUAL_HIGHS: EventType.EQUAL_HIGHS,
    LiquidityType.EQUAL_LOWS: EventType.EQUAL_LOWS,
    LiquidityType.PREVIOUS_DAY_HIGH: EventType.PREVIOUS_DAY_HIGH,
    LiquidityType.PREVIOUS_DAY_LOW: EventType.PREVIOUS_DAY_LOW,
    LiquidityType.PREVIOUS_WEEK_HIGH: EventType.PREVIOUS_WEEK_HIGH,
    LiquidityType.PREVIOUS_WEEK_LOW: EventType.PREVIOUS_WEEK_LOW,
    LiquidityType.SESSION_HIGH: EventType.SESSION_HIGH,
    LiquidityType.SESSION_LOW: EventType.SESSION_LOW,
}


class LiquiditySide(StrEnum):
    """Which side of the book the resting orders sit on.

    **Fixed at creation, by type; it never changes as price moves.** Inferring the side
    from current price would make the same historical level flip sides as price
    oscillates, so its classification would depend on when you asked — destroying
    immutability. What changes when price passes through is the *status*, not the side.
    """

    BUY_SIDE = "buy_side"  # rests ABOVE price — buy stops
    SELL_SIDE = "sell_side"  # rests BELOW price — sell stops


class LiquidityStatus(StrEnum):
    """Lifecycle.

    Three states carry the meaning: ``PENDING`` (created, not yet observable),
    ``ACTIVE`` (observable, unswept) and ``SWEPT`` (taken; terminal, and it IS the
    consumption — see docs §6). ``APPROACHED`` is an OPTIONAL refinement of ACTIVE,
    off unless ``approach_tolerance_points`` is set; it never gates anything and a
    level in it is still fully usable.
    """

    PENDING = "pending"
    ACTIVE = "active"
    APPROACHED = "approached"
    SWEPT = "swept"


@dataclass(frozen=True)
class LiquidityConfig:
    """Liquidity parameters. Configuration, never literals in the detector."""

    #: Two swings are "equal" within this many instrument points. Never float ==.
    equal_tolerance_points: float = 1.0
    #: How many positions apart, in the confirmed same-type swing sequence, two swings
    #: may be and still form an equal-highs pair. 1 = adjacent only.
    equal_max_swing_distance: int = 1
    #: Price must exceed the level by MORE than this to sweep it.
    sweep_tolerance_points: float = 0.0
    #: Price within this many points of a level marks it APPROACHED. ``None`` (the
    #: default) disables approach tracking entirely — it is an optional refinement,
    #: not part of the essential lifecycle.
    approach_tolerance_points: float | None = None
    #: The trading-day boundary. 17:00 America/New_York is the FX/broker day and
    #: matches the reopen times observed in the Phase 1.5 data (docs §3).
    day_timezone: str = "America/New_York"
    day_boundary_local: time = time(17, 0)
    #: Swing highs/lows are themselves liquidity. Disable to keep only the
    #: period/session/equal levels.
    include_swing_levels: bool = True

    def __post_init__(self) -> None:
        if self.equal_max_swing_distance < 1:
            raise ValueError(f"equal_max_swing_distance must be >= 1; got {self.equal_max_swing_distance}")
        for name in ("equal_tolerance_points", "sweep_tolerance_points"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0; got {getattr(self, name)}")
        if self.approach_tolerance_points is not None and self.approach_tolerance_points < 0:
            raise ValueError(
                f"approach_tolerance_points must be >= 0 or None; got {self.approach_tolerance_points}"
            )

    @property
    def day_definition(self) -> SessionDefinition:
        """The trading day as a session window.

        A day is exactly a 24-hour window anchored at a LOCAL time, which is what
        ``SessionDefinition`` already models — including DST. Expressing it this way
        means the day boundary is not a second, divergent calendar implementation.
        ``end == start`` makes it cross midnight, so the window runs to the same local
        time on the following day.
        """
        return SessionDefinition(
            name="trading_day",
            timezone=self.day_timezone,
            start_local=self.day_boundary_local,
            end_local=self.day_boundary_local,
            kind=SessionKind.SESSION,
        )

    def as_dict(self) -> dict:
        return {
            "equal_tolerance_points": self.equal_tolerance_points,
            "equal_max_swing_distance": self.equal_max_swing_distance,
            "sweep_tolerance_points": self.sweep_tolerance_points,
            "approach_tolerance_points": self.approach_tolerance_points,
            "day_timezone": self.day_timezone,
            "day_boundary_local": self.day_boundary_local.strftime("%H:%M"),
            "include_swing_levels": self.include_swing_levels,
        }


@dataclass(frozen=True)
class LiquidityLevel:
    """An observable price reference where liquidity is presumed to rest.

    Immutable. A later bar can never revise a confirmed level — status transitions are
    tracked on the analysis, not by mutating the level.
    """

    level_id: str
    symbol: str
    timeframe: str
    liquidity_type: LiquidityType
    side: LiquiditySide
    price_level: float
    #: When the underlying price action occurred (the extreme bar, or the later pivot).
    created_timestamp: datetime
    #: The earliest instant the level could be known. NEVER earlier than the period /
    #: pair that defines it completing.
    confirmation_timestamp: datetime
    #: Pivots this level was built from, so R2-07 can trace provenance.
    source_swing_timestamps: tuple[datetime, ...] = ()
    #: The period/session window that produced it, where applicable.
    period_start: datetime | None = None
    period_end: datetime | None = None
    period_label: str | None = None
    tolerance_points: float | None = None

    @property
    def is_buy_side(self) -> bool:
        return self.side is LiquiditySide.BUY_SIDE

    @property
    def direction(self) -> Direction:
        """Buy-side liquidity sits above (bullish target); sell-side below."""
        return Direction.BULLISH if self.is_buy_side else Direction.BEARISH

    def distance_from(self, price: float, point_value: float) -> float:
        """Signed distance in points: positive when the level is still out of reach."""
        delta = (self.price_level - price) if self.is_buy_side else (price - self.price_level)
        return delta / point_value if point_value else delta

    def as_dict(self) -> dict:
        return {
            "level_id": self.level_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "liquidity_type": self.liquidity_type.value,
            "side": self.side.value,
            "price_level": self.price_level,
            "created_timestamp": self.created_timestamp.isoformat(),
            "confirmation_timestamp": self.confirmation_timestamp.isoformat(),
            "source_swing_timestamps": [t.isoformat() for t in self.source_swing_timestamps],
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "period_label": self.period_label,
            "tolerance_points": self.tolerance_points,
        }


@dataclass(frozen=True)
class LiquiditySweep:
    """A later price event that took an already-observable level."""

    level_id: str
    symbol: str
    timeframe: str
    liquidity_type: LiquidityType
    side: LiquiditySide
    #: The sweeping bar's open — when the interaction occurred.
    event_timestamp: datetime
    #: The sweeping bar's close — when it became knowable. Intrabar sequencing is
    #: unknowable from bar data, so even a wick sweep confirms only at the close.
    confirmation_timestamp: datetime
    price_level: float
    #: How far beyond the level price reached, in points.
    penetration_points: float
    #: True when the bar CLOSED beyond the level (a break-through) rather than closing
    #: back inside (the textbook stop-hunt rejection). Recorded rather than decided.
    closed_beyond: bool
    extreme_price: float
    bar_index: int

    @property
    def direction(self) -> Direction:
        """A buy-side sweep is an upward move; a sell-side sweep, downward."""
        return Direction.BULLISH if self.side is LiquiditySide.BUY_SIDE else Direction.BEARISH

    @property
    def is_rejection(self) -> bool:
        return not self.closed_beyond

    def as_dict(self) -> dict:
        return {
            "level_id": self.level_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "liquidity_type": self.liquidity_type.value,
            "side": self.side.value,
            "event_timestamp": self.event_timestamp.isoformat(),
            "confirmation_timestamp": self.confirmation_timestamp.isoformat(),
            "price_level": self.price_level,
            "penetration_points": self.penetration_points,
            "closed_beyond": self.closed_beyond,
            "is_rejection": self.is_rejection,
            "extreme_price": self.extreme_price,
            "bar_index": self.bar_index,
        }


@dataclass(frozen=True)
class PendingPeriod:
    """A period in progress that has NOT yet produced a level.

    Exposed explicitly rather than emitted as a confirmed level. "The current day's
    high so far" is real information a live system has, but it is *not* a previous-day
    high, and conflating them is exactly the leak this module exists to prevent.
    """

    kind: str  # "day" | "week" | "session"
    label: str
    window_start: datetime
    window_end: datetime
    running_high: float | None
    running_low: float | None
    bar_count: int

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "label": self.label,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "running_high": self.running_high,
            "running_low": self.running_low,
            "bar_count": self.bar_count,
        }


@dataclass
class LiquidityAnalysis:
    """Levels, sweeps, and the end-of-run lifecycle state."""

    levels: list[LiquidityLevel] = field(default_factory=list)
    sweeps: list[LiquiditySweep] = field(default_factory=list)
    status: dict[str, LiquidityStatus] = field(default_factory=dict)
    approached: dict[str, datetime] = field(default_factory=dict)
    swept_at: dict[str, datetime] = field(default_factory=dict)
    #: Periods in progress at the end of the observed data. Never levels.
    pending: list[PendingPeriod] = field(default_factory=list)

    def state_of(self, level_id: str) -> dict | None:
        """Everything R2-07 / ML needs about one level, in one call.

        Answers, per the R2-04 data-model requirement: what is the level, what created
        it, when did it occur, when did it become observable, which side, what source
        type, is it still active, was it swept, when, how far did price penetrate, and
        was there rejection.
        """
        level = self.level_by_id(level_id)
        if level is None:
            return None
        sweep = next((s for s in self.sweeps if s.level_id == level_id), None)
        return {
            **level.as_dict(),
            "status": self.status.get(level_id, LiquidityStatus.ACTIVE).value,
            "is_active": self.status.get(level_id) is not LiquidityStatus.SWEPT,
            "approached_at": (self.approached[level_id].isoformat() if level_id in self.approached else None),
            "swept": sweep is not None,
            "swept_at": (self.swept_at[level_id].isoformat() if level_id in self.swept_at else None),
            "penetration_points": sweep.penetration_points if sweep else None,
            "closed_beyond": sweep.closed_beyond if sweep else None,
            "is_rejection": sweep.is_rejection if sweep else None,
        }

    def level_by_id(self, level_id: str) -> LiquidityLevel | None:
        return next((x for x in self.levels if x.level_id == level_id), None)

    def active_at(self, as_of: datetime) -> list[LiquidityLevel]:
        """Levels a decision at ``as_of`` may treat as resting liquidity.

        Observable, and not yet swept **as of that instant** — a level swept later is
        still active now, which is exactly the point-in-time question R2-07 asks.
        """
        if as_of.tzinfo is None:
            raise ValueError(f"as_of must be timezone-aware UTC; got naive {as_of!r}")
        swept_before = {s.level_id for s in self.sweeps if s.confirmation_timestamp <= as_of}
        return [
            level
            for level in self.levels
            if level.confirmation_timestamp <= as_of and level.level_id not in swept_before
        ]

    def swept_by(self, as_of: datetime) -> list[LiquiditySweep]:
        if as_of.tzinfo is None:
            raise ValueError(f"as_of must be timezone-aware UTC; got naive {as_of!r}")
        return [s for s in self.sweeps if s.confirmation_timestamp <= as_of]


@dataclass
class LiquidityDetector:
    """Deterministic liquidity levels and sweeps over confirmed inputs."""

    config: LiquidityConfig = LiquidityConfig()
    swing_config: SwingConfig = SwingConfig()
    session_detector: SessionDetector = field(default_factory=SessionDetector)

    @property
    def swing_detector(self) -> SwingDetector:
        return SwingDetector(self.swing_config)

    # ------------------------------------------------------------ level build

    def _swing_levels(self, swings: list[SwingPoint]) -> list[LiquidityLevel]:
        """Confirmed swings are themselves resting liquidity."""
        if not self.config.include_swing_levels:
            return []
        return [
            LiquidityLevel(
                level_id=f"swing:{s.direction.value}:{s.event_timestamp.isoformat()}",
                symbol=s.symbol,
                timeframe=s.timeframe,
                liquidity_type=LiquidityType.SWING_HIGH if s.is_high else LiquidityType.SWING_LOW,
                side=LiquiditySide.BUY_SIDE if s.is_high else LiquiditySide.SELL_SIDE,
                price_level=s.price_level,
                created_timestamp=s.event_timestamp,
                confirmation_timestamp=s.confirmation_timestamp,
                source_swing_timestamps=(s.event_timestamp,),
            )
            for s in swings
        ]

    def _equal_levels(self, swings: list[SwingPoint], point: float) -> list[LiquidityLevel]:
        """Pairs of confirmed same-type swings within tolerance.

        ``confirmation`` is the **later** of the two — if the swings confirm at
        different times the level cannot be known until the later information is
        available. Across a market gap that ordering can invert, so ``max`` is taken
        explicitly rather than assumed.
        """
        tolerance = self.config.equal_tolerance_points * point
        levels: list[LiquidityLevel] = []

        for is_high in (True, False):
            same_type = [s for s in swings if s.is_high == is_high]
            same_type.sort(key=lambda s: s.event_timestamp)

            for index, later in enumerate(same_type):
                lower = max(0, index - self.config.equal_max_swing_distance)
                for earlier in same_type[lower:index]:
                    if abs(later.price_level - earlier.price_level) > tolerance:
                        continue

                    # The extreme of the pair: stops rest beyond the furthest touch,
                    # so that is the price a sweep must exceed.
                    price = (
                        max(later.price_level, earlier.price_level)
                        if is_high
                        else min(later.price_level, earlier.price_level)
                    )
                    kind = LiquidityType.EQUAL_HIGHS if is_high else LiquidityType.EQUAL_LOWS
                    levels.append(
                        LiquidityLevel(
                            level_id=(
                                f"equal:{kind.value}:{earlier.event_timestamp.isoformat()}"
                                f":{later.event_timestamp.isoformat()}"
                            ),
                            symbol=later.symbol,
                            timeframe=later.timeframe,
                            liquidity_type=kind,
                            side=LiquiditySide.BUY_SIDE if is_high else LiquiditySide.SELL_SIDE,
                            price_level=price,
                            created_timestamp=later.event_timestamp,
                            confirmation_timestamp=max(
                                later.confirmation_timestamp, earlier.confirmation_timestamp
                            ),
                            source_swing_timestamps=(
                                earlier.event_timestamp,
                                later.event_timestamp,
                            ),
                            tolerance_points=self.config.equal_tolerance_points,
                        )
                    )
        return levels

    def _session_levels(
        self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe
    ) -> list[LiquidityLevel]:
        """R2-01 completed sessions only. Running session state is NOT a level."""
        levels: list[LiquidityLevel] = []
        for occurrence in self.session_detector.detect(frame, symbol, timeframe):
            window = occurrence.window
            for kind, price, moment in (
                (LiquidityType.SESSION_HIGH, occurrence.high_price, occurrence.high_timestamp),
                (LiquidityType.SESSION_LOW, occurrence.low_price, occurrence.low_timestamp),
            ):
                levels.append(
                    LiquidityLevel(
                        level_id=(f"session:{kind.value}:{window.name}:{window.local_date.isoformat()}"),
                        symbol=occurrence.symbol,
                        timeframe=occurrence.timeframe,
                        liquidity_type=kind,
                        side=(
                            LiquiditySide.BUY_SIDE
                            if kind is LiquidityType.SESSION_HIGH
                            else LiquiditySide.SELL_SIDE
                        ),
                        price_level=price,
                        created_timestamp=moment,
                        # R2-01's semantics, unchanged: the session's end.
                        confirmation_timestamp=occurrence.confirmation_timestamp,
                        period_start=window.start_utc,
                        period_end=window.end_utc,
                        period_label=f"{window.name}:{window.local_date.isoformat()}",
                    )
                )
        return levels

    def _day_windows(self, work: pd.DataFrame) -> list:
        """Completed trading-day windows that actually contained bars."""
        if len(work) == 0:
            return []
        return resolve_windows(
            (self.config.day_definition,),
            work["timestamp"].iloc[0].to_pydatetime(),
            work["close_time"].iloc[-1].to_pydatetime(),
        )

    def _period_levels(
        self, work: pd.DataFrame, symbol: Symbol, timeframe: Timeframe
    ) -> tuple[list[LiquidityLevel], list[PendingPeriod]]:
        """PDH/PDL from completed days, PWH/PWL from completed weeks.

        A period is only used once the observed data extends to or past its end — the
        R2-01 rule. A day's high is not knowable as the day's FINAL high until the day
        is over, which is precisely what the confirmation timestamp encodes.
        """
        levels: list[LiquidityLevel] = []
        pending: list[PendingPeriod] = []
        if len(work) == 0:
            return levels, pending

        observed_end = work["close_time"].iloc[-1]
        # Per-week accumulation, keyed by the Sunday the trading week is anchored on.
        weeks: dict[date, dict] = {}

        for window in self._day_windows(work):
            members = work.loc[
                (work["timestamp"] >= pd.Timestamp(window.start_utc))
                & (work["close_time"] <= pd.Timestamp(window.end_utc))
            ]
            if len(members) == 0:
                # Weekend or holiday: no bars, so no level. Absence preserved.
                continue

            complete = observed_end >= pd.Timestamp(window.end_utc)
            high_row = members.loc[members["high"].idxmax()]
            low_row = members.loc[members["low"].idxmin()]

            if complete:
                label = window.local_date.isoformat()
                for kind, price, moment in (
                    (LiquidityType.PREVIOUS_DAY_HIGH, float(members["high"].max()), high_row["timestamp"]),
                    (LiquidityType.PREVIOUS_DAY_LOW, float(members["low"].min()), low_row["timestamp"]),
                ):
                    levels.append(
                        LiquidityLevel(
                            level_id=f"day:{kind.value}:{label}",
                            symbol=symbol.value,
                            timeframe=timeframe.value,
                            liquidity_type=kind,
                            side=(
                                LiquiditySide.BUY_SIDE
                                if kind is LiquidityType.PREVIOUS_DAY_HIGH
                                else LiquiditySide.SELL_SIDE
                            ),
                            price_level=price,
                            created_timestamp=moment.to_pydatetime(),
                            # The day's END — never the extreme bar.
                            confirmation_timestamp=window.end_utc,
                            period_start=window.start_utc,
                            period_end=window.end_utc,
                            period_label=f"day:{label}",
                        )
                    )

            else:
                # In progress: real information, but NOT a previous-day high. Exposed
                # separately so nothing pretends it is confirmed.
                pending.append(
                    PendingPeriod(
                        kind="day",
                        label=f"day:{window.local_date.isoformat()}",
                        window_start=window.start_utc,
                        window_end=window.end_utc,
                        running_high=float(members["high"].max()),
                        running_low=float(members["low"].min()),
                        bar_count=len(members),
                    )
                )

            if not _in_trading_week(window.local_date):
                # A day anchored on Friday runs Fri 17:00 -> Sat 17:00, which begins
                # exactly when the trading week ENDS. Folding it into that week would
                # let a "week high" post-date the week's own close. Real FX data has
                # no bars there so it never shows, but an instrument that trades
                # Friday evening would silently corrupt every PWH/PWL.
                continue

            anchor = _week_anchor(window.local_date)
            bucket = weeks.setdefault(
                anchor,
                {
                    "high": None,
                    "low": None,
                    "high_ts": None,
                    "low_ts": None,
                    "start": window.start_utc,
                    "end": window.end_utc,
                },
            )
            day_high, day_low = float(members["high"].max()), float(members["low"].min())
            if bucket["high"] is None or day_high > bucket["high"]:
                bucket["high"], bucket["high_ts"] = day_high, high_row["timestamp"]
            if bucket["low"] is None or day_low < bucket["low"]:
                bucket["low"], bucket["low_ts"] = day_low, low_row["timestamp"]
            bucket["start"] = min(bucket["start"], window.start_utc)
            bucket["end"] = max(bucket["end"], window.end_utc)

        for anchor, bucket in weeks.items():
            # The trading week ends with the Thursday-anchored day window, i.e. Friday
            # at the day boundary. Only a completed week yields a level.
            week_end = _week_end(anchor, self.config)
            if observed_end < pd.Timestamp(week_end):
                pending.append(
                    PendingPeriod(
                        kind="week",
                        label=f"week:{anchor.isoformat()}",
                        window_start=bucket["start"],
                        window_end=week_end,
                        running_high=bucket["high"],
                        running_low=bucket["low"],
                        bar_count=0,
                    )
                )
                continue

            label = anchor.isoformat()
            for kind, price, moment in (
                (LiquidityType.PREVIOUS_WEEK_HIGH, bucket["high"], bucket["high_ts"]),
                (LiquidityType.PREVIOUS_WEEK_LOW, bucket["low"], bucket["low_ts"]),
            ):
                levels.append(
                    LiquidityLevel(
                        level_id=f"week:{kind.value}:{label}",
                        symbol=symbol.value,
                        timeframe=timeframe.value,
                        liquidity_type=kind,
                        side=(
                            LiquiditySide.BUY_SIDE
                            if kind is LiquidityType.PREVIOUS_WEEK_HIGH
                            else LiquiditySide.SELL_SIDE
                        ),
                        price_level=price,
                        created_timestamp=moment.to_pydatetime(),
                        confirmation_timestamp=week_end,
                        period_start=bucket["start"],
                        period_end=week_end,
                        period_label=f"week:{label}",
                    )
                )
        return levels, pending

    # ------------------------------------------------------------------ core

    def analyse(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> LiquidityAnalysis:
        """Build levels, then walk bars forward sweeping only observable ones."""
        analysis = LiquidityAnalysis()
        if len(frame) == 0:
            return analysis

        work = with_close_time(frame, timeframe).sort_values("timestamp", kind="mergesort")
        work = work.reset_index(drop=True)
        point = symbol.spec.point_value

        swings = self.swing_detector.detect(frame, symbol, timeframe)
        period_levels, pending = self._period_levels(work, symbol, timeframe)
        analysis.pending = pending
        levels = [
            *self._swing_levels(swings),
            *self._equal_levels(swings, point),
            *self._session_levels(frame, symbol, timeframe),
            *period_levels,
        ]
        levels.sort(key=lambda x: (x.confirmation_timestamp, x.level_id))
        analysis.levels = levels
        analysis.status = {x.level_id: LiquidityStatus.PENDING for x in levels}
        if not levels:
            return analysis

        sweep_tol = self.config.sweep_tolerance_points * point
        approach_tol = (
            None
            if self.config.approach_tolerance_points is None
            else self.config.approach_tolerance_points * point
        )

        highs = work["high"].to_numpy(dtype="float64")
        lows = work["low"].to_numpy(dtype="float64")
        closes = work["close"].to_numpy(dtype="float64")
        open_times = work["timestamp"].to_numpy()
        close_times = work["close_time"].to_numpy()

        active: list[LiquidityLevel] = []
        cursor = 0

        for index in range(len(work)):
            now = close_times[index]

            # 1. Admit levels that have become observable BY THIS BAR'S CLOSE.
            while cursor < len(levels) and pd.Timestamp(
                levels[cursor].confirmation_timestamp
            ) <= pd.Timestamp(now):
                level = levels[cursor]
                cursor += 1
                active.append(level)
                analysis.status[level.level_id] = LiquidityStatus.ACTIVE

            # 2. Sweep / approach checks against the observable, unswept set.
            still_active: list[LiquidityLevel] = []
            for level in active:
                if level.is_buy_side:
                    extreme, exceeded = highs[index], highs[index] > level.price_level + sweep_tol
                    closed_beyond = closes[index] > level.price_level
                    near = approach_tol is not None and highs[index] >= level.price_level - approach_tol
                else:
                    extreme, exceeded = lows[index], lows[index] < level.price_level - sweep_tol
                    closed_beyond = closes[index] < level.price_level
                    near = approach_tol is not None and lows[index] <= level.price_level + approach_tol

                if exceeded:
                    penetration = abs(extreme - level.price_level)
                    analysis.sweeps.append(
                        LiquiditySweep(
                            level_id=level.level_id,
                            symbol=level.symbol,
                            timeframe=level.timeframe,
                            liquidity_type=level.liquidity_type,
                            side=level.side,
                            event_timestamp=pd.Timestamp(open_times[index]).to_pydatetime(),
                            confirmation_timestamp=pd.Timestamp(close_times[index]).to_pydatetime(),
                            price_level=level.price_level,
                            penetration_points=penetration / point if point else penetration,
                            closed_beyond=bool(closed_beyond),
                            extreme_price=float(extreme),
                            bar_index=index,
                        )
                    )
                    analysis.status[level.level_id] = LiquidityStatus.SWEPT
                    analysis.swept_at[level.level_id] = pd.Timestamp(close_times[index]).to_pydatetime()
                    continue  # consumed: removed from the active set

                if approach_tol is not None and near and level.level_id not in analysis.approached:
                    analysis.approached[level.level_id] = pd.Timestamp(close_times[index]).to_pydatetime()
                    if analysis.status[level.level_id] is LiquidityStatus.ACTIVE:
                        analysis.status[level.level_id] = LiquidityStatus.APPROACHED
                still_active.append(level)

            active = still_active

        logger.info(
            "liquidity %s %s: %d level(s), %d sweep(s), %d pending period(s) from %d bar(s)",
            symbol.value,
            timeframe.value,
            len(analysis.levels),
            len(analysis.sweeps),
            len(analysis.pending),
            len(work),
        )
        return analysis

    # ---------------------------------------------------------------- public

    def events(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[IctEvent]:
        """Contract events: one per level, one per sweep. Never merged."""
        analysis = self.analyse(frame, symbol, timeframe)
        events: list[IctEvent] = []

        for level in analysis.levels:
            status = analysis.status.get(level.level_id, LiquidityStatus.ACTIVE)
            events.append(
                IctEvent(
                    symbol=level.symbol,
                    timeframe=level.timeframe,
                    event_type=_EVENT_TYPE[level.liquidity_type],
                    direction=level.direction,
                    event_timestamp=level.created_timestamp,
                    confirmation_timestamp=level.confirmation_timestamp,
                    price_level=level.price_level,
                    created_timestamp=level.created_timestamp,
                    status=(EventStatus.SWEPT if status is LiquidityStatus.SWEPT else EventStatus.ACTIVE),
                    metadata={
                        "level_id": level.level_id,
                        "liquidity_type": level.liquidity_type.value,
                        "side": level.side.value,
                        "source_swing_timestamps": [t.isoformat() for t in level.source_swing_timestamps],
                        "period_label": level.period_label,
                        "lifecycle_status": status.value,
                        "swept_at": (
                            analysis.swept_at[level.level_id].isoformat()
                            if level.level_id in analysis.swept_at
                            else None
                        ),
                        **self.config.as_dict(),
                    },
                )
            )

        for sweep in analysis.sweeps:
            events.append(
                IctEvent(
                    symbol=sweep.symbol,
                    timeframe=sweep.timeframe,
                    event_type=EventType.LIQUIDITY_SWEEP,
                    direction=sweep.direction,
                    event_timestamp=sweep.event_timestamp,
                    confirmation_timestamp=sweep.confirmation_timestamp,
                    price_level=sweep.extreme_price,
                    reference_level=sweep.price_level,
                    strength=sweep.penetration_points,
                    status=EventStatus.SWEPT,
                    metadata={
                        "level_id": sweep.level_id,
                        "liquidity_type": sweep.liquidity_type.value,
                        "side": sweep.side.value,
                        "closed_beyond": sweep.closed_beyond,
                        "is_rejection": sweep.is_rejection,
                        **self.config.as_dict(),
                    },
                )
            )

        events.sort(key=lambda e: (e.confirmation_timestamp, e.event_timestamp, e.event_type.value))
        return events

    def observable_at(
        self, frame: pd.DataFrame, as_of: datetime, symbol: Symbol, timeframe: Timeframe
    ) -> LiquidityAnalysis:
        """The liquidity picture a decision at ``as_of`` may use."""
        if as_of.tzinfo is None:
            raise ValueError(f"as_of must be timezone-aware UTC; got naive {as_of!r}")

        full = self.analyse(frame, symbol, timeframe)
        limited = LiquidityAnalysis(
            levels=[x for x in full.levels if x.confirmation_timestamp <= as_of],
            sweeps=[s for s in full.sweeps if s.confirmation_timestamp <= as_of],
        )
        swept = {s.level_id for s in limited.sweeps}
        limited.swept_at = {k: v for k, v in full.swept_at.items() if v <= as_of}
        limited.approached = {k: v for k, v in full.approached.items() if v <= as_of}
        limited.status = {
            x.level_id: (
                LiquidityStatus.SWEPT
                if x.level_id in swept
                else (
                    LiquidityStatus.APPROACHED if x.level_id in limited.approached else LiquidityStatus.ACTIVE
                )
            )
            for x in limited.levels
        }
        return limited

    def with_config(self, config: LiquidityConfig) -> LiquidityDetector:
        return replace(self, config=config)


def _in_trading_week(local_day: date) -> bool:
    """Whether a day window anchored on ``local_day`` belongs to a trading week.

    The trading week runs Sunday 17:00 NY -> Friday 17:00 NY, i.e. the day windows
    anchored Sunday through Thursday. The Friday- and Saturday-anchored windows fall
    outside it: the Friday window *starts* at the moment the week ends.

    ``weekday()`` is Monday=0 … Sunday=6, so Friday=4 and Saturday=5 are excluded.
    """
    return local_day.weekday() not in (4, 5)


def _week_anchor(local_day: date) -> date:
    """The Sunday on or before ``local_day`` — the trading week's anchor.

    ``weekday()`` is Monday=0 … Sunday=6, so ``(weekday + 1) % 7`` is the number of
    days back to the preceding Sunday, mapping Sunday to itself.
    """
    return local_day - timedelta(days=(local_day.weekday() + 1) % 7)


def _week_end(anchor: date, config: LiquidityConfig) -> datetime:
    """When a trading week completes: the end of its Thursday-anchored day window.

    A Sunday-anchored week covers day windows Sunday…Thursday, and the Thursday window
    ends on Friday at the day boundary — 17:00 New York by default. Derived from the
    same day definition, so it inherits the timezone and DST handling rather than
    being a second calendar.
    """
    from .sessions import resolve_window

    thursday = anchor + timedelta(days=4)
    return resolve_window(config.day_definition, thursday).end_utc
