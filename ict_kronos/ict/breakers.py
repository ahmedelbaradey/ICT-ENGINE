"""Breaker Blocks — a failed Order Block that flips polarity.

Full semantics in ``docs/ict/breaker_block.md``. Read that first.

A Breaker is **never** an independent candle pattern. It is defined entirely by the
failure of an Order Block that ``OrderBlockDetector`` already confirmed:

    bullish OB, closed through downward  ->  BEARISH breaker
    bearish OB, closed through upward    ->  BULLISH breaker

Two rules keep this honest.

**A wick through the block is not a failure.** The default ``break_mode`` is ``CLOSE``,
matching R2-03: wick breaks fire on every stop-run, and treating one as a structural
failure would manufacture breakers out of ordinary liquidity grabs.

**Not every broken Order Block becomes a Breaker.** ``require_structure_break`` is on
by default: the failure must be accompanied by a confirmed R2-03 structure break in the
breaker's direction, within a bounded window. Without that condition a "breaker" is
just a block that got filled, and the term stops carrying information. The structure
break is consumed from ``StructureDetector`` — no second BOS algorithm exists here.

The source Order Block is never mutated. The Breaker is a **new** event that points
back at it by id.
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
    confirmed_within,
    first_close_beyond,
    structure_break_id,
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
from .order_blocks import OrderBlock, OrderBlockConfig, OrderBlockDetector
from .structure import StructureConfig, StructureDetector

logger = get_logger(__name__)


class BreakerBreakMode(StrEnum):
    """What counts as failing the source Order Block.

    ``CLOSE`` (default) — a bar closes beyond the block's far edge.
    ``WICK`` — a wick beyond the far edge suffices. Available so the leakage suite can
    run the naive implementation; **not recommended**, for the R2-03 reason.
    """

    CLOSE = "close"
    WICK = "wick"


@dataclass(frozen=True)
class BreakerConfig:
    """Breaker parameters. Configuration, never literals in the detector."""

    break_mode: BreakerBreakMode = BreakerBreakMode.CLOSE
    #: A broken Order Block becomes a Breaker only when a confirmed structure break in
    #: the breaker's direction accompanies the failure. On by default: see module docs.
    require_structure_break: bool = True
    #: How far after the failure the confirming structure break may occur.
    structure_window_bars: int = 20
    partial_fill_threshold: float = 0.0
    full_fill_threshold: float = 1.0

    def __post_init__(self) -> None:
        if self.structure_window_bars < 1:
            raise ValueError(f"structure_window_bars must be >= 1; got {self.structure_window_bars}")
        if not 0.0 < self.full_fill_threshold <= 1.0:
            raise ValueError(f"full_fill_threshold must be in (0, 1]; got {self.full_fill_threshold}")
        if self.partial_fill_threshold >= self.full_fill_threshold:
            raise ValueError("partial_fill_threshold must be below full_fill_threshold")

    def as_dict(self) -> dict:
        return {
            "break_mode": self.break_mode.value,
            "require_structure_break": self.require_structure_break,
            "structure_window_bars": self.structure_window_bars,
        }


@dataclass(frozen=True)
class BreakerBlock:
    """One confirmed Breaker Block. Immutable."""

    breaker_id: str
    #: Provenance. Resolves to an ``OrderBlock`` from the same analysis.
    source_order_block_id: str
    symbol: str
    timeframe: str
    #: The source block's polarity, retained so the flip is auditable.
    original_direction: Direction
    #: The flipped polarity — what this zone now acts as.
    direction: Direction
    zone_top: float
    zone_bottom: float
    #: The bar whose close failed the source block.
    failure_timestamp: datetime
    event_timestamp: datetime
    confirmation_timestamp: datetime
    #: The source block's confirmation, carried so the composite invariant is checkable
    #: from the record alone.
    source_order_block_confirmation: datetime
    #: Provenance for the structure condition; ``None`` when it is not required.
    source_break_id: str | None = None

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
    def far_edge(self) -> float:
        """A close beyond this against the breaker's direction invalidates it."""
        return self.zone_bottom if self.is_bullish else self.zone_top

    def as_dict(self) -> dict:
        return {
            "breaker_id": self.breaker_id,
            "source_order_block_id": self.source_order_block_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "original_direction": self.original_direction.value,
            "direction": self.direction.value,
            "zone_top": self.zone_top,
            "zone_bottom": self.zone_bottom,
            "failure_timestamp": self.failure_timestamp.isoformat(),
            "event_timestamp": self.event_timestamp.isoformat(),
            "confirmation_timestamp": self.confirmation_timestamp.isoformat(),
            "source_order_block_confirmation": self.source_order_block_confirmation.isoformat(),
            "source_break_id": self.source_break_id,
        }


