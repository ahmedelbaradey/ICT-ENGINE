"""Inversion Fair Value Gap — a state transition of an existing FVG.

Full semantics in ``docs/ict/ifvg.md``. Read that first.

**The distinction this module exists to preserve.** R2-05 already models FVG
*mitigation*: price entering and filling the gap, measured from bar **extremes**. An
IFVG is a different thing entirely — the gap being *broken through and flipped*,
measured from a bar **close**.

    a wick that fills a gap 100%   -> MITIGATED, and NOT an IFVG
    a close beyond the far edge    -> INVERTED, and an IFVG exists

Both remain answerable about the same zone. Conflating them would make every IFVG
count wrong in the direction that flatters the concept, which is exactly why the
default trigger is the strictest of the three available.

**Provenance, not duplication.** An ``IfvgZone`` stores ``source_fvg_id`` and inherits
the source's geometry unchanged. It never re-detects a gap: ``FvgDetector`` is the only
thing in this codebase that decides whether three candles contain an imbalance.
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
from .fvg import FvgConfig, FvgDetector, FvgZone

logger = get_logger(__name__)


class InversionTrigger(StrEnum):
    """What counts as inverting a gap.

    ``CLOSE_THROUGH_FAR_EDGE`` (default) — a bar closes beyond the zone's far edge:
    the gap was traversed and rejected outright. The strictest reading, and the one
    that keeps inversion clearly distinct from mitigation.

    ``CLOSE_INSIDE_ZONE`` — a bar closes anywhere past the entry edge. Looser; emits
    an IFVG for gaps that were merely closed into.

    ``WICK_THROUGH`` — a wick beyond the far edge suffices. Present so the leakage
    suite can exercise the naive implementation against the causal one. **Not a
    recommended setting**: it makes inversion nearly synonymous with mitigation and
    fires a bar earlier than the information warrants.
    """

    CLOSE_THROUGH_FAR_EDGE = "close_through_far_edge"
    CLOSE_INSIDE_ZONE = "close_inside_zone"
    WICK_THROUGH = "wick_through"


@dataclass(frozen=True)
class IfvgConfig:
    """IFVG parameters. Configuration, never literals in the detector."""

    trigger: InversionTrigger = InversionTrigger.CLOSE_THROUGH_FAR_EDGE
    #: Any penetration beyond this fraction counts as a partial fill of the INVERTED zone.
    partial_fill_threshold: float = 0.0
    #: Fraction of the inverted zone that must be retraced for full mitigation.
    full_fill_threshold: float = 1.0
    #: A gap must invert within this many bars of its confirmation, or it never does.
    #: Unbounded search would pair a March gap with a June close and call it a flip.
    max_bars_to_invert: int = 500

    def __post_init__(self) -> None:
        if not 0.0 <= self.partial_fill_threshold <= 1.0:
            raise ValueError(f"partial_fill_threshold must be in [0, 1]; got {self.partial_fill_threshold}")
        if not 0.0 < self.full_fill_threshold <= 1.0:
            raise ValueError(f"full_fill_threshold must be in (0, 1]; got {self.full_fill_threshold}")
        if self.partial_fill_threshold >= self.full_fill_threshold:
            raise ValueError("partial_fill_threshold must be below full_fill_threshold")
        if self.max_bars_to_invert < 1:
            raise ValueError(f"max_bars_to_invert must be >= 1; got {self.max_bars_to_invert}")

    def as_dict(self) -> dict:
        return {
            "trigger": self.trigger.value,
            "partial_fill_threshold": self.partial_fill_threshold,
            "full_fill_threshold": self.full_fill_threshold,
            "max_bars_to_invert": self.max_bars_to_invert,
        }


@dataclass(frozen=True)
class IfvgZone:
    """One inverted Fair Value Gap. Immutable."""

    ifvg_id: str
    #: Provenance. Resolves to an ``FvgZone`` produced by the same analysis.
    source_fvg_id: str
    symbol: str
    timeframe: str
    #: The source FVG's polarity, retained so the flip is auditable.
    original_direction: Direction
    #: The inverted polarity — what this zone now acts as.
    direction: Direction
    zone_top: float
    zone_bottom: float
    #: The inverting bar's open — where the flip sits on the chart.
    event_timestamp: datetime
    #: The inverting bar's close_time. The trigger reads a close, so this is when the
    #: inversion could first be known.
    confirmation_timestamp: datetime
    #: The source's confirmation, carried so the composite invariant is checkable
    #: from the record alone without re-running the FVG detector.
    source_fvg_confirmation: datetime
    #: The close that triggered the inversion.
    close_through_price: float
    bars_to_invert: int

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
            "ifvg_id": self.ifvg_id,
            "source_fvg_id": self.source_fvg_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "original_direction": self.original_direction.value,
            "direction": self.direction.value,
            "zone_top": self.zone_top,
            "zone_bottom": self.zone_bottom,
            "event_timestamp": self.event_timestamp.isoformat(),
            "confirmation_timestamp": self.confirmation_timestamp.isoformat(),
            "source_fvg_confirmation": self.source_fvg_confirmation.isoformat(),
            "close_through_price": self.close_through_price,
            "bars_to_invert": self.bars_to_invert,
        }


@dataclass
class IfvgAnalysis:
    """Inverted zones plus their post-inversion fill progression."""

    zones: list[IfvgZone] = field(default_factory=list)
    fills: list[ZoneFillUpdate] = field(default_factory=list)
    status: dict[str, ZoneStatus] = field(default_factory=dict)
    #: FVGs that were mitigated but never inverted — the population the naive reading
    #: would have wrongly counted as IFVGs. Kept so the distinction is inspectable.
    mitigated_without_inversion: list[str] = field(default_factory=list)

    def zone_by_id(self, ifvg_id: str) -> IfvgZone | None:
        return next((z for z in self.zones if z.ifvg_id == ifvg_id), None)

    def status_at(self, ifvg_id: str, as_of: datetime) -> ZoneStatus | None:
        """Lifecycle state as known at ``as_of``. Point-in-time, never final-state."""
        zone = self.zone_by_id(ifvg_id)
        if zone is None or not is_observable_at(zone, as_of):
            return None
        seen = [u for u in self.fills if u.zone_id == ifvg_id and is_observable_at(u, as_of)]
        return seen[-1].status_after if seen else ZoneStatus.ACTIVE

    def active_at(self, as_of: datetime) -> list[IfvgZone]:
        return [
            z
            for z in filter_observable(self.zones, as_of)
            if self.status_at(z.ifvg_id, as_of) is not ZoneStatus.MITIGATED
        ]


@dataclass
class IfvgDetector:
    """Deterministic FVG inversion. Consumes ``FvgDetector``; never re-detects gaps."""

    config: IfvgConfig = IfvgConfig()
    fvg_config: FvgConfig = FvgConfig()

    @property
    def fvg_detector(self) -> FvgDetector:
        return FvgDetector(self.fvg_config)

    def _inverted(self, direction: Direction) -> Direction:
        return Direction.BEARISH if direction is Direction.BULLISH else Direction.BULLISH

    def _trigger_hit(self, zone: FvgZone, row) -> bool:
        """Whether this bar inverts the zone, under the configured trigger."""
        bullish_source = zone.direction is Direction.BULLISH
        close = float(row["close"])

        if self.config.trigger is InversionTrigger.CLOSE_THROUGH_FAR_EDGE:
            # A bullish gap is support: inverting means closing BELOW its bottom.
            return close < zone.bottom if bullish_source else close > zone.top
        if self.config.trigger is InversionTrigger.CLOSE_INSIDE_ZONE:
            return close < zone.top if bullish_source else close > zone.bottom
        # WICK_THROUGH — the naive reading, kept only so tests can exercise it.
        return float(row["low"]) < zone.bottom if bullish_source else float(row["high"]) > zone.top

    def detect(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[IfvgZone]:
        """Every FVG that has inverted within the observed data."""
        zones = self.fvg_detector.detect(frame, symbol, timeframe)
        if not zones:
            return []

        work = with_close_time(frame, timeframe).sort_values("timestamp", kind="mergesort")
        work = work.reset_index(drop=True)

        inverted: list[IfvgZone] = []
        for zone in zones:
            # Search starts at the first bar AFTER C3 closed. C3 cannot invert the gap
            # it defines, and nothing before C3's close was knowable.
            window = work[work["timestamp"] >= pd.Timestamp(zone.confirmation_timestamp)]
            window = window.head(self.config.max_bars_to_invert)

            for offset, (_, row) in enumerate(window.iterrows(), start=1):
                if not self._trigger_hit(zone, row):
                    continue

                event_timestamp = row["timestamp"].to_pydatetime()
                confirmation = composite_confirmation(
                    [zone.confirmation_timestamp],
                    own_trigger=row["close_time"].to_pydatetime(),
                )
                new_direction = self._inverted(zone.direction)
                inverted.append(
                    IfvgZone(
                        # The source is part of the identity: several gaps can invert on
                        # the SAME bar, and without it their ids collide.
                        ifvg_id=(
                            f"ifvg:{symbol.value}:{timeframe.value}:"
                            f"{event_timestamp.isoformat()}:{zone.zone_id}"
                        ),
                        source_fvg_id=zone.zone_id,
                        symbol=symbol.value,
                        timeframe=timeframe.value,
                        original_direction=zone.direction,
                        direction=new_direction,
                        zone_top=float(zone.top),
                        zone_bottom=float(zone.bottom),
                        event_timestamp=event_timestamp,
                        confirmation_timestamp=confirmation,
                        source_fvg_confirmation=zone.confirmation_timestamp,
                        close_through_price=float(row["close"]),
                        bars_to_invert=offset,
                    )
                )
                # One inversion per gap, terminal. A zone does not flip back.
                break

        inverted.sort(key=lambda z: (z.confirmation_timestamp, z.ifvg_id))
        return inverted

    def analyse(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> IfvgAnalysis:
        """Inverted zones plus their post-inversion fill progression."""
        zones = self.detect(frame, symbol, timeframe)
        analysis = IfvgAnalysis(zones=zones)

        if len(frame) == 0:
            return analysis

        work = with_close_time(frame, timeframe).sort_values("timestamp", kind="mergesort")
        work = work.reset_index(drop=True)

        for zone in zones:
            updates = track_zone_fill(
                work,
                zone_id=zone.ifvg_id,
                top=zone.zone_top,
                bottom=zone.zone_bottom,
                direction=zone.direction,
                # Filling starts on the bar AFTER the one that inverted it: that bar
                # established the flip, it did not also retest it.
                start_timestamp=zone.confirmation_timestamp,
                partial_threshold=self.config.partial_fill_threshold,
                full_threshold=self.config.full_fill_threshold,
            )
            analysis.fills.extend(updates)
            analysis.status[zone.ifvg_id] = updates[-1].status_after if updates else ZoneStatus.ACTIVE

        # The population the naive reading conflates with inversion, kept inspectable.
        fvg_analysis = self.fvg_detector.analyse(frame, symbol, timeframe)
        inverted_sources = {z.source_fvg_id for z in zones}
        analysis.mitigated_without_inversion = [
            zone_id
            for zone_id, status in fvg_analysis.status.items()
            if status.value == "mitigated" and zone_id not in inverted_sources
        ]

        analysis.fills.sort(key=lambda u: (u.confirmation_timestamp, u.zone_id))
        return analysis

    def events(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[IctEvent]:
        """Contract events, one per inverted zone."""
        analysis = self.analyse(frame, symbol, timeframe)
        events: list[IctEvent] = []

        for zone in analysis.zones:
            status = analysis.status.get(zone.ifvg_id, ZoneStatus.ACTIVE)
            events.append(
                IctEvent(
                    symbol=zone.symbol,
                    timeframe=zone.timeframe,
                    event_type=(EventType.IFVG_BULLISH if zone.is_bullish else EventType.IFVG_BEARISH),
                    direction=zone.direction,
                    event_timestamp=zone.event_timestamp,
                    confirmation_timestamp=zone.confirmation_timestamp,
                    price_level=zone.midpoint,
                    reference_level=zone.close_through_price,
                    strength=zone.size,
                    created_timestamp=zone.event_timestamp,
                    status=(EventStatus.MITIGATED if status is ZoneStatus.MITIGATED else EventStatus.ACTIVE),
                    metadata={
                        "ifvg_id": zone.ifvg_id,
                        "source_fvg_id": zone.source_fvg_id,
                        "original_direction": zone.original_direction.value,
                        "zone_top": zone.zone_top,
                        "zone_bottom": zone.zone_bottom,
                        "bars_to_invert": zone.bars_to_invert,
                        "lifecycle_status": status.value,
                        **self.config.as_dict(),
                    },
                )
            )

        events.sort(key=lambda e: (e.confirmation_timestamp, e.event_timestamp))
        return events

    def observable_at(
        self, frame: pd.DataFrame, as_of: datetime, symbol: Symbol, timeframe: Timeframe
    ) -> IfvgAnalysis:
        """The IFVG picture a decision at ``as_of`` may use."""
        full = self.analyse(frame, symbol, timeframe)
        limited = IfvgAnalysis(
            zones=filter_observable(full.zones, as_of),
            fills=filter_observable(full.fills, as_of),
        )
        limited.status = {
            z.ifvg_id: limited.status_at(z.ifvg_id, as_of) or ZoneStatus.ACTIVE for z in limited.zones
        }
        return limited

    def with_config(self, config: IfvgConfig) -> IfvgDetector:
        return replace(self, config=config)
