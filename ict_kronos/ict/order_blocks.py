"""Order Blocks — the last opposing candle or group, closed through by the move.

Full semantics in ``docs/ict/order_block.md``. Read that first.

**The definition of record for this engine:**

    An Order Block is the last candle, or contiguous group of candles, in the opposing
    direction whose range is subsequently **closed through** by the directional move.

    bullish OB   last down-close candle/group, then a later bar CLOSES ABOVE its high
    bearish OB   last up-close candle/group,   then a later bar CLOSES BELOW its low

Two things follow, and both are load-bearing.

**An Order Block does not require a Fair Value Gap.** OB formation and FVG formation
are different events. ``require_fvg`` exists as an explicit opt-in qualifier and
defaults to *off*; when an FVG happens to print inside the impulse leg it is recorded
as ``related_fvg_id`` — a confluence annotation, never a precondition.

**An Order Block is not observable when its candidate closes.** The candidate is
identified first, but it is only an Order Block once the directional close through its
range has happened. That gap between ``event_timestamp`` and ``confirmation_timestamp``
is typically several bars and is the whole reason this detector is not trivial.

The break must be a **close**. A wick through the boundary confirms nothing — the same
rule R2-03 defaults to for structure breaks, for the same reason: wick breaks fire on
every stop-run.
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
from .composites import (
    ZoneFillUpdate,
    ZoneStatus,
    composite_confirmation,
    confirmed_within,
    first_close_beyond,
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


class ObGrouping(StrEnum):
    """Which candles form the block.

    ``MULTI_CANDLE_GROUP`` (default) — the **maximal contiguous run** of same-direction
    candles ending immediately before the move. Deterministic: the run starts at the
    first candle after the previous non-conforming candle and ends at the last one
    before the move. No discretionary "visual grouping" is possible.

    ``SINGLE_CANDLE`` — only the final candle of that run.

    The group reading is the default because its zone is wider, so it demands a larger
    move to confirm and is touched sooner once active — it errs toward fewer and
    shorter-lived Order Blocks rather than more and longer-lived ones.
    """

    MULTI_CANDLE_GROUP = "multi_candle_group"
    SINGLE_CANDLE = "single_candle"


class ObZoneGeometry(StrEnum):
    """Which prices bound the block.

    ``FULL_RANGE`` (default) — low to high across the group, wicks included.
    ``BODY`` — open/close extremes only.
    """

    FULL_RANGE = "full_range"
    BODY = "body"


class ObStatus(StrEnum):
    """Lifecycle.

    ``MITIGATED`` means price traded back through the zone (a *fill*, from bar
    extremes). ``INVALIDATED`` means a bar **closed** beyond the far edge against the
    block's direction — the block failed. They are different events and only the
    second is the precondition for a Breaker Block.
    """

    ACTIVE = "active"
    PARTIALLY_FILLED = "partially_filled"
    MITIGATED = "mitigated"
    INVALIDATED = "invalidated"


@dataclass(frozen=True)
class OrderBlockConfig:
    """Order Block parameters. Configuration, never literals in the detector."""

    grouping: ObGrouping = ObGrouping.MULTI_CANDLE_GROUP
    geometry: ObZoneGeometry = ObZoneGeometry.FULL_RANGE
    #: The confirming break must be a CLOSE beyond the boundary by MORE than this many
    #: instrument points. 0 means a strict comparison; equality is never a break.
    break_tolerance_points: float = 0.0
    #: How far past the candidate the confirming close may occur. Unbounded search
    #: would qualify a candidate from March with a close in June.
    max_bars_to_confirm: int = 50
    #: Candles in a group must be contiguous in time. Across a weekend the "group" is
    #: two unrelated runs of delivery, not one.
    require_contiguous_bars: bool = True
    #: EXPLICIT OPT-IN QUALIFIERS. Both default OFF — the canonical definition requires
    #: neither, and an OB that silently required an FVG would not be an OB.
    require_fvg: bool = False
    require_displacement: bool = False
    displacement_lookback: int = 20
    displacement_factor: float = 1.5
    #: Fill thresholds for the mitigation stream, as elsewhere in the engine.
    partial_fill_threshold: float = 0.0
    full_fill_threshold: float = 1.0

    def __post_init__(self) -> None:
        if self.break_tolerance_points < 0:
            raise ValueError(f"break_tolerance_points must be >= 0; got {self.break_tolerance_points}")
        if self.max_bars_to_confirm < 1:
            raise ValueError(f"max_bars_to_confirm must be >= 1; got {self.max_bars_to_confirm}")
        if self.displacement_lookback < 1:
            raise ValueError(f"displacement_lookback must be >= 1; got {self.displacement_lookback}")
        if self.displacement_factor <= 0:
            raise ValueError(f"displacement_factor must be > 0; got {self.displacement_factor}")
        if not 0.0 < self.full_fill_threshold <= 1.0:
            raise ValueError(f"full_fill_threshold must be in (0, 1]; got {self.full_fill_threshold}")
        if self.partial_fill_threshold >= self.full_fill_threshold:
            raise ValueError("partial_fill_threshold must be below full_fill_threshold")

    def as_dict(self) -> dict:
        return {
            "grouping": self.grouping.value,
            "geometry": self.geometry.value,
            "break_tolerance_points": self.break_tolerance_points,
            "max_bars_to_confirm": self.max_bars_to_confirm,
            "require_contiguous_bars": self.require_contiguous_bars,
            "require_fvg": self.require_fvg,
            "require_displacement": self.require_displacement,
            "displacement_lookback": self.displacement_lookback,
            "displacement_factor": self.displacement_factor,
        }


@dataclass(frozen=True)
class OrderBlock:
    """One confirmed Order Block. Immutable."""

    order_block_id: str
    symbol: str
    timeframe: str
    direction: Direction
    zone_top: float
    zone_bottom: float
    #: Every candle forming the block, in time order. Timestamps are the identity;
    #: positional indexes are diagnostics only and are not stored.
    source_candle_timestamps: tuple[datetime, ...]
    #: The candidate's own location — the FIRST candle of the group's open.
    event_timestamp: datetime
    #: The close_time of the bar whose CLOSE broke through the block's range.
    confirmation_timestamp: datetime
    #: That breaking bar's open and its close, kept so the trigger is auditable.
    break_bar_timestamp: datetime
    break_close: float
    bars_to_confirm: int
    displacement_ratio: float | None = None
    #: Confluence annotation, NOT a precondition. Present when an FVG confirmed inside
    #: the impulse leg; ``None`` otherwise, including when the OB is perfectly valid.
    related_fvg_id: str | None = None

    def is_observable_at(self, as_of: datetime) -> bool:
        """Delegates to the ONE contract-level predicate — never a private copy."""
        return is_observable_at(self, as_of)

    @property
    def is_bullish(self) -> bool:
        return self.direction is Direction.BULLISH

    @property
    def candle_count(self) -> int:
        return len(self.source_candle_timestamps)

    @property
    def midpoint(self) -> float:
        """The 50% level — ICT's *mean threshold*. Exposed so downstream never
        re-derives it and never gets the sign wrong."""
        return (self.zone_top + self.zone_bottom) / 2.0

    @property
    def mean_threshold(self) -> float:
        return self.midpoint

    @property
    def far_edge(self) -> float:
        """The edge a close beyond which INVALIDATES the block."""
        return self.zone_bottom if self.is_bullish else self.zone_top

    def as_dict(self) -> dict:
        return {
            "order_block_id": self.order_block_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction.value,
            "zone_top": self.zone_top,
            "zone_bottom": self.zone_bottom,
            "source_candle_timestamps": [t.isoformat() for t in self.source_candle_timestamps],
            "event_timestamp": self.event_timestamp.isoformat(),
            "confirmation_timestamp": self.confirmation_timestamp.isoformat(),
            "break_bar_timestamp": self.break_bar_timestamp.isoformat(),
            "break_close": self.break_close,
            "bars_to_confirm": self.bars_to_confirm,
            "displacement_ratio": self.displacement_ratio,
            "related_fvg_id": self.related_fvg_id,
        }


@dataclass
class OrderBlockAnalysis:
    """Blocks, their fill progression, and their failures."""

    blocks: list[OrderBlock] = field(default_factory=list)
    fills: list[ZoneFillUpdate] = field(default_factory=list)
    status: dict[str, ObStatus] = field(default_factory=dict)
    #: ``order_block_id -> the close_time at which a bar closed beyond the far edge``.
    #: This is what R2-05.4 consumes; it is deliberately separate from mitigation.
    invalidated_at: dict[str, datetime] = field(default_factory=dict)

    def block_by_id(self, order_block_id: str) -> OrderBlock | None:
        return next((b for b in self.blocks if b.order_block_id == order_block_id), None)

    def status_at(self, order_block_id: str, as_of: datetime) -> ObStatus | None:
        """Lifecycle state as known at ``as_of``. Point-in-time, never final-state."""
        block = self.block_by_id(order_block_id)
        if block is None or not is_observable_at(block, as_of):
            return None

        failure = self.invalidated_at.get(order_block_id)
        if failure is not None and failure <= as_of:
            return ObStatus.INVALIDATED

        seen = [u for u in self.fills if u.zone_id == order_block_id and is_observable_at(u, as_of)]
        if not seen:
            return ObStatus.ACTIVE
        return (
            ObStatus.MITIGATED
            if seen[-1].status_after is ZoneStatus.MITIGATED
            else (ObStatus.PARTIALLY_FILLED)
        )

    def active_at(self, as_of: datetime) -> list[OrderBlock]:
        return [
            b
            for b in filter_observable(self.blocks, as_of)
            if self.status_at(b.order_block_id, as_of) in (ObStatus.ACTIVE, ObStatus.PARTIALLY_FILLED)
        ]


@dataclass
class OrderBlockDetector:
    """Deterministic Order Block detection: opposing candle/group, closed through."""

    config: OrderBlockConfig = OrderBlockConfig()
    fvg_config: FvgConfig = FvgConfig()

    # ------------------------------------------------------------------ core

    def _runs(self, work: pd.DataFrame) -> list[tuple[int, int, Direction]]:
        """Maximal contiguous runs of same-direction candles.

        A candle is *down-close* when ``close < open`` and *up-close* when
        ``close > open``. A doji (``close == open``) is **neither**: it belongs to no
        run and terminates the one in progress. Stated explicitly because leaving it
        implicit is how "contiguous group" becomes a judgement call.

        Returns ``(start_index, end_index_inclusive, run_direction)``.
        """
        opens = work["open"].to_numpy(dtype="float64")
        closes = work["close"].to_numpy(dtype="float64")
        stamps = work["timestamp"].to_numpy()
        close_times = work["close_time"].to_numpy()

        runs: list[tuple[int, int, Direction]] = []
        start: int | None = None
        current: Direction | None = None

        for i in range(len(work)):
            if closes[i] > opens[i]:
                kind: Direction | None = Direction.BULLISH
            elif closes[i] < opens[i]:
                kind = Direction.BEARISH
            else:
                kind = None  # doji — belongs to no run

            contiguous = (
                start is not None
                and i > 0
                and (not self.config.require_contiguous_bars or close_times[i - 1] == stamps[i])
            )

            if kind is not None and kind is current and contiguous:
                continue

            if start is not None and current is not None:
                runs.append((start, i - 1, current))

            if kind is None:
                start, current = None, None
            else:
                start, current = i, kind

        if start is not None and current is not None:
            runs.append((start, len(work) - 1, current))
        return runs

    def _zone(self, work: pd.DataFrame, lo: int, hi: int) -> tuple[float, float]:
        """The block's price boundaries under the configured geometry."""
        rows = work.iloc[lo : hi + 1]
        if self.config.geometry is ObZoneGeometry.FULL_RANGE:
            return float(rows["high"].max()), float(rows["low"].min())
        bodies_top = rows[["open", "close"]].max(axis=1).max()
        bodies_bottom = rows[["open", "close"]].min(axis=1).min()
        return float(bodies_top), float(bodies_bottom)

    def _displacement(self, work: pd.DataFrame) -> np.ndarray:
        """Bar range ÷ mean range of the bars strictly before it. No look-ahead.

        The identical definition R2-03 and R2-05 use — one definition of displacement
        in this codebase, not three.
        """
        ranges = (work["high"] - work["low"]).astype("float64")
        baseline = ranges.rolling(self.config.displacement_lookback).mean().shift(1)
        with np.errstate(divide="ignore", invalid="ignore"):
            return ranges.to_numpy() / baseline.to_numpy()

    def detect(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[OrderBlock]:
        """Every Order Block whose confirming close has occurred in the observed data."""
        if len(frame) < 2:
            return []

        work = with_close_time(frame, timeframe).sort_values("timestamp", kind="mergesort")
        work = work.reset_index(drop=True)

        point = symbol.spec.point_value
        tolerance = self.config.break_tolerance_points * point
        displacement = self._displacement(work) if self.config.require_displacement else None

        # Always computed. When ``require_fvg`` is off — the default — this supplies the
        # confluence annotation only and gates nothing. ``FvgDetector`` is the ONLY
        # thing in this codebase that decides whether candles contain a gap.
        fvg_zones = FvgDetector(self.fvg_config).detect(frame, symbol, timeframe)

        blocks: list[OrderBlock] = []
        for start, end, run_direction in self._runs(work):
            # A run of DOWN-close candles is the candidate for a BULLISH order block.
            ob_direction = Direction.BULLISH if run_direction is Direction.BEARISH else Direction.BEARISH
            lo = start if self.config.grouping is ObGrouping.MULTI_CANDLE_GROUP else end
            zone_top, zone_bottom = self._zone(work, lo, end)
            if zone_top <= zone_bottom:
                continue

            if end + 1 >= len(work):
                continue
            search = work.iloc[end + 1 : end + 1 + self.config.max_bars_to_confirm]
            if len(search) == 0:
                continue

            bullish = ob_direction is Direction.BULLISH
            level = (zone_top + tolerance) if bullish else (zone_bottom - tolerance)
            hit = first_close_beyond(
                search,
                level=level,
                above=bullish,
                start_timestamp=search["timestamp"].iloc[0].to_pydatetime(),
            )
            if hit is None:
                # The candidate was never closed through. It is not an Order Block —
                # not a pending one, not a weak one. It simply does not exist.
                continue

            break_index, break_row = hit
            ratio = None
            if displacement is not None:
                raw = displacement[break_index]
                if np.isnan(raw) or raw < self.config.displacement_factor:
                    continue
                ratio = float(raw)

            leg_start = work["timestamp"].iloc[end].to_pydatetime()
            leg_end = break_row["close_time"].to_pydatetime()
            related = next(
                (
                    z.zone_id
                    for z in fvg_zones
                    if z.direction is ob_direction
                    and leg_start <= z.formation_timestamp
                    and confirmed_within(z, leg_start, leg_end)
                ),
                None,
            )
            if self.config.require_fvg and related is None:
                continue

            group = tuple(t.to_pydatetime() for t in work["timestamp"].iloc[lo : end + 1])
            event_timestamp = group[0]
            confirmation = composite_confirmation([], own_trigger=leg_end)

            blocks.append(
                OrderBlock(
                    order_block_id=(
                        f"ob:{symbol.value}:{timeframe.value}:"
                        f"{ob_direction.value}:{event_timestamp.isoformat()}"
                    ),
                    symbol=symbol.value,
                    timeframe=timeframe.value,
                    direction=ob_direction,
                    zone_top=zone_top,
                    zone_bottom=zone_bottom,
                    source_candle_timestamps=group,
                    event_timestamp=event_timestamp,
                    confirmation_timestamp=confirmation,
                    break_bar_timestamp=break_row["timestamp"].to_pydatetime(),
                    break_close=float(break_row["close"]),
                    bars_to_confirm=break_index - end,
                    displacement_ratio=ratio,
                    related_fvg_id=related,
                )
            )

        blocks.sort(key=lambda b: (b.confirmation_timestamp, b.order_block_id))
        return blocks

    def analyse(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> OrderBlockAnalysis:
        """Blocks plus their mitigation stream and their failures."""
        blocks = self.detect(frame, symbol, timeframe)
        analysis = OrderBlockAnalysis(blocks=blocks)
        if not blocks:
            return analysis

        work = with_close_time(frame, timeframe).sort_values("timestamp", kind="mergesort")
        work = work.reset_index(drop=True)

        for block in blocks:
            updates = track_zone_fill(
                work,
                zone_id=block.order_block_id,
                top=block.zone_top,
                bottom=block.zone_bottom,
                direction=block.direction,
                start_timestamp=block.confirmation_timestamp,
                partial_threshold=self.config.partial_fill_threshold,
                full_threshold=self.config.full_fill_threshold,
            )
            analysis.fills.extend(updates)

            # Invalidation is a CLOSE beyond the far edge — distinct from a fill, and
            # the precondition R2-05.4 consumes.
            failure = first_close_beyond(
                work,
                level=block.far_edge,
                above=not block.is_bullish,
                start_timestamp=block.confirmation_timestamp,
            )
            if failure is not None:
                analysis.invalidated_at[block.order_block_id] = failure[1]["close_time"].to_pydatetime()

            if block.order_block_id in analysis.invalidated_at:
                analysis.status[block.order_block_id] = ObStatus.INVALIDATED
            elif updates and updates[-1].status_after is ZoneStatus.MITIGATED:
                analysis.status[block.order_block_id] = ObStatus.MITIGATED
            elif updates:
                analysis.status[block.order_block_id] = ObStatus.PARTIALLY_FILLED
            else:
                analysis.status[block.order_block_id] = ObStatus.ACTIVE

        analysis.fills.sort(key=lambda u: (u.confirmation_timestamp, u.zone_id))
        return analysis

    def events(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[IctEvent]:
        """Contract events, one per confirmed block."""
        analysis = self.analyse(frame, symbol, timeframe)
        events: list[IctEvent] = []

        for block in analysis.blocks:
            status = analysis.status.get(block.order_block_id, ObStatus.ACTIVE)
            events.append(
                IctEvent(
                    symbol=block.symbol,
                    timeframe=block.timeframe,
                    event_type=(
                        EventType.ORDER_BLOCK_BULLISH if block.is_bullish else EventType.ORDER_BLOCK_BEARISH
                    ),
                    direction=block.direction,
                    event_timestamp=block.event_timestamp,
                    confirmation_timestamp=block.confirmation_timestamp,
                    price_level=block.mean_threshold,
                    reference_level=block.zone_top if block.is_bullish else block.zone_bottom,
                    strength=block.displacement_ratio,
                    created_timestamp=block.event_timestamp,
                    invalidation_timestamp=analysis.invalidated_at.get(block.order_block_id),
                    status=(
                        EventStatus.INVALIDATED
                        if status is ObStatus.INVALIDATED
                        else EventStatus.MITIGATED if status is ObStatus.MITIGATED else EventStatus.ACTIVE
                    ),
                    metadata={
                        "order_block_id": block.order_block_id,
                        "zone_top": block.zone_top,
                        "zone_bottom": block.zone_bottom,
                        "candle_count": block.candle_count,
                        "source_candle_timestamps": [t.isoformat() for t in block.source_candle_timestamps],
                        "bars_to_confirm": block.bars_to_confirm,
                        "related_fvg_id": block.related_fvg_id,
                        "lifecycle_status": status.value,
                        **self.config.as_dict(),
                    },
                )
            )

        events.sort(key=lambda e: (e.confirmation_timestamp, e.event_timestamp))
        return events

    def observable_at(
        self, frame: pd.DataFrame, as_of: datetime, symbol: Symbol, timeframe: Timeframe
    ) -> OrderBlockAnalysis:
        """The Order Block picture a decision at ``as_of`` may use."""
        full = self.analyse(frame, symbol, timeframe)
        limited = OrderBlockAnalysis(
            blocks=filter_observable(full.blocks, as_of),
            fills=filter_observable(full.fills, as_of),
        )
        limited.invalidated_at = {k: v for k, v in full.invalidated_at.items() if v <= as_of}
        limited.status = {
            b.order_block_id: limited.status_at(b.order_block_id, as_of) or ObStatus.ACTIVE
            for b in limited.blocks
        }
        return limited

    def with_config(self, config: OrderBlockConfig) -> OrderBlockDetector:
        return replace(self, config=config)