@dataclass
class BreakerAnalysis:
    """Breakers plus their fill progression."""

    breakers: list[BreakerBlock] = field(default_factory=list)
    fills: list[ZoneFillUpdate] = field(default_factory=list)
    status: dict[str, ZoneStatus] = field(default_factory=dict)
    #: Order Blocks that failed but did NOT become Breakers because the structure
    #: condition was absent. Kept so the gate is inspectable rather than invisible.
    failed_without_structure: list[str] = field(default_factory=list)

    def breaker_by_id(self, breaker_id: str) -> BreakerBlock | None:
        return next((b for b in self.breakers if b.breaker_id == breaker_id), None)

    def status_at(self, breaker_id: str, as_of: datetime) -> ZoneStatus | None:
        breaker = self.breaker_by_id(breaker_id)
        if breaker is None or not is_observable_at(breaker, as_of):
            return None
        seen = [u for u in self.fills if u.zone_id == breaker_id and is_observable_at(u, as_of)]
        return seen[-1].status_after if seen else ZoneStatus.ACTIVE


@dataclass
class BreakerDetector:
    """Deterministic Breaker detection. Consumes Order Blocks; detects no patterns."""

    config: BreakerConfig = BreakerConfig()
    order_block_config: OrderBlockConfig = OrderBlockConfig()
    structure_config: StructureConfig = StructureConfig()

    @property
    def order_block_detector(self) -> OrderBlockDetector:
        return OrderBlockDetector(self.order_block_config)

    def _failure(self, work: pd.DataFrame, block: OrderBlock):
        """The bar that failed the block, under the configured break mode."""
        if self.config.break_mode is BreakerBreakMode.CLOSE:
            return first_close_beyond(
                work,
                level=block.far_edge,
                above=not block.is_bullish,
                start_timestamp=block.confirmation_timestamp,
            )

        # WICK — the naive reading, kept only so tests can exercise it.
        window = work[work["timestamp"] >= pd.Timestamp(block.confirmation_timestamp)]
        column = "low" if block.is_bullish else "high"
        series = window[column].to_numpy(dtype="float64")
        hits = series < block.far_edge if block.is_bullish else series > block.far_edge
        positions = hits.nonzero()[0]
        if len(positions) == 0:
            return None
        local = int(positions[0])
        return int(window.index[local]), window.iloc[local]

    def detect(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[BreakerBlock]:
        """Every Breaker whose failure (and structure condition) has occurred."""
        blocks = self.order_block_detector.detect(frame, symbol, timeframe)
        if not blocks:
            return []

        work = with_close_time(frame, timeframe).sort_values("timestamp", kind="mergesort")
        work = work.reset_index(drop=True)

        breaks = []
        if self.config.require_structure_break:
            breaks = StructureDetector(self.structure_config).analyse(frame, symbol, timeframe).breaks

        breakers: list[BreakerBlock] = []
        for block in blocks:
            failure = self._failure(work, block)
            if failure is None:
                continue

            failure_index, failure_row = failure
            flipped = Direction.BEARISH if block.is_bullish else Direction.BULLISH
            failure_close = failure_row["close_time"].to_pydatetime()

            source_break_id = None
            trigger = failure_close
            if self.config.require_structure_break:
                limit_index = min(failure_index + self.config.structure_window_bars, len(work) - 1)
                limit = work["close_time"].iloc[limit_index].to_pydatetime()
                match = next(
                    (
                        b
                        for b in breaks
                        if b.direction is flipped and confirmed_within(b, failure_close, limit)
                    ),
                    None,
                )
                if match is None:
                    # A block that failed without a structural confirmation is not a
                    # Breaker. Recorded in the analysis rather than silently dropped.
                    continue
                source_break_id = structure_break_id(match)
                trigger = match.confirmation_timestamp

            failure_timestamp = failure_row["timestamp"].to_pydatetime()
            confirmation = composite_confirmation([block.confirmation_timestamp], own_trigger=trigger)

            breakers.append(
                BreakerBlock(
                    # The source block is part of the identity: several Order Blocks
                    # can fail on the SAME bar in the same direction, and without it
                    # their ids collide.
                    breaker_id=(
                        f"breaker:{symbol.value}:{timeframe.value}:"
                        f"{flipped.value}:{failure_timestamp.isoformat()}:"
                        f"{block.order_block_id}"
                    ),
                    source_order_block_id=block.order_block_id,
                    symbol=symbol.value,
                    timeframe=timeframe.value,
                    original_direction=block.direction,
                    direction=flipped,
                    zone_top=block.zone_top,
                    zone_bottom=block.zone_bottom,
                    failure_timestamp=failure_timestamp,
                    event_timestamp=failure_timestamp,
                    confirmation_timestamp=confirmation,
                    source_order_block_confirmation=block.confirmation_timestamp,
                    source_break_id=source_break_id,
                )
            )

        breakers.sort(key=lambda b: (b.confirmation_timestamp, b.breaker_id))
        return breakers

    def analyse(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> BreakerAnalysis:
        breakers = self.detect(frame, symbol, timeframe)
        analysis = BreakerAnalysis(breakers=breakers)
        if len(frame) == 0:
            return analysis

        work = with_close_time(frame, timeframe).sort_values("timestamp", kind="mergesort")
        work = work.reset_index(drop=True)

        for breaker in breakers:
            updates = track_zone_fill(
                work,
                zone_id=breaker.breaker_id,
                top=breaker.zone_top,
                bottom=breaker.zone_bottom,
                direction=breaker.direction,
                start_timestamp=breaker.confirmation_timestamp,
                partial_threshold=self.config.partial_fill_threshold,
                full_threshold=self.config.full_fill_threshold,
            )
            analysis.fills.extend(updates)
            analysis.status[breaker.breaker_id] = updates[-1].status_after if updates else ZoneStatus.ACTIVE

        if self.config.require_structure_break:
            promoted = {b.source_order_block_id for b in breakers}
            ob_analysis = self.order_block_detector.analyse(frame, symbol, timeframe)
            analysis.failed_without_structure = [
                block_id for block_id in ob_analysis.invalidated_at if block_id not in promoted
            ]

        analysis.fills.sort(key=lambda u: (u.confirmation_timestamp, u.zone_id))
        return analysis

    def events(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[IctEvent]:
        analysis = self.analyse(frame, symbol, timeframe)
        events: list[IctEvent] = []

        for breaker in analysis.breakers:
            status = analysis.status.get(breaker.breaker_id, ZoneStatus.ACTIVE)
            events.append(
                IctEvent(
                    symbol=breaker.symbol,
                    timeframe=breaker.timeframe,
                    event_type=(
                        EventType.BREAKER_BULLISH if breaker.is_bullish else EventType.BREAKER_BEARISH
                    ),
                    direction=breaker.direction,
                    event_timestamp=breaker.event_timestamp,
                    confirmation_timestamp=breaker.confirmation_timestamp,
                    price_level=breaker.midpoint,
                    reference_level=breaker.far_edge,
                    created_timestamp=breaker.event_timestamp,
                    status=(EventStatus.MITIGATED if status is ZoneStatus.MITIGATED else EventStatus.ACTIVE),
                    metadata={
                        "breaker_id": breaker.breaker_id,
                        "source_order_block_id": breaker.source_order_block_id,
                        "source_break_id": breaker.source_break_id,
                        "original_direction": breaker.original_direction.value,
                        "zone_top": breaker.zone_top,
                        "zone_bottom": breaker.zone_bottom,
                        "failure_timestamp": breaker.failure_timestamp.isoformat(),
                        "lifecycle_status": status.value,
                        **self.config.as_dict(),
                    },
                )
            )

        events.sort(key=lambda e: (e.confirmation_timestamp, e.event_timestamp))
        return events

    def observable_at(
        self, frame: pd.DataFrame, as_of: datetime, symbol: Symbol, timeframe: Timeframe
    ) -> BreakerAnalysis:
        full = self.analyse(frame, symbol, timeframe)
        limited = BreakerAnalysis(
            breakers=filter_observable(full.breakers, as_of),
            fills=filter_observable(full.fills, as_of),
        )
        limited.status = {
            b.breaker_id: limited.status_at(b.breaker_id, as_of) or ZoneStatus.ACTIVE
            for b in limited.breakers
        }
        return limited

    def with_config(self, config: BreakerConfig) -> BreakerDetector:
        return replace(self, config=config)
