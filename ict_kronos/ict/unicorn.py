"""The Unicorn Model — a Breaker Block overlapping a same-polarity Fair Value Gap.

Full semantics in ``docs/ict/unicorn.md``. Read that first.

A Unicorn is the deepest composite in Phase 2 and contains the least logic of any
module here, which is the point. It computes one intersection and one adjacency test;
everything underneath it — the gap, the Order Block, the Breaker, the structure break
that promoted the block — is consumed **by id** from detectors that are already
approved.

    zone_top    = min(breaker.zone_top,    fvg.top)
    zone_bottom = max(breaker.zone_bottom, fvg.bottom)     # must be strictly below

**Cardinality is not collapsed.** One Unicorn per qualifying (Breaker, FVG) pair, so
three gaps overlapping one Breaker are three Unicorns with three ids. The pair is part
of the identity — the R2-05.2 audit found two real id collisions in exactly this
shape, and the fix in both cases was to put the source in the id.

**Retest is not confirmation.** The source describes price returning to the overlap as
what confirms a Unicorn *trade*; the *event* exists as soon as both components do.
Deferring the event to the retest would push it to an instant that may never arrive.

**Invalidation is inherited, never recomputed.** A Unicorn cannot outlive its Breaker,
so when the Breaker reaches ``ZoneStatus.MITIGATED`` in ``BreakerAnalysis`` the Unicorn
is ``INVALIDATED`` from that instant — read out of the Breaker's own fill stream, with
no price condition evaluated here.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

import pandas as pd

from ..app.logging import get_logger
from ..data.resampler import with_close_time
from ..domain import Symbol, Timeframe
from .breakers import BreakerConfig, BreakerDetector
from .composites import (
    ZoneFillUpdate,
    ZoneStatus,
    composite_confirmation,
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
from .order_blocks import OrderBlockConfig
from .structure import StructureConfig

logger = get_logger(__name__)


class UnicornStatus(StrEnum):
    """Lifecycle of a Unicorn zone.

    ``MITIGATED`` is fill of the Unicorn's *own* intersection, from bar extremes.
    ``INVALIDATED`` is **inherited** from the source Breaker's death and is not a
    statement about the intersection at all. They are different events, which is why
    they are different states.
    """

    ACTIVE = "active"
    PARTIALLY_FILLED = "partially_filled"
    MITIGATED = "mitigated"
    INVALIDATED = "invalidated"


@dataclass(frozen=True)
class UnicornConfig:
    """Unicorn parameters. Configuration, never literals in the detector."""

    #: The Breaker and the FVG must confirm within this many bars of one another. A
    #: configured proxy for the source's informal "same structural leg"; unbounded
    #: pairing would join a Breaker to an unrelated gap weeks later.
    max_bars_from_breaker: int = 50
    #: Minimum overlap in instrument points. 0 means any strictly positive overlap.
    min_overlap_points: float = 0.0
    #: OFF by default: the source requires the gap to *overlap* the Breaker, not to sit
    #: inside it. Available as an explicit qualifier, never as a silent default.
    require_full_containment: bool = False
    partial_fill_threshold: float = 0.0
    full_fill_threshold: float = 1.0

    def __post_init__(self) -> None:
        if self.max_bars_from_breaker < 1:
            raise ValueError(f"max_bars_from_breaker must be >= 1; got {self.max_bars_from_breaker}")
        if self.min_overlap_points < 0:
            raise ValueError(f"min_overlap_points must be >= 0; got {self.min_overlap_points}")
        if not 0.0 < self.full_fill_threshold <= 1.0:
            raise ValueError(f"full_fill_threshold must be in (0, 1]; got {self.full_fill_threshold}")
        if self.partial_fill_threshold >= self.full_fill_threshold:
            raise ValueError("partial_fill_threshold must be below full_fill_threshold")

    def as_dict(self) -> dict:
        return {
            "max_bars_from_breaker": self.max_bars_from_breaker,
            "min_overlap_points": self.min_overlap_points,
            "require_full_containment": self.require_full_containment,
        }


@dataclass(frozen=True)
class Unicorn:
    """One confirmed Unicorn. Immutable."""

    unicorn_id: str
    #: Provenance. Resolves to a ``BreakerBlock`` from the same analysis.
    source_breaker_id: str
    #: Provenance. Resolves to an ``FvgZone`` from the same analysis.
    source_fvg_id: str
    #: Transitive provenance — the Order Block beneath the Breaker, carried rather than
    #: recomputed, so the whole chain is answerable from one record.
    source_order_block_id: str
    symbol: str
    timeframe: str
    direction: Direction
    #: The INTERSECTION of the two components. Genuinely new geometry; identity still
    #: points back at both parents.
    zone_top: float
    zone_bottom: float
    overlap_points: float
    #: The later of the two components' event timestamps.
    event_timestamp: datetime
    #: ``max`` of both components' confirmations. A Unicorn is not knowable before both
    #: of its components are.
    confirmation_timestamp: datetime
    #: Both source confirmations, carried so the composite invariant is checkable from
    #: the record alone without re-running either upstream detector.
    source_breaker_confirmation: datetime
    source_fvg_confirmation: datetime
    bars_between: int
    #: Whether the gap sits entirely inside the Breaker. Recorded, never required.
    fully_contained: bool

    def is_observable_at(self, as_of: datetime) -> bool:
        """Delegates to the ONE contract-level predicate — never a private copy."""
        return is_observable_at(self, as_of)

    @property
    def is_bullish(self) -> bool:
        return self.direction is Direction.BULLISH

    @property
    def midpoint(self) -> float:
        return (self.zone_top + self.zone_bottom) / 2.0

    @property
    def size(self) -> float:
        return self.zone_top - self.zone_bottom

    def as_dict(self) -> dict:
        return {
            "unicorn_id": self.unicorn_id,
            "source_breaker_id": self.source_breaker_id,
            "source_fvg_id": self.source_fvg_id,
            "source_order_block_id": self.source_order_block_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction.value,
            "zone_top": self.zone_top,
            "zone_bottom": self.zone_bottom,
            "overlap_points": self.overlap_points,
            "event_timestamp": self.event_timestamp.isoformat(),
            "confirmation_timestamp": self.confirmation_timestamp.isoformat(),
            "source_breaker_confirmation": self.source_breaker_confirmation.isoformat(),
            "source_fvg_confirmation": self.source_fvg_confirmation.isoformat(),
            "bars_between": self.bars_between,
            "fully_contained": self.fully_contained,
        }


@dataclass
class UnicornAnalysis:
    """Unicorns, their fill progression, and their inherited deaths."""

    unicorns: list[Unicorn] = field(default_factory=list)
    fills: list[ZoneFillUpdate] = field(default_factory=list)
    status: dict[str, UnicornStatus] = field(default_factory=dict)
    #: ``unicorn_id -> the instant its source Breaker was mitigated``. Copied out of
    #: ``BreakerAnalysis``; no price condition is evaluated here to produce it.
    inherited_invalidation_at: dict[str, datetime] = field(default_factory=dict)
    #: ``unicorn_id -> the first bar whose extreme entered the zone``. The retest, as
    #: an entry in the update stream rather than a change to the immutable record.
    retested_at: dict[str, datetime] = field(default_factory=dict)

    def unicorn_by_id(self, unicorn_id: str) -> Unicorn | None:
        return next((u for u in self.unicorns if u.unicorn_id == unicorn_id), None)

    def status_at(self, unicorn_id: str, as_of: datetime) -> UnicornStatus | None:
        """Lifecycle state as known at ``as_of``. Point-in-time, never final-state."""
        unicorn = self.unicorn_by_id(unicorn_id)
        if unicorn is None or not is_observable_at(unicorn, as_of):
            return None

        death = self.inherited_invalidation_at.get(unicorn_id)
        if death is not None and death <= as_of:
            return UnicornStatus.INVALIDATED

        seen = [u for u in self.fills if u.zone_id == unicorn_id and is_observable_at(u, as_of)]
        if not seen:
            return UnicornStatus.ACTIVE
        return (
            UnicornStatus.MITIGATED
            if seen[-1].status_after is ZoneStatus.MITIGATED
            else UnicornStatus.PARTIALLY_FILLED
        )

    def active_at(self, as_of: datetime) -> list[Unicorn]:
        return [
            u
            for u in filter_observable(self.unicorns, as_of)
            if self.status_at(u.unicorn_id, as_of) in (UnicornStatus.ACTIVE, UnicornStatus.PARTIALLY_FILLED)
        ]


@dataclass
class UnicornDetector:
    """Deterministic Unicorn detection.

    Consumes ``BreakerDetector`` and ``FvgDetector``. Detects no gaps, no blocks, no
    breakers and no structure — one intersection and one adjacency test, nothing else.
    """

    config: UnicornConfig = UnicornConfig()
    breaker_config: BreakerConfig = BreakerConfig()
    fvg_config: FvgConfig = FvgConfig()
    order_block_config: OrderBlockConfig = OrderBlockConfig()
    structure_config: StructureConfig = StructureConfig()

    @property
    def breaker_detector(self) -> BreakerDetector:
        return BreakerDetector(
            config=self.breaker_config,
            order_block_config=self.order_block_config,
            structure_config=self.structure_config,
        )

    @property
    def fvg_detector(self) -> FvgDetector:
        return FvgDetector(self.fvg_config)

    def detect(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[Unicorn]:
        """Every qualifying (Breaker, FVG) pair, one Unicorn each."""
        breakers = self.breaker_detector.detect(frame, symbol, timeframe)
        if not breakers:
            return []
        gaps = self.fvg_detector.detect(frame, symbol, timeframe)
        if not gaps:
            return []

        point = symbol.spec.point_value
        min_overlap = self.config.min_overlap_points * point
        duration = timeframe.duration

        unicorns: list[Unicorn] = []
        for breaker in breakers:
            for gap in gaps:
                if gap.direction is not breaker.direction:
                    # Polarity must match. An opposite-polarity overlap is a different
                    # concept entirely and is not silently promoted here.
                    continue

                zone_top = min(breaker.zone_top, gap.top)
                zone_bottom = max(breaker.zone_bottom, gap.bottom)
                overlap = zone_top - zone_bottom
                if overlap <= 0 or overlap <= min_overlap:
                    # Touching at a single price is not an overlap; a zero-width
                    # Unicorn is refused at construction rather than stored.
                    continue

                contained = gap.top <= breaker.zone_top and gap.bottom >= breaker.zone_bottom
                if self.config.require_full_containment and not contained:
                    continue

                apart = abs(gap.confirmation_timestamp - breaker.confirmation_timestamp)
                bars_between = int(apart / duration)
                if bars_between > self.config.max_bars_from_breaker:
                    continue

                # The LATER of the two components' event timestamps — where the
                # completed relationship sits on the chart. Deliberately the max of the
                # two, not "whichever confirmed second": a component can form later and
                # still confirm first, and the relationship is not on the chart until
                # both halves of it are drawn.
                event_timestamp = max(breaker.event_timestamp, gap.formation_timestamp)
                confirmation = composite_confirmation(
                    [breaker.confirmation_timestamp, gap.confirmation_timestamp]
                )

                unicorns.append(
                    Unicorn(
                        # The PAIR is part of the identity: one Breaker can overlap
                        # several gaps confirming on the same bar, and one gap can
                        # overlap several Breakers. Without both, their ids collide.
                        unicorn_id=(
                            f"unicorn:{symbol.value}:{timeframe.value}:"
                            f"{confirmation.isoformat()}:{breaker.breaker_id}|{gap.zone_id}"
                        ),
                        source_breaker_id=breaker.breaker_id,
                        source_fvg_id=gap.zone_id,
                        source_order_block_id=breaker.source_order_block_id,
                        symbol=symbol.value,
                        timeframe=timeframe.value,
                        direction=breaker.direction,
                        zone_top=float(zone_top),
                        zone_bottom=float(zone_bottom),
                        overlap_points=float(overlap / point) if point else float(overlap),
                        event_timestamp=event_timestamp,
                        confirmation_timestamp=confirmation,
                        source_breaker_confirmation=breaker.confirmation_timestamp,
                        source_fvg_confirmation=gap.confirmation_timestamp,
                        bars_between=bars_between,
                        fully_contained=bool(contained),
                    )
                )

        unicorns.sort(key=lambda u: (u.confirmation_timestamp, u.unicorn_id))
        return unicorns

    def analyse(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> UnicornAnalysis:
        unicorns = self.detect(frame, symbol, timeframe)
        analysis = UnicornAnalysis(unicorns=unicorns)
        if not unicorns:
            return analysis

        work = with_close_time(frame, timeframe).sort_values("timestamp", kind="mergesort")
        work = work.reset_index(drop=True)

        # Inherited invalidation, read out of the Breaker's OWN fill stream. No price
        # condition is evaluated here: a Unicorn's death is its Breaker's death, and
        # recomputing it would be a second implementation free to drift.
        breaker_analysis = self.breaker_detector.analyse(frame, symbol, timeframe)
        breaker_death: dict[str, datetime] = {}
        for update in breaker_analysis.fills:
            if update.status_after is ZoneStatus.MITIGATED and update.zone_id not in breaker_death:
                breaker_death[update.zone_id] = update.confirmation_timestamp

        for unicorn in unicorns:
            updates = track_zone_fill(
                work,
                zone_id=unicorn.unicorn_id,
                top=unicorn.zone_top,
                bottom=unicorn.zone_bottom,
                direction=unicorn.direction,
                start_timestamp=unicorn.confirmation_timestamp,
                partial_threshold=self.config.partial_fill_threshold,
                full_threshold=self.config.full_fill_threshold,
            )
            analysis.fills.extend(updates)
            if updates:
                # The retest IS the first fill update; it is not a separate detection.
                analysis.retested_at[unicorn.unicorn_id] = updates[0].confirmation_timestamp

            death = breaker_death.get(unicorn.source_breaker_id)
            if death is not None:
                analysis.inherited_invalidation_at[unicorn.unicorn_id] = death

            analysis.status[unicorn.unicorn_id] = self._final_status(updates, death)

        analysis.fills.sort(key=lambda u: (u.confirmation_timestamp, u.zone_id))
        return analysis

    @staticmethod
    def _final_status(updates: list[ZoneFillUpdate], death: datetime | None) -> UnicornStatus:
        """End-of-frame state. Point-in-time queries go through ``status_at``."""
        if death is not None:
            return UnicornStatus.INVALIDATED
        if not updates:
            return UnicornStatus.ACTIVE
        return (
            UnicornStatus.MITIGATED
            if updates[-1].status_after is ZoneStatus.MITIGATED
            else UnicornStatus.PARTIALLY_FILLED
        )

    def events(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[IctEvent]:
        analysis = self.analyse(frame, symbol, timeframe)
        events: list[IctEvent] = []

        for unicorn in analysis.unicorns:
            status = analysis.status.get(unicorn.unicorn_id, UnicornStatus.ACTIVE)
            events.append(
                IctEvent(
                    symbol=unicorn.symbol,
                    timeframe=unicorn.timeframe,
                    event_type=(
                        EventType.UNICORN_BULLISH if unicorn.is_bullish else EventType.UNICORN_BEARISH
                    ),
                    direction=unicorn.direction,
                    event_timestamp=unicorn.event_timestamp,
                    confirmation_timestamp=unicorn.confirmation_timestamp,
                    price_level=unicorn.midpoint,
                    reference_level=unicorn.zone_top,
                    strength=unicorn.overlap_points,
                    created_timestamp=unicorn.event_timestamp,
                    status=_EVENT_STATUS[status],
                    metadata={
                        "unicorn_id": unicorn.unicorn_id,
                        "source_breaker_id": unicorn.source_breaker_id,
                        "source_fvg_id": unicorn.source_fvg_id,
                        "source_order_block_id": unicorn.source_order_block_id,
                        "zone_top": unicorn.zone_top,
                        "zone_bottom": unicorn.zone_bottom,
                        "bars_between": unicorn.bars_between,
                        "fully_contained": unicorn.fully_contained,
                        "lifecycle_status": status.value,
                        **self.config.as_dict(),
                    },
                )
            )

        events.sort(key=lambda e: (e.confirmation_timestamp, e.event_timestamp))
        return events

    def observable_at(
        self, frame: pd.DataFrame, as_of: datetime, symbol: Symbol, timeframe: Timeframe
    ) -> UnicornAnalysis:
        full = self.analyse(frame, symbol, timeframe)
        limited = UnicornAnalysis(
            unicorns=filter_observable(full.unicorns, as_of),
            fills=filter_observable(full.fills, as_of),
        )
        visible = {u.unicorn_id for u in limited.unicorns}
        # A death that has not happened yet is not knowable yet, so the inherited
        # stream is filtered by the same rule as everything else.
        limited.inherited_invalidation_at = {
            k: v for k, v in full.inherited_invalidation_at.items() if k in visible and v <= as_of
        }
        limited.retested_at = {k: v for k, v in full.retested_at.items() if k in visible and v <= as_of}
        limited.status = {
            u.unicorn_id: limited.status_at(u.unicorn_id, as_of) or UnicornStatus.ACTIVE
            for u in limited.unicorns
        }
        return limited

    def with_config(self, config: UnicornConfig) -> UnicornDetector:
        return replace(self, config=config)


_EVENT_STATUS = {
    UnicornStatus.ACTIVE: EventStatus.ACTIVE,
    UnicornStatus.PARTIALLY_FILLED: EventStatus.ACTIVE,
    UnicornStatus.MITIGATED: EventStatus.MITIGATED,
    UnicornStatus.INVALIDATED: EventStatus.INVALIDATED,
}
