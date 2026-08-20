"""RDRB — Redelivered Rebalanced Price Range. A FOUR-candle pattern.

Full semantics in ``docs/ict/rdrb.md``. Read that first.

**This engine's definition of record — four candles, C1 → C2 → C3 → C4:**

===  ==========================================================================
C1   opens the initial delivery
C2   continues it and prints the **protected wick extreme**
C3   the intervening continuation candle
C4   the redelivery candle, which must NOT reach the protected wick
===  ==========================================================================

    bullish   valid iff  C4.low  >  C2.low       (C2.low  is the protected extreme)
    bearish   valid iff  C4.high <  C2.high      (C2.high is the protected extreme)

The comparison is **wick to wick**. Never close-to-close, never body-to-body, never
body-to-wick. Equality is **invalid** by default: `C4.low == C2.low` reached the
protected extreme, and reaching it is violating it.

**The two-candle and three-candle readings in circulation are not implemented here.**
They are recorded in ``docs/ict/rdrb.md`` as alternatives not adopted.

**Confirmation is C4's close, and nothing earlier.** The validity condition is a
statement about C4, so no amount of information at C1, C2 or C3 can establish it.
A detector that published at C2 or C3 would be asserting a fact about a candle that
had not printed — which is the leak this module's tests are built around.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime

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

logger = get_logger(__name__)

#: The pattern is exactly four candles. Named rather than written as a literal because
#: the count IS the definition, not a tunable window.
RDRB_CANDLES = 4


@dataclass(frozen=True)
class RdrbConfig:
    """RDRB parameters.

    The directional prerequisites are **engineering assumptions where the source is
    silent** (``docs/ict/rdrb.md`` §10), not quoted rules — hence one flag each, so a
    reader can see exactly which conditions are being imposed. What is *not*
    configurable is the four-candle shape or the protected-wick comparison: those are
    the definition.
    """

    #: C1 must close in the delivery direction ("opens the initial delivery").
    require_directional_c1: bool = True
    #: C2 must close in the delivery direction ("continues it").
    require_directional_c2: bool = True
    #: C3 is described only as the intervening candle, so its close is NOT constrained
    #: by default. Turn on for the strictest reading.
    require_directional_c3: bool = False
    #: C4 must close in the delivery direction ("the redelivery candle").
    require_directional_c4: bool = True
    #: C4 must clear the protected wick by MORE than this many instrument points.
    #: 0 keeps the strict inequality, under which equality is invalid.
    wick_tolerance_points: float = 0.0
    #: The four candles must be contiguous in time. Across a weekend they are not one
    #: delivery sequence, whatever their positions in the array suggest.
    require_contiguous_bars: bool = True
    partial_fill_threshold: float = 0.0
    full_fill_threshold: float = 1.0

    def __post_init__(self) -> None:
        if self.wick_tolerance_points < 0:
            raise ValueError(f"wick_tolerance_points must be >= 0; got {self.wick_tolerance_points}")
        if not 0.0 < self.full_fill_threshold <= 1.0:
            raise ValueError(f"full_fill_threshold must be in (0, 1]; got {self.full_fill_threshold}")
        if self.partial_fill_threshold >= self.full_fill_threshold:
            raise ValueError("partial_fill_threshold must be below full_fill_threshold")

    def as_dict(self) -> dict:
        return {
            "require_directional_c1": self.require_directional_c1,
            "require_directional_c2": self.require_directional_c2,
            "require_directional_c3": self.require_directional_c3,
            "require_directional_c4": self.require_directional_c4,
            "wick_tolerance_points": self.wick_tolerance_points,
            "require_contiguous_bars": self.require_contiguous_bars,
        }


@dataclass(frozen=True)
class RdrbZone:
    """One confirmed Redelivered Rebalanced Price Range. Immutable."""

    rdrb_id: str
    symbol: str
    timeframe: str
    direction: Direction
    zone_top: float
    zone_bottom: float
    #: Provenance — all four source candles, in order. Timestamps are the identity.
    source_candle_timestamps: tuple[datetime, datetime, datetime, datetime]
    c1_timestamp: datetime
    c2_timestamp: datetime
    c3_timestamp: datetime
    c4_timestamp: datetime
    #: C2's protected extreme: its low (bullish) or its high (bearish).
    protected_wick: float
    #: C4's corresponding extreme — the one that had to stay clear of it.
    validation_wick: float
    #: How far C4 stayed clear, in instrument points. Strictly positive by definition.
    clearance_points: float
    #: C1's open — where the sequence begins on the chart.
    event_timestamp: datetime
    #: C4's close_time. The validity condition is a statement about C4.
    confirmation_timestamp: datetime

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
            "rdrb_id": self.rdrb_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction.value,
            "zone_top": self.zone_top,
            "zone_bottom": self.zone_bottom,
            "source_candle_timestamps": [t.isoformat() for t in self.source_candle_timestamps],
            "protected_wick": self.protected_wick,
            "validation_wick": self.validation_wick,
            "clearance_points": self.clearance_points,
            "event_timestamp": self.event_timestamp.isoformat(),
            "confirmation_timestamp": self.confirmation_timestamp.isoformat(),
        }


@dataclass
class RdrbAnalysis:
    zones: list[RdrbZone] = field(default_factory=list)
    fills: list[ZoneFillUpdate] = field(default_factory=list)
    status: dict[str, ZoneStatus] = field(default_factory=dict)

    def zone_by_id(self, rdrb_id: str) -> RdrbZone | None:
        return next((z for z in self.zones if z.rdrb_id == rdrb_id), None)

    def status_at(self, rdrb_id: str, as_of: datetime) -> ZoneStatus | None:
        zone = self.zone_by_id(rdrb_id)
        if zone is None or not is_observable_at(zone, as_of):
            return None
        seen = [u for u in self.fills if u.zone_id == rdrb_id and is_observable_at(u, as_of)]
        return seen[-1].status_after if seen else ZoneStatus.ACTIVE


@dataclass
class RdrbDetector:
    """Deterministic four-candle RDRB detection."""

    config: RdrbConfig = RdrbConfig()

    def detect(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[RdrbZone]:
        """Every four-candle sequence whose C4 respected C2's protected wick."""
        if len(frame) < RDRB_CANDLES:
            # Insufficient history is not an error; it is simply too early.
            return []

        work = with_close_time(frame, timeframe).sort_values("timestamp", kind="mergesort")
        work = work.reset_index(drop=True)

        opens = work["open"].to_numpy(dtype="float64")
        highs = work["high"].to_numpy(dtype="float64")
        lows = work["low"].to_numpy(dtype="float64")
        closes = work["close"].to_numpy(dtype="float64")
        stamps = work["timestamp"].to_numpy()
        close_times = work["close_time"].to_numpy()

        contiguous = close_times[:-1] == stamps[1:]
        point = symbol.spec.point_value
        tolerance = self.config.wick_tolerance_points * point

        zones: list[RdrbZone] = []
        for c1 in range(len(work) - RDRB_CANDLES + 1):
            c2, c3, c4 = c1 + 1, c1 + 2, c1 + 3

            if self.config.require_contiguous_bars and not (
                contiguous[c1] and contiguous[c2] and contiguous[c3]
            ):
                # A weekend or data gap inside the sequence. Positionally adjacent, but
                # not one delivery sequence.
                continue

            for direction in (Direction.BULLISH, Direction.BEARISH):
                up = direction is Direction.BULLISH

                required = [
                    (c1, self.config.require_directional_c1),
                    (c2, self.config.require_directional_c2),
                    (c3, self.config.require_directional_c3),
                    (c4, self.config.require_directional_c4),
                ]
                if any(
                    flag and not ((closes[i] > opens[i]) if up else (closes[i] < opens[i]))
                    for i, flag in required
                ):
                    continue

                # THE definition: wick to wick, C4 against C2's protected extreme.
                protected = lows[c2] if up else highs[c2]
                validation = lows[c4] if up else highs[c4]
                clearance = (validation - protected) if up else (protected - validation)
                if clearance <= tolerance:
                    # C4 reached or violated the protected wick. Reaching it IS
                    # violating it — equality is invalid by default.
                    continue

                zone_top = float(validation if up else protected)
                zone_bottom = float(protected if up else validation)

                event_timestamp = pd.Timestamp(stamps[c1]).to_pydatetime()
                confirmation = composite_confirmation(
                    [], own_trigger=pd.Timestamp(close_times[c4]).to_pydatetime()
                )
                group = tuple(pd.Timestamp(stamps[i]).to_pydatetime() for i in (c1, c2, c3, c4))

                zones.append(
                    RdrbZone(
                        rdrb_id=(
                            f"rdrb:{symbol.value}:{timeframe.value}:"
                            f"{direction.value}:{event_timestamp.isoformat()}"
                        ),
                        symbol=symbol.value,
                        timeframe=timeframe.value,
                        direction=direction,
                        zone_top=zone_top,
                        zone_bottom=zone_bottom,
                        source_candle_timestamps=group,
                        c1_timestamp=group[0],
                        c2_timestamp=group[1],
                        c3_timestamp=group[2],
                        c4_timestamp=group[3],
                        protected_wick=float(protected),
                        validation_wick=float(validation),
                        clearance_points=float(clearance / point) if point else float(clearance),
                        event_timestamp=event_timestamp,
                        confirmation_timestamp=confirmation,
                    )
                )

        zones.sort(key=lambda z: (z.confirmation_timestamp, z.rdrb_id))
        return zones

    def analyse(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> RdrbAnalysis:
        zones = self.detect(frame, symbol, timeframe)
        analysis = RdrbAnalysis(zones=zones)
        if not zones:
            return analysis

        work = with_close_time(frame, timeframe).sort_values("timestamp", kind="mergesort")
        work = work.reset_index(drop=True)

        for zone in zones:
            updates = track_zone_fill(
                work,
                zone_id=zone.rdrb_id,
                top=zone.zone_top,
                bottom=zone.zone_bottom,
                direction=zone.direction,
                start_timestamp=zone.confirmation_timestamp,
                partial_threshold=self.config.partial_fill_threshold,
                full_threshold=self.config.full_fill_threshold,
            )
            analysis.fills.extend(updates)
            analysis.status[zone.rdrb_id] = updates[-1].status_after if updates else ZoneStatus.ACTIVE

        analysis.fills.sort(key=lambda u: (u.confirmation_timestamp, u.zone_id))
        return analysis

    def events(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[IctEvent]:
        analysis = self.analyse(frame, symbol, timeframe)
        events: list[IctEvent] = []

        for zone in analysis.zones:
            status = analysis.status.get(zone.rdrb_id, ZoneStatus.ACTIVE)
            events.append(
                IctEvent(
                    symbol=zone.symbol,
                    timeframe=zone.timeframe,
                    event_type=(EventType.RDRB_BULLISH if zone.is_bullish else EventType.RDRB_BEARISH),
                    direction=zone.direction,
                    event_timestamp=zone.event_timestamp,
                    confirmation_timestamp=zone.confirmation_timestamp,
                    price_level=zone.midpoint,
                    reference_level=zone.protected_wick,
                    strength=zone.clearance_points,
                    created_timestamp=zone.event_timestamp,
                    status=(EventStatus.MITIGATED if status is ZoneStatus.MITIGATED else EventStatus.ACTIVE),
                    metadata={
                        "rdrb_id": zone.rdrb_id,
                        "zone_top": zone.zone_top,
                        "zone_bottom": zone.zone_bottom,
                        "protected_wick": zone.protected_wick,
                        "validation_wick": zone.validation_wick,
                        "source_candle_timestamps": [t.isoformat() for t in zone.source_candle_timestamps],
                        "lifecycle_status": status.value,
                        **self.config.as_dict(),
                    },
                )
            )

        events.sort(key=lambda e: (e.confirmation_timestamp, e.event_timestamp))
        return events

    def observable_at(
        self, frame: pd.DataFrame, as_of: datetime, symbol: Symbol, timeframe: Timeframe
    ) -> RdrbAnalysis:
        full = self.analyse(frame, symbol, timeframe)
        limited = RdrbAnalysis(
            zones=filter_observable(full.zones, as_of),
            fills=filter_observable(full.fills, as_of),
        )
        limited.status = {
            z.rdrb_id: limited.status_at(z.rdrb_id, as_of) or ZoneStatus.ACTIVE for z in limited.zones
        }
        return limited

    def with_config(self, config: RdrbConfig) -> RdrbDetector:
        return replace(self, config=config)
