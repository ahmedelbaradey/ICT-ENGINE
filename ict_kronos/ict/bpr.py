"""Balanced Price Range — the intersection of two opposing Fair Value Gaps.

Full semantics in ``docs/ict/bpr.md``. Read that first.

    bullish FVG = [A, B]      bearish FVG = [C, D]

    if  max(A, C) < min(B, D):
        BPR.low  = max(A, C)
        BPR.high = min(B, D)
    else:
        no BPR

The zone is the **intersection**, never the union and never a midpoint construction.
Overlap must be strictly positive: two gaps that touch at exactly one price share no
range, and a zero-width BPR is refused at construction rather than stored.

Both gaps come from ``FvgDetector``. This module contains no gap detection.

**The confirmation rule that matters:** a BPR is not knowable until *both* its gaps
are. Publishing at the earlier gap's confirmation is the classic composite leak, and
:func:`composites.composite_confirmation` exists so that mistake cannot be made
locally.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

import pandas as pd

from ..app.logging import get_logger
from ..data.resampler import with_close_time
from ..domain import Symbol, Timeframe
from .composites import (
    ZoneFillUpdate,
    ZoneStatus,
    composite_confirmation,
    later_confirmed,
    track_zone_fill,
)
from .contract import (
    Direction,
    EventStatus,
    EventType,
    IctEvent,
    filter_observable,
    is_observable_at,
)
from .fvg import FvgConfig, FvgDetector

logger = get_logger(__name__)


class BprPolarity(StrEnum):
    """How a BPR's direction is assigned.

    ``LATER_FVG`` (default, the project convention) — the polarity of whichever gap
    confirmed second, i.e. the most recent delivery through the zone.

    ``NEUTRAL`` — the BPR carries no direction. Defensible: the zone is by
    construction a place where price delivered *both* ways, so an unqualified polarity
    is arguably an over-claim. Available, not default.
    """

    LATER_FVG = "later_fvg"
    NEUTRAL = "neutral"


@dataclass(frozen=True)
class BprConfig:
    """BPR parameters. Configuration, never literals in the detector."""

    polarity: BprPolarity = BprPolarity.LATER_FVG
    #: The two gaps must confirm within this many bars of each other. Unbounded pairing
    #: would join a March gap to a June one and call the overlap a structure.
    max_bars_between: int = 100
    #: Minimum overlap in instrument points. 0 means any strictly positive overlap.
    min_overlap_points: float = 0.0
    partial_fill_threshold: float = 0.0
    full_fill_threshold: float = 1.0

    def __post_init__(self) -> None:
        if self.max_bars_between < 1:
            raise ValueError(f"max_bars_between must be >= 1; got {self.max_bars_between}")
        if self.min_overlap_points < 0:
            raise ValueError(f"min_overlap_points must be >= 0; got {self.min_overlap_points}")
        if not 0.0 < self.full_fill_threshold <= 1.0:
            raise ValueError(f"full_fill_threshold must be in (0, 1]; got {self.full_fill_threshold}")
        if self.partial_fill_threshold >= self.full_fill_threshold:
            raise ValueError("partial_fill_threshold must be below full_fill_threshold")

    def as_dict(self) -> dict:
        return {
            "polarity": self.polarity.value,
            "max_bars_between": self.max_bars_between,
            "min_overlap_points": self.min_overlap_points,
        }


@dataclass(frozen=True)
class BalancedPriceRange:
    """One confirmed Balanced Price Range. Immutable."""

    bpr_id: str
    #: Provenance — both ids resolve to ``FvgZone``s from the same analysis.
    source_fvg_ids: tuple[str, str]
    bullish_fvg_id: str
    bearish_fvg_id: str
    symbol: str
    timeframe: str
    direction: Direction
    zone_top: float
    zone_bottom: float
    overlap_points: float
    #: The later gap's formation timestamp — where the completed relationship sits.
    event_timestamp: datetime
    #: max of both gaps' confirmations. A BPR is not knowable before both gaps are.
    confirmation_timestamp: datetime
    bars_between: int

    def is_observable_at(self, as_of: datetime) -> bool:
        """Delegates to the ONE contract-level predicate — never a private copy."""
        return is_observable_at(self, as_of)

    @property
    def midpoint(self) -> float:
        return (self.zone_top + self.zone_bottom) / 2.0

    @property
    def size(self) -> float:
        return self.zone_top - self.zone_bottom

    def as_dict(self) -> dict:
        return {
            "bpr_id": self.bpr_id,
            "source_fvg_ids": list(self.source_fvg_ids),
            "bullish_fvg_id": self.bullish_fvg_id,
            "bearish_fvg_id": self.bearish_fvg_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction.value,
            "zone_top": self.zone_top,
            "zone_bottom": self.zone_bottom,
            "overlap_points": self.overlap_points,
            "event_timestamp": self.event_timestamp.isoformat(),
            "confirmation_timestamp": self.confirmation_timestamp.isoformat(),
            "bars_between": self.bars_between,
        }


@dataclass
class BprAnalysis:
    ranges: list[BalancedPriceRange] = field(default_factory=list)
    fills: list[ZoneFillUpdate] = field(default_factory=list)
    status: dict[str, ZoneStatus] = field(default_factory=dict)

    def range_by_id(self, bpr_id: str) -> BalancedPriceRange | None:
        return next((r for r in self.ranges if r.bpr_id == bpr_id), None)

    def status_at(self, bpr_id: str, as_of: datetime) -> ZoneStatus | None:
        item = self.range_by_id(bpr_id)
        if item is None or not is_observable_at(item, as_of):
            return None
        seen = [u for u in self.fills if u.zone_id == bpr_id and is_observable_at(u, as_of)]
        return seen[-1].status_after if seen else ZoneStatus.ACTIVE


@dataclass
class BprDetector:
    """Deterministic BPR detection. Consumes ``FvgDetector``; detects no gaps."""

    config: BprConfig = BprConfig()
    fvg_config: FvgConfig = FvgConfig()

    @property
    def fvg_detector(self) -> FvgDetector:
        return FvgDetector(self.fvg_config)

    def detect(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[BalancedPriceRange]:
        """Every strictly-overlapping opposite-polarity pair of confirmed gaps."""
        zones = self.fvg_detector.detect(frame, symbol, timeframe)
        if len(zones) < 2:
            return []

        bullish = [z for z in zones if z.direction is Direction.BULLISH]
        bearish = [z for z in zones if z.direction is Direction.BEARISH]
        if not bullish or not bearish:
            return []

        point = symbol.spec.point_value
        min_overlap = self.config.min_overlap_points * point
        duration = timeframe.duration

        ranges: list[BalancedPriceRange] = []
        for up in bullish:
            for down in bearish:
                zone_top = min(up.top, down.top)
                zone_bottom = max(up.bottom, down.bottom)
                overlap = zone_top - zone_bottom
                if overlap <= 0 or overlap <= min_overlap:
                    # Touching at a single price is not an overlap. Strictness is
                    # deliberate and mirrors R2-05's rule that equality is not a gap.
                    continue

                gap = abs(up.confirmation_timestamp - down.confirmation_timestamp)
                bars_between = int(gap / duration)
                if bars_between > self.config.max_bars_between:
                    continue

                later = later_confirmed(up, down)
                direction = (
                    later.direction if self.config.polarity is BprPolarity.LATER_FVG else Direction.NEUTRAL
                )
                confirmation = composite_confirmation(
                    [up.confirmation_timestamp, down.confirmation_timestamp]
                )

                ranges.append(
                    BalancedPriceRange(
                        bpr_id=(
                            f"bpr:{symbol.value}:{timeframe.value}:"
                            f"{confirmation.isoformat()}:{up.zone_id}|{down.zone_id}"
                        ),
                        source_fvg_ids=(up.zone_id, down.zone_id),
                        bullish_fvg_id=up.zone_id,
                        bearish_fvg_id=down.zone_id,
                        symbol=symbol.value,
                        timeframe=timeframe.value,
                        direction=direction,
                        zone_top=float(zone_top),
                        zone_bottom=float(zone_bottom),
                        overlap_points=float(overlap / point) if point else float(overlap),
                        event_timestamp=later.formation_timestamp,
                        confirmation_timestamp=confirmation,
                        bars_between=bars_between,
                    )
                )

        ranges.sort(key=lambda r: (r.confirmation_timestamp, r.bpr_id))
        return ranges

    def analyse(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> BprAnalysis:
        ranges = self.detect(frame, symbol, timeframe)
        analysis = BprAnalysis(ranges=ranges)
        if not ranges:
            return analysis

        work = with_close_time(frame, timeframe).sort_values("timestamp", kind="mergesort")
        work = work.reset_index(drop=True)

        for item in ranges:
            # A NEUTRAL BPR still needs a direction to measure fill against; the later
            # gap's polarity is used for the arithmetic only, never re-published.
            fill_direction = (
                item.direction if item.direction is not Direction.NEUTRAL else (Direction.BULLISH)
            )
            updates = track_zone_fill(
                work,
                zone_id=item.bpr_id,
                top=item.zone_top,
                bottom=item.zone_bottom,
                direction=fill_direction,
                start_timestamp=item.confirmation_timestamp,
                partial_threshold=self.config.partial_fill_threshold,
                full_threshold=self.config.full_fill_threshold,
            )
            analysis.fills.extend(updates)
            analysis.status[item.bpr_id] = updates[-1].status_after if updates else ZoneStatus.ACTIVE

        analysis.fills.sort(key=lambda u: (u.confirmation_timestamp, u.zone_id))
        return analysis

    def events(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[IctEvent]:
        analysis = self.analyse(frame, symbol, timeframe)
        events: list[IctEvent] = []

        for item in analysis.ranges:
            status = analysis.status.get(item.bpr_id, ZoneStatus.ACTIVE)
            events.append(
                IctEvent(
                    symbol=item.symbol,
                    timeframe=item.timeframe,
                    event_type=EventType.BALANCED_PRICE_RANGE,
                    direction=item.direction,
                    event_timestamp=item.event_timestamp,
                    confirmation_timestamp=item.confirmation_timestamp,
                    price_level=item.midpoint,
                    reference_level=item.zone_top,
                    strength=item.overlap_points,
                    created_timestamp=item.event_timestamp,
                    status=(EventStatus.MITIGATED if status is ZoneStatus.MITIGATED else EventStatus.ACTIVE),
                    metadata={
                        "bpr_id": item.bpr_id,
                        "source_fvg_ids": list(item.source_fvg_ids),
                        "bullish_fvg_id": item.bullish_fvg_id,
                        "bearish_fvg_id": item.bearish_fvg_id,
                        "zone_top": item.zone_top,
                        "zone_bottom": item.zone_bottom,
                        "bars_between": item.bars_between,
                        "lifecycle_status": status.value,
                        **self.config.as_dict(),
                    },
                )
            )

        events.sort(key=lambda e: (e.confirmation_timestamp, e.event_timestamp))
        return events

    def observable_at(
        self, frame: pd.DataFrame, as_of: datetime, symbol: Symbol, timeframe: Timeframe
    ) -> BprAnalysis:
        full = self.analyse(frame, symbol, timeframe)
        limited = BprAnalysis(
            ranges=filter_observable(full.ranges, as_of),
            fills=filter_observable(full.fills, as_of),
        )
        limited.status = {
            r.bpr_id: limited.status_at(r.bpr_id, as_of) or ZoneStatus.ACTIVE for r in limited.ranges
        }
        return limited

    def with_config(self, config: BprConfig) -> BprDetector:
        return replace(self, config=config)
