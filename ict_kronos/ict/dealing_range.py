"""Dealing ranges and premium / discount classification (R2-06).

Full semantics in ``docs/ict/dealing_range.md``; the four rejected range definitions
are in ``docs/ict/R2-06-CONCEPT-MAP.md``. Read those first.

The arithmetic here is trivial and the selection is not:

    equilibrium = (high + low) / 2
    position    = (price - low) / (high - low)

**Every leak this module can have is a leak in choosing ``high`` and ``low``.**
``frame["high"].max()`` is the single worst answer available — it depends on bars that
had not printed — and a leaked range is invisible in the output, because it looks
exactly like an honest one.

So the range is the **last structural leg**: when a BOS/MSS confirms, the anchors are
the swing it broke and the most recent confirmed opposite swing. Both are confirmed
*before* the break by construction — you cannot break a level that is not yet a
level — so no forming pivot appears anywhere.

Two records, two lifetimes, never merged:

    DealingRange      a fact about STRUCTURE, frozen when created
    RangeObservation  a fact about a PRICE at an instant, recomputed per bar

A later break never rewrites an earlier range. It creates a new one and supersedes the
old, and supersession lives in the analysis rather than on the frozen record — the
same immutable-record / update-stream separation R2-04 established.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

import numpy as np
import pandas as pd

from ..app.logging import get_logger
from ..data.resampler import with_close_time
from ..domain import Symbol, Timeframe
from .composites import composite_confirmation, structure_break_id, swing_point_id
from .contract import (
    ContractViolation,
    Direction,
    EventStatus,
    EventType,
    IctEvent,
    filter_observable,
    is_observable_at,
)
from .structure import StructureConfig, StructureDetector
from .swings import SwingConfig, SwingDetector, SwingPoint

logger = get_logger(__name__)


class RangeZone(StrEnum):
    """Where a price sits relative to a range's equilibrium.

    ``EQUILIBRIUM`` is a **band**, never a floating-point equality. Its width is
    configured in instrument points and defaults to half a tick — below the
    instrument's own resolution, so the default is a numerical-safety choice rather
    than a claim about where an equilibrium zone begins.
    """

    PREMIUM = "premium"
    DISCOUNT = "discount"
    EQUILIBRIUM = "equilibrium"


@dataclass(frozen=True)
class DealingRangeConfig:
    """Dealing-range parameters. Configuration, never literals in the detector.

    Deliberately small. The range *definition* is not a knob: two live definitions
    would mean every downstream result has to name which one produced it, and the
    first person who forgets makes the research unreproducible. The alternatives are
    documented in the concept map and implemented nowhere.
    """

    #: Half-width of the EQUILIBRIUM band, in instrument points. 0.5 = half a tick,
    #: i.e. finer than the instrument can express, so EQUILIBRIUM means "at
    #: equilibrium" rather than "near it". Raise it to make equilibrium a real zone —
    #: that is a research decision, which is why it is configurable.
    equilibrium_tolerance_points: float = 0.5
    #: Emit a per-bar classification stream. Off makes ``analyse`` cheap when only the
    #: ranges themselves are wanted.
    classify_bars: bool = True

    def __post_init__(self) -> None:
        if self.equilibrium_tolerance_points < 0:
            raise ValueError(
                f"equilibrium_tolerance_points must be >= 0; got {self.equilibrium_tolerance_points}"
            )

    def as_dict(self) -> dict:
        return {
            "equilibrium_tolerance_points": self.equilibrium_tolerance_points,
            "classify_bars": self.classify_bars,
        }


@dataclass(frozen=True)
class DealingRange:
    """One confirmed dealing range. Immutable.

    ``high_price < low_price`` is refused at construction — that is a contract
    violation, not a degenerate case. ``high_price == low_price`` is *representable*
    so the degenerate path is defined and testable, though §4 of the docs means the
    detector never produces one.
    """

    range_id: str
    symbol: str
    timeframe: str
    #: Inherited from the break and FIXED. Never re-derived from where price sits.
    direction: Direction
    high_price: float
    low_price: float
    equilibrium_price: float
    #: Provenance — derived swing ids, resolvable against ``SwingDetector`` output.
    high_source_id: str
    low_source_id: str
    #: Carried so the composite invariant is checkable from the record alone.
    high_source_confirmation: datetime
    low_source_confirmation: datetime
    #: The break that created the range. ``None`` only if a future selection rule
    #: produces a range without one.
    source_break_id: str | None
    #: The break's event timestamp — where the range begins on the chart.
    created_timestamp: datetime
    #: max(both anchors, the break). A range is not knowable before all three are.
    confirmation_timestamp: datetime

    def __post_init__(self) -> None:
        if self.high_price < self.low_price:
            raise ContractViolation(
                f"dealing range {self.range_id} has high {self.high_price} below low "
                f"{self.low_price}; an inverted range is not a degenerate range"
            )

    def is_observable_at(self, as_of: datetime) -> bool:
        """Delegates to the ONE contract-level predicate — never a private copy."""
        return is_observable_at(self, as_of)

    @property
    def width(self) -> float:
        return self.high_price - self.low_price

    @property
    def is_degenerate(self) -> bool:
        """Zero-width. ``position`` is undefined; ``zone`` still is not."""
        return self.width == 0.0

    def position_of(self, price: float) -> float:
        """Normalised position: 0 at the low, 0.5 at equilibrium, 1 at the high.

        **Never clamped.** Price beyond an anchor gives < 0 or > 1, and those are real
        answers — clamping would erase the difference between "at the low" and "far
        below the low". Returns NaN for a degenerate range, the documented sentinel;
        no division by zero occurs on any path.
        """
        if self.is_degenerate:
            return math.nan
        return (price - self.low_price) / self.width

    def as_dict(self) -> dict:
        return {
            "range_id": self.range_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction.value,
            "high_price": self.high_price,
            "low_price": self.low_price,
            "equilibrium_price": self.equilibrium_price,
            "high_source_id": self.high_source_id,
            "low_source_id": self.low_source_id,
            "high_source_confirmation": self.high_source_confirmation.isoformat(),
            "low_source_confirmation": self.low_source_confirmation.isoformat(),
            "source_break_id": self.source_break_id,
            "created_timestamp": self.created_timestamp.isoformat(),
            "confirmation_timestamp": self.confirmation_timestamp.isoformat(),
        }


@dataclass(frozen=True)
class RangeObservation:
    """Where one price sat inside one range at one instant. Immutable.

    A separate record from the range on purpose: classification changes every bar and
    the range does not, so writing the zone onto the range would make an immutable
    structural fact mutable.
    """

    range_id: str
    symbol: str
    timeframe: str
    #: The classified bar's open time.
    observation_timestamp: datetime
    #: That bar's close_time — the instant its close is knowable.
    confirmation_timestamp: datetime
    #: The bar's CLOSE. Documented choice: it is the price the bar is known by.
    price: float
    zone: RangeZone
    #: Signed, in price units. Positive above equilibrium.
    distance_from_equilibrium: float
    #: Normalised position, NaN for a degenerate range. Unclamped.
    percentage_position: float

    def is_observable_at(self, as_of: datetime) -> bool:
        """Delegates to the ONE contract-level predicate — never a private copy."""
        return is_observable_at(self, as_of)

    def as_dict(self) -> dict:
        return {
            "range_id": self.range_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "observation_timestamp": self.observation_timestamp.isoformat(),
            "confirmation_timestamp": self.confirmation_timestamp.isoformat(),
            "price": self.price,
            "zone": self.zone.value,
            "distance_from_equilibrium": self.distance_from_equilibrium,
            "percentage_position": self.percentage_position,
        }


@dataclass
class DealingRangeAnalysis:
    """Ranges, their supersession, and the per-bar classification stream."""

    ranges: list[DealingRange] = field(default_factory=list)
    observations: list[RangeObservation] = field(default_factory=list)
    #: ``range_id -> the confirmation of the range that replaced it``. A timestamped
    #: stream, never written back onto the frozen record.
    superseded_at: dict[str, datetime] = field(default_factory=dict)
    #: Break candidates whose anchors were inverted or zero-width. Counted so the
    #: rejection is inspectable rather than invisible.
    rejected_inverted: int = 0
    #: Breaks with no observable opposite anchor yet — early in a series, normally.
    rejected_missing_anchor: int = 0
    #: Breaks whose anchors matched the active range, so nothing new was emitted.
    suppressed_unchanged: int = 0

    def range_by_id(self, range_id: str) -> DealingRange | None:
        return next((r for r in self.ranges if r.range_id == range_id), None)

    def range_at(self, as_of: datetime) -> DealingRange | None:
        """The active range a decision timestamped ``as_of`` may use.

        ``None`` before the first confirmed break — a correct answer, not a gap.
        Premium and discount are meaningless without a range.
        """
        observable = filter_observable(self.ranges, as_of)
        return observable[-1] if observable else None

    def observation_at(self, as_of: datetime) -> RangeObservation | None:
        """The most recent classification knowable at ``as_of``."""
        seen = filter_observable(self.observations, as_of)
        return seen[-1] if seen else None

    def is_active_at(self, range_id: str, as_of: datetime) -> bool:
        current = self.range_at(as_of)
        return current is not None and current.range_id == range_id


@dataclass
class DealingRangeDetector:
    """Deterministic dealing-range detection and premium/discount classification.

    Consumes ``SwingDetector`` and ``StructureDetector``. Detects no pivots and no
    breaks of its own; it selects two confirmed anchors and does arithmetic.
    """

    config: DealingRangeConfig = DealingRangeConfig()
    swing_config: SwingConfig = SwingConfig()
    structure_config: StructureConfig = StructureConfig()

    @property
    def swing_detector(self) -> SwingDetector:
        return SwingDetector(self.swing_config)

    @property
    def structure_detector(self) -> StructureDetector:
        return StructureDetector(self.structure_config)

    # ------------------------------------------------------------------ anchors

    @staticmethod
    def _broken_swing(swings: list[SwingPoint], brk) -> SwingPoint | None:
        """The pivot the break broke, identified by the break's own reference.

        Matched on ``(direction, event_timestamp)`` rather than on price: two pivots
        can share a price, and only one of them is the level that was broken.
        """
        wanted = Direction.BULLISH if brk.direction is Direction.BULLISH else Direction.BEARISH
        return next(
            (
                s
                for s in swings
                if s.direction is wanted and s.event_timestamp == brk.reference_swing_timestamp
            ),
            None,
        )

    @staticmethod
    def _opposite_anchor(swings: list[SwingPoint], brk, as_of: datetime) -> SwingPoint | None:
        """The most recent confirmed pivot of the opposite kind, observable at ``as_of``.

        Two conditions, both load-bearing: it must be **observable** (routed through
        the shared gate, so a forming pivot can never qualify) and it must sit
        **before the break on the chart**, so the anchor belongs to the leg that
        produced the break rather than to whatever comes after it.
        """
        wanted = Direction.BEARISH if brk.direction is Direction.BULLISH else Direction.BULLISH
        candidates = [
            s
            for s in filter_observable(swings, as_of)
            if s.direction is wanted and s.event_timestamp < brk.event_timestamp
        ]
        return candidates[-1] if candidates else None

    # ------------------------------------------------------------------ core

    def detect(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[DealingRange]:
        """Every confirmed dealing range, in confirmation order."""
        return self._detect(frame, symbol, timeframe)[0]

    def _detect(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe):
        swings = self.swing_detector.detect(frame, symbol, timeframe)
        if not swings:
            return [], 0, 0, 0

        breaks = self.structure_detector.analyse(frame, symbol, timeframe).breaks
        if not breaks:
            return [], 0, 0, 0

        ranges: list[DealingRange] = []
        inverted = missing = suppressed = 0
        active_anchors: tuple[str, str] | None = None

        for brk in sorted(breaks, key=lambda b: (b.confirmation_timestamp, b.event_timestamp)):
            broken = self._broken_swing(swings, brk)
            if broken is None:  # pragma: no cover - a break always references a swing
                missing += 1
                continue

            opposite = self._opposite_anchor(swings, brk, brk.confirmation_timestamp)
            if opposite is None:
                # Early in a series there may be no confirmed pivot of the other kind
                # yet. No range is the honest answer.
                missing += 1
                continue

            if brk.direction is Direction.BULLISH:
                high, low = broken, opposite
            else:
                high, low = opposite, broken

            if high.price_level <= low.price_level:
                # Interleaved pivots can pair a high below a low. Neither an inverted
                # nor a zero-width range is stored; the rejection is counted instead.
                inverted += 1
                continue

            anchors = (swing_point_id(high), swing_point_id(low))
            if anchors == active_anchors:
                # Structure moved, the range did not. The stream records CHANGES.
                suppressed += 1
                continue

            confirmation = composite_confirmation(
                [high.confirmation_timestamp, low.confirmation_timestamp],
                own_trigger=brk.confirmation_timestamp,
            )
            ranges.append(
                DealingRange(
                    # Identity is anchored on CAUSAL SOURCE identity plus the instant.
                    # Two ranges with identical prices on the same bar stay distinct
                    # when their anchors differ — which is what makes provenance
                    # resolvable rather than decorative.
                    range_id=(
                        f"range:{symbol.value}:{timeframe.value}:{brk.direction.value}:"
                        f"{confirmation.isoformat()}:{anchors[0]}|{anchors[1]}"
                    ),
                    symbol=symbol.value,
                    timeframe=timeframe.value,
                    direction=brk.direction,
                    high_price=float(high.price_level),
                    low_price=float(low.price_level),
                    equilibrium_price=float((high.price_level + low.price_level) / 2.0),
                    high_source_id=anchors[0],
                    low_source_id=anchors[1],
                    high_source_confirmation=high.confirmation_timestamp,
                    low_source_confirmation=low.confirmation_timestamp,
                    source_break_id=structure_break_id(brk),
                    created_timestamp=brk.event_timestamp,
                    confirmation_timestamp=confirmation,
                )
            )
            active_anchors = anchors

        ranges.sort(key=lambda r: (r.confirmation_timestamp, r.range_id))
        return ranges, inverted, missing, suppressed

    # ------------------------------------------------------------------ zones

    def zone_of(self, dealing_range: DealingRange, price: float, symbol: Symbol) -> RangeZone:
        """Classify one price against one range.

        Compares against **equilibrium**, not against the normalised position, so a
        degenerate range classifies correctly instead of dividing by zero.
        """
        band = self.config.equilibrium_tolerance_points * symbol.spec.point_value
        distance = price - dealing_range.equilibrium_price
        if abs(distance) <= band:
            return RangeZone.EQUILIBRIUM
        return RangeZone.PREMIUM if distance > 0 else RangeZone.DISCOUNT

    def _observations(
        self, work: pd.DataFrame, ranges: list[DealingRange], symbol: Symbol, timeframe: Timeframe
    ) -> list[RangeObservation]:
        """One classification per bar, against whichever range was active then."""
        if not ranges or len(work) == 0:
            return []

        band = self.config.equilibrium_tolerance_points * symbol.spec.point_value
        # ``close_time`` is timezone-aware, so the comparison stays in pandas rather
        # than dropping to ``np.datetime64`` — which silently discards the tz and then
        # refuses to compare against an aware timestamp.
        close_times = work["close_time"]
        stamps = work["timestamp"].dt.to_pydatetime()
        closes = close_times.dt.to_pydatetime()
        prices = work["close"].to_numpy(dtype="float64")

        out: list[RangeObservation] = []
        for position, item in enumerate(ranges):
            # A range classifies from its own confirmation until the next one confirms;
            # never the bars that created it, and never after it is superseded.
            mask = (close_times >= pd.Timestamp(item.confirmation_timestamp)).to_numpy().copy()
            if position + 1 < len(ranges):
                nxt = pd.Timestamp(ranges[position + 1].confirmation_timestamp)
                mask &= (close_times < nxt).to_numpy()
            index = mask.nonzero()[0]
            if len(index) == 0:
                continue

            segment = prices[index]
            distance = segment - item.equilibrium_price
            width = item.width
            ratios = (segment - item.low_price) / width if width > 0 else np.full(len(segment), math.nan)

            for offset, i in enumerate(index):
                d = float(distance[offset])
                if abs(d) <= band:
                    zone = RangeZone.EQUILIBRIUM
                elif d > 0:
                    zone = RangeZone.PREMIUM
                else:
                    zone = RangeZone.DISCOUNT
                out.append(
                    RangeObservation(
                        range_id=item.range_id,
                        symbol=symbol.value,
                        timeframe=timeframe.value,
                        observation_timestamp=stamps[i],
                        confirmation_timestamp=closes[i],
                        price=float(segment[offset]),
                        zone=zone,
                        distance_from_equilibrium=d,
                        percentage_position=float(ratios[offset]),
                    )
                )
        return out

    def analyse(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> DealingRangeAnalysis:
        ranges, inverted, missing, suppressed = self._detect(frame, symbol, timeframe)
        analysis = DealingRangeAnalysis(
            ranges=ranges,
            rejected_inverted=inverted,
            rejected_missing_anchor=missing,
            suppressed_unchanged=suppressed,
        )
        if not ranges:
            return analysis

        for earlier, later in zip(ranges[:-1], ranges[1:], strict=True):
            analysis.superseded_at[earlier.range_id] = later.confirmation_timestamp

        if self.config.classify_bars and len(frame) > 0:
            work = with_close_time(frame, timeframe).sort_values("timestamp", kind="mergesort")
            analysis.observations = self._observations(work.reset_index(drop=True), ranges, symbol, timeframe)
        return analysis

    def classify_at(
        self, frame: pd.DataFrame, as_of: datetime, symbol: Symbol, timeframe: Timeframe
    ) -> RangeObservation | None:
        """The classification a decision timestamped ``as_of`` may use, or ``None``."""
        return self.analyse(frame, symbol, timeframe).observation_at(as_of)

    def events(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[IctEvent]:
        """Contract events for the RANGES.

        Deliberately not one event per observation: a classification exists for every
        bar, and flooding the event stream with them would drown the structural events
        it exists to carry. Observations are analysis output, reached through
        ``analyse``.
        """
        analysis = self.analyse(frame, symbol, timeframe)
        events = [
            IctEvent(
                symbol=item.symbol,
                timeframe=item.timeframe,
                event_type=EventType.DEALING_RANGE,
                direction=item.direction,
                event_timestamp=item.created_timestamp,
                confirmation_timestamp=item.confirmation_timestamp,
                price_level=item.equilibrium_price,
                reference_level=item.high_price,
                strength=item.width,
                created_timestamp=item.created_timestamp,
                status=(
                    EventStatus.INVALIDATED if item.range_id in analysis.superseded_at else EventStatus.ACTIVE
                ),
                metadata={
                    "range_id": item.range_id,
                    "high_source_id": item.high_source_id,
                    "low_source_id": item.low_source_id,
                    "source_break_id": item.source_break_id,
                    "high_price": item.high_price,
                    "low_price": item.low_price,
                    "equilibrium_price": item.equilibrium_price,
                    "width": item.width,
                    "superseded_at": (
                        analysis.superseded_at[item.range_id].isoformat()
                        if item.range_id in analysis.superseded_at
                        else None
                    ),
                    **self.config.as_dict(),
                },
            )
            for item in analysis.ranges
        ]
        events.sort(key=lambda e: (e.confirmation_timestamp, e.event_timestamp))
        return events

    def observable_at(
        self, frame: pd.DataFrame, as_of: datetime, symbol: Symbol, timeframe: Timeframe
    ) -> DealingRangeAnalysis:
        full = self.analyse(frame, symbol, timeframe)
        visible = filter_observable(full.ranges, as_of)
        limited = DealingRangeAnalysis(
            ranges=visible,
            observations=filter_observable(full.observations, as_of),
            rejected_inverted=full.rejected_inverted,
            rejected_missing_anchor=full.rejected_missing_anchor,
            suppressed_unchanged=full.suppressed_unchanged,
        )
        known = {r.range_id for r in visible}
        limited.superseded_at = {k: v for k, v in full.superseded_at.items() if k in known and v <= as_of}
        return limited

    def with_config(self, config: DealingRangeConfig) -> DealingRangeDetector:
        return replace(self, config=config)
