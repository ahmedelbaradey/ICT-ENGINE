"""Fair Value Gaps — three-candle imbalance zones with correct confirmation timing.

Definitions, the legacy bug this story exists to not repeat, and every ambiguity we
did NOT silently resolve are in ``docs/ict/fvg.md``. Read that first.

**The one thing that matters here.** A bullish FVG's condition reads ``low(C3)``, and
a bar's low is not final until it closes. So:

    formation_timestamp    = C3's OPEN   (where the pattern sits on the chart)
    confirmation_timestamp = C3's CLOSE  (when it could first be known)

They differ by exactly one bar duration, always. ``ForexQuant``'s implementation has a
single ``StartTime`` field set to C3's *open* — so every consumer asking "which FVGs
existed at time t?" gets each one a bar early. Two required fields plus the contract's
``confirmation >= event`` invariant make that error unrepresentable here.

**Zones are immutable; fill progression is a stream of timestamped updates**, the same
level/sweep separation R2-04 uses. A confirmed zone is never mutated.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

import numpy as np
import pandas as pd

from ..app.logging import get_logger
from ..data.resampler import with_close_time
from ..domain import Symbol, Timeframe
from .contract import (
    Direction,
    EventStatus,
    EventType,
    IctEvent,
    filter_observable,
    is_observable_at,
)

logger = get_logger(__name__)


class GapMeasure(StrEnum):
    """Which prices bound the gap.

    ``WICK`` (default) uses high/low; ``BODY`` uses ``max(open, close)`` /
    ``min(open, close)``. Wick is both the more common ICT reading and the more
    conservative — a body-measured gap is always at least as wide, so it would report
    imbalances the wick measure denies.
    """

    WICK = "wick"
    BODY = "body"


class FvgStatus(StrEnum):
    """Lifecycle. ``MITIGATED`` is terminal and IS invalidation — see docs §5."""

    ACTIVE = "active"
    PARTIALLY_FILLED = "partially_filled"
    MITIGATED = "mitigated"


@dataclass(frozen=True)
class FvgConfig:
    """FVG parameters. Configuration, never literals in the detector."""

    #: Gap must exceed this many instrument points. 0 = any strictly positive gap.
    min_gap_points: float = 0.0
    measure: GapMeasure = GapMeasure.WICK
    #: The three candles must be CONTIGUOUS in time. Across a weekend or a data gap
    #: the price jump would otherwise manufacture a large, entirely fictitious
    #: imbalance — a phantom FVG at every closure. See docs §8.
    require_contiguous_bars: bool = True
    #: Any penetration beyond this fraction counts as a partial fill.
    partial_fill_threshold: float = 0.0
    #: Fraction of the zone that must be retraced for full mitigation.
    full_fill_threshold: float = 1.0
    #: Optional "expansion candle" variant: require C2's range to exceed
    #: ``displacement_factor`` x the mean range of the previous N bars.
    require_displacement: bool = False
    displacement_lookback: int = 20
    displacement_factor: float = 1.5

    def __post_init__(self) -> None:
        if self.min_gap_points < 0:
            raise ValueError(f"min_gap_points must be >= 0; got {self.min_gap_points}")
        if not 0.0 <= self.partial_fill_threshold <= 1.0:
            raise ValueError(f"partial_fill_threshold must be in [0, 1]; got {self.partial_fill_threshold}")
        if not 0.0 < self.full_fill_threshold <= 1.0:
            raise ValueError(f"full_fill_threshold must be in (0, 1]; got {self.full_fill_threshold}")
        if self.partial_fill_threshold >= self.full_fill_threshold:
            raise ValueError(
                f"partial_fill_threshold ({self.partial_fill_threshold}) must be below "
                f"full_fill_threshold ({self.full_fill_threshold})"
            )
        if self.displacement_lookback < 1:
            raise ValueError(f"displacement_lookback must be >= 1; got {self.displacement_lookback}")
        if self.displacement_factor <= 0:
            raise ValueError(f"displacement_factor must be > 0; got {self.displacement_factor}")

    def as_dict(self) -> dict:
        return {
            "min_gap_points": self.min_gap_points,
            "measure": self.measure.value,
            "require_contiguous_bars": self.require_contiguous_bars,
            "partial_fill_threshold": self.partial_fill_threshold,
            "full_fill_threshold": self.full_fill_threshold,
            "require_displacement": self.require_displacement,
            "displacement_lookback": self.displacement_lookback,
            "displacement_factor": self.displacement_factor,
        }


@dataclass(frozen=True)
class FvgZone:
    """One confirmed three-candle imbalance. Immutable."""

    zone_id: str
    symbol: str
    timeframe: str
    direction: Direction  # BULLISH | BEARISH
    top: float
    bottom: float
    #: C3's open — where the completed pattern sits on the chart.
    formation_timestamp: datetime
    #: C3's close_time — the earliest instant the FVG could be known.
    confirmation_timestamp: datetime
    candle1_timestamp: datetime
    candle2_timestamp: datetime
    candle3_timestamp: datetime
    size: float
    size_points: float
    #: Positional index of C3 in the source frame. Diagnostics only, never a join key.
    index: int
    displacement_ratio: float | None = None

    def is_observable_at(self, as_of: datetime) -> bool:
        """Delegates to the ONE contract-level predicate — never a private copy."""
        return is_observable_at(self, as_of)

    @property
    def is_bullish(self) -> bool:
        return self.direction is Direction.BULLISH

    @property
    def midpoint(self) -> float:
        """The 50% level — ICT's *consequent encroachment*. Provided so downstream
        never re-derives it (and never gets the sign wrong)."""
        return (self.top + self.bottom) / 2.0

    @property
    def entry_edge(self) -> float:
        """The edge price must cross to begin filling the zone."""
        return self.top if self.is_bullish else self.bottom

    @property
    def far_edge(self) -> float:
        """The edge price must reach for full mitigation."""
        return self.bottom if self.is_bullish else self.top

    def fill_fraction(self, extreme: float) -> float:
        """How much of the zone a penetrating extreme has retraced, in ``[0, 1]``.

        ``extreme`` is the lowest low seen since C3 for a bullish zone, the highest
        high for a bearish one. Touching the entry edge exactly gives 0 — a touch is
        not a fill.
        """
        span = self.top - self.bottom
        if span <= 0:  # pragma: no cover - construction guarantees a positive span
            return 0.0
        raw = (self.top - extreme) / span if self.is_bullish else (extreme - self.bottom) / span
        return float(min(max(raw, 0.0), 1.0))

    def as_dict(self) -> dict:
        return {
            "zone_id": self.zone_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction.value,
            "top": self.top,
            "bottom": self.bottom,
            "midpoint": self.midpoint,
            "formation_timestamp": self.formation_timestamp.isoformat(),
            "confirmation_timestamp": self.confirmation_timestamp.isoformat(),
            "candle1_timestamp": self.candle1_timestamp.isoformat(),
            "candle2_timestamp": self.candle2_timestamp.isoformat(),
            "candle3_timestamp": self.candle3_timestamp.isoformat(),
            "size": self.size,
            "size_points": self.size_points,
            "index": self.index,
            "displacement_ratio": self.displacement_ratio,
        }


@dataclass(frozen=True)
class FvgFillUpdate:
    """A timestamped increase in how much of a zone has been retraced.

    Emitted only when the fill deepens, so the sequence is monotonic per zone and a
    point-in-time query is a lookup rather than a rescan.
    """

    zone_id: str
    #: The penetrating bar's open.
    event_timestamp: datetime
    #: The penetrating bar's close — when the fill became knowable.
    confirmation_timestamp: datetime
    fill_percentage: float
    deepest_price: float
    status_after: FvgStatus
    bar_index: int

    def is_observable_at(self, as_of: datetime) -> bool:
        return is_observable_at(self, as_of)

    def as_dict(self) -> dict:
        return {
            "zone_id": self.zone_id,
            "event_timestamp": self.event_timestamp.isoformat(),
            "confirmation_timestamp": self.confirmation_timestamp.isoformat(),
            "fill_percentage": self.fill_percentage,
            "deepest_price": self.deepest_price,
            "status_after": self.status_after.value,
            "bar_index": self.bar_index,
        }


@dataclass
class FvgAnalysis:
    """Zones, fill updates, and end-of-run lifecycle state."""

    zones: list[FvgZone] = field(default_factory=list)
    fills: list[FvgFillUpdate] = field(default_factory=list)
    status: dict[str, FvgStatus] = field(default_factory=dict)
    mitigated_at: dict[str, datetime] = field(default_factory=dict)

    def zone_by_id(self, zone_id: str) -> FvgZone | None:
        return next((z for z in self.zones if z.zone_id == zone_id), None)

    def fill_at(self, zone_id: str, as_of: datetime) -> float:
        """Fill fraction a decision at ``as_of`` may assume. Point-in-time.

        Uses only fill updates that had themselves confirmed, so it can never reflect
        a penetration whose bar had not closed.
        """
        observable = [u for u in filter_observable(self.fills, as_of) if u.zone_id == zone_id]
        return observable[-1].fill_percentage if observable else 0.0

    def status_at(self, zone_id: str, as_of: datetime) -> FvgStatus | None:
        zone = self.zone_by_id(zone_id)
        if zone is None or not zone.is_observable_at(as_of):
            return None
        observable = [u for u in filter_observable(self.fills, as_of) if u.zone_id == zone_id]
        return observable[-1].status_after if observable else FvgStatus.ACTIVE

    def active_at(self, as_of: datetime) -> list[FvgZone]:
        """Zones a decision at ``as_of`` may treat as live imbalance.

        Observable and not yet fully mitigated **as of that instant**. A partially
        filled zone is still active — that is the point of §6.
        """
        mitigated = {
            u.zone_id for u in filter_observable(self.fills, as_of) if u.status_after is FvgStatus.MITIGATED
        }
        return [z for z in filter_observable(self.zones, as_of) if z.zone_id not in mitigated]

    def state_of(self, zone_id: str) -> dict | None:
        """Everything about one zone in a single call, for inspection and R2-07."""
        zone = self.zone_by_id(zone_id)
        if zone is None:
            return None
        updates = [u for u in self.fills if u.zone_id == zone_id]
        status = self.status.get(zone_id, FvgStatus.ACTIVE)
        return {
            **zone.as_dict(),
            "status": status.value,
            "is_active": status is not FvgStatus.MITIGATED,
            "fill_percentage": updates[-1].fill_percentage if updates else 0.0,
            "first_touch_timestamp": (updates[0].confirmation_timestamp.isoformat() if updates else None),
            "mitigation_timestamp": (
                self.mitigated_at[zone_id].isoformat() if zone_id in self.mitigated_at else None
            ),
            # Invalidation IS full mitigation here (docs §5) — exposed under both names
            # so a consumer expecting either finds it.
            "invalidation_timestamp": (
                self.mitigated_at[zone_id].isoformat() if zone_id in self.mitigated_at else None
            ),
            "fill_update_count": len(updates),
        }


@dataclass
class FvgDetector:
    """Deterministic three-candle FVG detection with point-in-time fill tracking."""

    config: FvgConfig = FvgConfig()

    # ------------------------------------------------------------------ core

    def _bounds(self, work: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """Upper and lower bounding prices per bar, per the configured measure."""
        if self.config.measure is GapMeasure.WICK:
            return (
                work["high"].to_numpy(dtype="float64"),
                work["low"].to_numpy(dtype="float64"),
            )
        upper = work[["open", "close"]].max(axis=1).to_numpy(dtype="float64")
        lower = work[["open", "close"]].min(axis=1).to_numpy(dtype="float64")
        return upper, lower

    def _displacement(self, work: pd.DataFrame) -> np.ndarray:
        """C2 range ÷ mean range of the bars strictly before it. No look-ahead."""
        ranges = (work["high"] - work["low"]).astype("float64")
        baseline = ranges.rolling(self.config.displacement_lookback).mean().shift(1)
        with np.errstate(divide="ignore", invalid="ignore"):
            return ranges.to_numpy() / baseline.to_numpy()

    def detect(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[FvgZone]:
        """Every FVG whose C3 has closed within the observed data.

        A zone is emitted only once C3 exists and has closed — which is what makes
        batch detection equal streaming replay.
        """
        if len(frame) < 3:
            # Insufficient history is not an error; it is simply too early.
            return []

        work = with_close_time(frame, timeframe).sort_values("timestamp", kind="mergesort")
        work = work.reset_index(drop=True)

        upper, lower = self._bounds(work)
        timestamps = work["timestamp"].to_numpy()
        close_times = work["close_time"].to_numpy()
        point = symbol.spec.point_value
        min_gap = self.config.min_gap_points * point
        displacement = self._displacement(work) if self.config.require_displacement else None

        # Contiguity: close_time(n) == timestamp(n+1). Computed once, vectorised.
        contiguous = work["close_time"].to_numpy()[:-1] == work["timestamp"].to_numpy()[1:]

        zones: list[FvgZone] = []
        for third in range(2, len(work)):
            first, second = third - 2, third - 1

            if self.config.require_contiguous_bars and not (contiguous[first] and contiguous[second]):
                # A weekend or data gap between the three bars. Positionally adjacent,
                # but nothing traded in between — admitting it would manufacture a
                # large, entirely fictitious imbalance (docs §8).
                continue

            if displacement is not None:
                ratio = displacement[second]
                if np.isnan(ratio) or ratio < self.config.displacement_factor:
                    continue

            bullish = lower[third] > upper[first]
            bearish = upper[third] < lower[first]
            if not (bullish or bearish):
                continue

            top = lower[third] if bullish else lower[first]
            bottom = upper[first] if bullish else upper[third]
            size = top - bottom
            if size <= 0 or size <= min_gap:
                # Exact equality is not a gap; strictness is deliberate (docs §1).
                continue

            direction = Direction.BULLISH if bullish else Direction.BEARISH
            formation = pd.Timestamp(timestamps[third]).to_pydatetime()
            ratio_value = None
            if displacement is not None:
                raw = displacement[second]
                ratio_value = None if np.isnan(raw) or np.isinf(raw) else float(raw)

            zones.append(
                FvgZone(
                    zone_id=f"fvg:{direction.value}:{formation.isoformat()}",
                    symbol=symbol.value,
                    timeframe=timeframe.value,
                    direction=direction,
                    top=float(top),
                    bottom=float(bottom),
                    formation_timestamp=formation,
                    # C3's CLOSE. The condition reads C3's low/high, which is not
                    # final until then — this is the legacy bug, made impossible.
                    confirmation_timestamp=pd.Timestamp(close_times[third]).to_pydatetime(),
                    candle1_timestamp=pd.Timestamp(timestamps[first]).to_pydatetime(),
                    candle2_timestamp=pd.Timestamp(timestamps[second]).to_pydatetime(),
                    candle3_timestamp=formation,
                    size=float(size),
                    size_points=float(size / point) if point else float(size),
                    index=third,
                    displacement_ratio=ratio_value,
                )
            )

        logger.info(
            "fvg %s %s: %d zone(s) from %d bar(s) (measure=%s contiguous=%s)",
            symbol.value,
            timeframe.value,
            len(zones),
            len(work),
            self.config.measure.value,
            self.config.require_contiguous_bars,
        )
        return zones

    def analyse(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> FvgAnalysis:
        """Zones plus their fill progression, walking bars forward."""
        analysis = FvgAnalysis()
        zones = self.detect(frame, symbol, timeframe)
        analysis.zones = zones
        analysis.status = {z.zone_id: FvgStatus.ACTIVE for z in zones}
        if not zones:
            return analysis

        work = with_close_time(frame, timeframe).sort_values("timestamp", kind="mergesort")
        work = work.reset_index(drop=True)
        highs = work["high"].to_numpy(dtype="float64")
        lows = work["low"].to_numpy(dtype="float64")
        open_times = work["timestamp"].to_numpy()
        close_times = work["close_time"].to_numpy()

        # Zones are ordered by C3 index, so a cursor admits each as its bar passes.
        by_confirmation = sorted(zones, key=lambda z: (z.index, z.zone_id))
        cursor = 0
        active: list[FvgZone] = []
        deepest: dict[str, float] = {}

        for index in range(len(work)):
            # Admit zones confirmed by this bar's close. A zone's own C3 is `index`,
            # so it becomes eligible for filling only from the NEXT bar — C3 defines
            # the gap and cannot fill it.
            while cursor < len(by_confirmation) and by_confirmation[cursor].index <= index:
                zone = by_confirmation[cursor]
                cursor += 1
                active.append(zone)
                deepest[zone.zone_id] = zone.entry_edge

            still_active: list[FvgZone] = []
            for zone in active:
                if zone.index >= index:
                    # C3 itself (or earlier) cannot fill its own gap.
                    still_active.append(zone)
                    continue

                extreme = lows[index] if zone.is_bullish else highs[index]
                previous = deepest[zone.zone_id]
                deeper = extreme < previous if zone.is_bullish else extreme > previous
                if not deeper:
                    still_active.append(zone)
                    continue

                deepest[zone.zone_id] = extreme
                fraction = zone.fill_fraction(extreme)
                if fraction <= 0.0:
                    still_active.append(zone)
                    continue

                mitigated = fraction >= self.config.full_fill_threshold
                partial = fraction > self.config.partial_fill_threshold
                if not (mitigated or partial):
                    still_active.append(zone)
                    continue

                status = FvgStatus.MITIGATED if mitigated else FvgStatus.PARTIALLY_FILLED
                analysis.fills.append(
                    FvgFillUpdate(
                        zone_id=zone.zone_id,
                        event_timestamp=pd.Timestamp(open_times[index]).to_pydatetime(),
                        confirmation_timestamp=pd.Timestamp(close_times[index]).to_pydatetime(),
                        fill_percentage=fraction,
                        deepest_price=float(extreme),
                        status_after=status,
                        bar_index=index,
                    )
                )
                analysis.status[zone.zone_id] = status
                if mitigated:
                    analysis.mitigated_at[zone.zone_id] = pd.Timestamp(close_times[index]).to_pydatetime()
                    continue  # terminal: leaves the active set

                still_active.append(zone)

            active = still_active

        analysis.fills.sort(key=lambda u: (u.confirmation_timestamp, u.zone_id))
        logger.info(
            "fvg %s %s: %d zone(s), %d fill update(s), %d mitigated",
            symbol.value,
            timeframe.value,
            len(analysis.zones),
            len(analysis.fills),
            len(analysis.mitigated_at),
        )
        return analysis

    # ---------------------------------------------------------------- public

    def events(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[IctEvent]:
        """Contract events: one per zone. Fill progression stays on the analysis."""
        analysis = self.analyse(frame, symbol, timeframe)
        events: list[IctEvent] = []

        for zone in analysis.zones:
            status = analysis.status.get(zone.zone_id, FvgStatus.ACTIVE)
            updates = [u for u in analysis.fills if u.zone_id == zone.zone_id]
            mitigation = analysis.mitigated_at.get(zone.zone_id)
            events.append(
                IctEvent(
                    symbol=zone.symbol,
                    timeframe=zone.timeframe,
                    event_type=(EventType.FVG_BULLISH if zone.is_bullish else EventType.FVG_BEARISH),
                    direction=zone.direction,
                    event_timestamp=zone.formation_timestamp,
                    confirmation_timestamp=zone.confirmation_timestamp,
                    price_level=zone.midpoint,
                    reference_level=zone.entry_edge,
                    strength=zone.size_points,
                    created_timestamp=zone.formation_timestamp,
                    invalidation_timestamp=mitigation,
                    status=(EventStatus.MITIGATED if status is FvgStatus.MITIGATED else EventStatus.ACTIVE),
                    metadata={
                        "zone_id": zone.zone_id,
                        "top": zone.top,
                        "bottom": zone.bottom,
                        "midpoint": zone.midpoint,
                        "candle1_timestamp": zone.candle1_timestamp.isoformat(),
                        "candle2_timestamp": zone.candle2_timestamp.isoformat(),
                        "candle3_timestamp": zone.candle3_timestamp.isoformat(),
                        "lifecycle_status": status.value,
                        "fill_percentage": updates[-1].fill_percentage if updates else 0.0,
                        "displacement_ratio": zone.displacement_ratio,
                        **self.config.as_dict(),
                    },
                )
            )

        events.sort(key=lambda e: (e.confirmation_timestamp, e.event_timestamp, e.event_type.value))
        return events

    def observable_at(
        self, frame: pd.DataFrame, as_of: datetime, symbol: Symbol, timeframe: Timeframe
    ) -> FvgAnalysis:
        """The FVG picture a decision at ``as_of`` may use."""
        full = self.analyse(frame, symbol, timeframe)
        limited = FvgAnalysis(
            zones=filter_observable(full.zones, as_of),
            fills=filter_observable(full.fills, as_of),
        )
        limited.mitigated_at = {k: v for k, v in full.mitigated_at.items() if v <= as_of}
        limited.status = {
            zone.zone_id: limited.status_at(zone.zone_id, as_of) or FvgStatus.ACTIVE for zone in limited.zones
        }
        return limited

    def with_config(self, config: FvgConfig) -> FvgDetector:
        return replace(self, config=config)


def reference_zones(
    frame: pd.DataFrame, config: FvgConfig, timeframe: Timeframe, point_value: float = 0.0
) -> list[tuple[int, str]]:
    """Deliberately simple reference implementation, for testing only.

    A plain Python loop with no vectorisation, no rolling windows and no numpy — so
    its correctness is obvious by inspection. The tests assert the real detector
    agrees with it on real data. **Never call this on a full history.**
    """
    rows = frame.sort_values("timestamp", kind="mergesort").reset_index(drop=True)
    duration = timeframe.duration
    found: list[tuple[int, str]] = []

    for third in range(2, len(rows)):
        c1, c2, c3 = rows.iloc[third - 2], rows.iloc[third - 1], rows.iloc[third]

        if config.require_contiguous_bars:
            if c1["timestamp"] + duration != c2["timestamp"]:
                continue
            if c2["timestamp"] + duration != c3["timestamp"]:
                continue

        if config.measure is GapMeasure.WICK:
            hi1, lo1 = c1["high"], c1["low"]
            hi3, lo3 = c3["high"], c3["low"]
        else:
            hi1, lo1 = max(c1["open"], c1["close"]), min(c1["open"], c1["close"])
            hi3, lo3 = max(c3["open"], c3["close"]), min(c3["open"], c3["close"])

        if lo3 > hi1:
            if lo3 - hi1 > config.min_gap_points * point_value:
                found.append((third, "bullish"))
        elif hi3 < lo1:
            if lo1 - hi3 > config.min_gap_points * point_value:
                found.append((third, "bearish"))

    return found
