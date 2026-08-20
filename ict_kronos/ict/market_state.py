"""ICTMarketState — the point-in-time aggregation of every approved detector (R2-07).

Full semantics in ``docs/ict/market_state.md``. Read that first.

This module answers exactly one question:

    **What could a decision made at instant t have known about ICT structure?**

It is an aggregation, not a detector. It contains no pattern logic, no thresholds, no
geometry and no lifecycle rules of its own. Every value is either read from an approved
detector's own point-in-time API — ``active_at``, ``status_at``, ``state_at``,
``range_at``, ``latest_at``, ``session_state_at`` — or derived arithmetically from
values that were.

**There is no ``confirmation_timestamp <= as_of`` comparison anywhere in this file**,
and a source-level guard enforces it. The engine has one observability gate and R2-07
does not become the second.

Three rules carry the module:

* **Provenance is an id.** Every value that came from an event carries that event's id,
  never a price. Two events can share a price; the R2-05.2 audit found two real id
  collisions caused by exactly that shortcut.
* **"Latest" is a timestamp question**, resolved by ``(confirmation, event, id)`` —
  never by array position or dict iteration order.
* **0 and UNKNOWN are different.** Zero is a real distance. A count of 0 means "nothing
  active"; ``None`` means "no reference event exists". Never zero for missing.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import pandas as pd

from ..app.logging import get_logger
from ..data.resampler import with_close_time
from ..domain import Symbol, Timeframe
from .bpr import BprAnalysis, BprConfig, BprDetector
from .breakers import BreakerAnalysis, BreakerConfig, BreakerDetector
from .cisd import CisdAnalysis, CisdConfig, CisdDetector, DeliveryState
from .composites import ZoneStatus, structure_break_id, swing_point_id
from .contract import Direction, EventType, filter_observable
from .dealing_range import (
    DealingRangeAnalysis,
    DealingRangeConfig,
    DealingRangeDetector,
    RangeZone,
)
from .fvg import FvgAnalysis, FvgConfig, FvgDetector
from .ifvg import IfvgAnalysis, IfvgConfig, IfvgDetector
from .liquidity import (
    LiquidityAnalysis,
    LiquidityConfig,
    LiquidityDetector,
    LiquiditySide,
)
from .order_blocks import OrderBlockAnalysis, OrderBlockConfig, OrderBlockDetector
from .rdrb import RdrbAnalysis, RdrbConfig, RdrbDetector
from .sessions import SessionDetector, resolve_windows
from .structure import StructureAnalysis, StructureConfig, StructureDetector, StructureState
from .swings import SwingConfig, SwingDetector
from .true_daily_open import TrueDailyOpenConfig, TrueDailyOpenDetector
from .unicorn import UnicornAnalysis, UnicornConfig, UnicornDetector

logger = get_logger(__name__)

#: Bumped whenever the SHAPE or MEANING of the state changes. A dataset records it so
#: results can be tied to the definitions that produced them.
STATE_VERSION = "r2-07.1"


class MarketBias(StrEnum):
    """Aggregate directional read.

    ``NEUTRAL`` and ``UNKNOWN`` are **different answers**: NEUTRAL means the evidence
    was weighed and conflicts, UNKNOWN means there was no evidence to weigh. Collapsing
    them would hide the difference between a contested market and an unread one.
    """

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


def _points(distance: float, symbol: Symbol) -> float:
    """A price distance expressed in instrument points. The ONLY conversion here."""
    point = symbol.spec.point_value
    return float(distance / point) if point else float(distance)


def _latest(events: Sequence, id_field: str):
    """The last event by ``(confirmation, event, id)`` — never by array position.

    The id is the final tiebreaker so two events sharing both timestamps still order
    deterministically, which is what makes the state reproducible across runs.
    """
    if not events:
        return None
    return max(
        events,
        key=lambda e: (
            e.confirmation_timestamp,
            getattr(e, "event_timestamp", e.confirmation_timestamp),
            getattr(e, id_field, ""),
        ),
    )


@dataclass(frozen=True)
class ObservationBar:
    """The bar an observation is anchored to. Immutable, no detector internals."""

    symbol: str
    timeframe: str
    #: The bar's OPEN time — where it sits on the chart.
    timestamp: datetime
    #: The bar's close time, which IS the observation instant.
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "timestamp": self.timestamp.isoformat(),
            "close_time": self.close_time.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass(frozen=True)
class StructureContext:
    """R2-03. Nothing here re-derives a break; CHoCH uses the existing policy."""

    state: StructureState
    direction: Direction
    latest_break_id: str | None = None
    latest_break_type: EventType | None = None
    latest_break_direction: Direction | None = None
    latest_break_event_timestamp: datetime | None = None
    latest_break_confirmation: datetime | None = None
    latest_break_level: float | None = None
    latest_break_distance_points: float | None = None
    bars_since_break: int | None = None
    latest_bos_id: str | None = None
    latest_mss_id: str | None = None
    latest_choch_id: str | None = None
    bos_count: int = 0
    mss_count: int = 0
    choch_count: int = 0


@dataclass(frozen=True)
class LiquidityContext:
    """R2-04. A sweep never makes an unobservable level observable."""

    active_buy_side_ids: tuple[str, ...] = ()
    active_sell_side_ids: tuple[str, ...] = ()
    buy_side_count: int = 0
    sell_side_count: int = 0
    nearest_buy_side_id: str | None = None
    nearest_buy_side_price: float | None = None
    nearest_buy_side_points: float | None = None
    nearest_sell_side_id: str | None = None
    nearest_sell_side_price: float | None = None
    nearest_sell_side_points: float | None = None
    swept_level_ids: tuple[str, ...] = ()
    latest_sweep_level_id: str | None = None
    latest_sweep_side: LiquiditySide | None = None
    latest_sweep_confirmation: datetime | None = None
    latest_sweep_is_rejection: bool | None = None
    bars_since_sweep: int | None = None


@dataclass(frozen=True)
class ImbalanceContext:
    """R2-05 / R2-05.2 / R2-05.5. Mitigation is never reinterpreted as inversion."""

    active_bullish_fvg_ids: tuple[str, ...] = ()
    active_bearish_fvg_ids: tuple[str, ...] = ()
    bullish_fvg_count: int = 0
    bearish_fvg_count: int = 0
    latest_bullish_fvg_id: str | None = None
    latest_bearish_fvg_id: str | None = None
    nearest_bullish_fvg_id: str | None = None
    nearest_bullish_fvg_points: float | None = None
    nearest_bearish_fvg_id: str | None = None
    nearest_bearish_fvg_points: float | None = None
    active_ifvg_ids: tuple[str, ...] = ()
    ifvg_count: int = 0
    latest_ifvg_id: str | None = None
    latest_ifvg_direction: Direction | None = None
    active_bpr_ids: tuple[str, ...] = ()
    bpr_count: int = 0
    latest_bpr_id: str | None = None


@dataclass(frozen=True)
class InstitutionalContext:
    """R2-05.3 / R2-05.4. No new qualifier is introduced."""

    active_bullish_order_block_ids: tuple[str, ...] = ()
    active_bearish_order_block_ids: tuple[str, ...] = ()
    bullish_order_block_count: int = 0
    bearish_order_block_count: int = 0
    latest_bullish_order_block_id: str | None = None
    latest_bearish_order_block_id: str | None = None
    active_bullish_breaker_ids: tuple[str, ...] = ()
    active_bearish_breaker_ids: tuple[str, ...] = ()
    bullish_breaker_count: int = 0
    bearish_breaker_count: int = 0
    latest_breaker_id: str | None = None
    latest_breaker_direction: Direction | None = None
    latest_breaker_source_order_block_id: str | None = None


@dataclass(frozen=True)
class CompositeContext:
    """R2-05.6 / R2-05.7 / R2-05.9. Unicorn provenance is inherited whole."""

    active_rdrb_ids: tuple[str, ...] = ()
    rdrb_count: int = 0
    latest_rdrb_id: str | None = None
    latest_rdrb_direction: Direction | None = None
    delivery_state: DeliveryState = DeliveryState.UNDEFINED
    cisd_count: int = 0
    latest_cisd_id: str | None = None
    latest_cisd_direction: Direction | None = None
    bars_since_cisd: int | None = None
    active_unicorn_ids: tuple[str, ...] = ()
    unicorn_count: int = 0
    latest_unicorn_id: str | None = None
    latest_unicorn_direction: Direction | None = None
    #: Inherited provenance — ids only, never duplicated geometry.
    latest_unicorn_fvg_id: str | None = None
    latest_unicorn_breaker_id: str | None = None
    latest_unicorn_order_block_id: str | None = None


@dataclass(frozen=True)
class DailyOpenContext:
    """R2-05.1 — 00:00 America/New_York. No second DST implementation exists here."""

    level_id: str | None = None
    trading_date: str | None = None
    timezone: str | None = None
    price: float | None = None
    timestamp: datetime | None = None
    distance_points: float | None = None
    #: Whether the level belongs to the observation's own New York trading day.
    #: ``latest_at`` is "most recent", not "today's" — staleness is visible, not laundered.
    is_current_trading_day: bool | None = None


@dataclass(frozen=True)
class PremiumDiscountContext:
    """R2-06. ``percentage_position`` is carried through UNCLAMPED."""

    range_id: str | None = None
    high_anchor_id: str | None = None
    low_anchor_id: str | None = None
    source_break_id: str | None = None
    direction: Direction | None = None
    high_price: float | None = None
    low_price: float | None = None
    equilibrium_price: float | None = None
    width_points: float | None = None
    percentage_position: float | None = None
    distance_from_equilibrium_points: float | None = None
    zone: RangeZone | None = None


@dataclass(frozen=True)
class SessionContext:
    """R2-01. Reuses the existing session definitions and DST handling."""

    active_sessions: tuple[str, ...] = ()
    primary_session: str | None = None
    session_elapsed_minutes: float | None = None
    minute_of_session: int | None = None
    day_of_week: int = 0
    hour_of_day: int = 0
    #: Minutes since the observation's own True Daily Open, when one is observable.
    trading_day_age_minutes: float | None = None


@dataclass(frozen=True)
class BiasContext:
    """Evidence first, verdict second — and the evidence is always exposed.

    Counting, not scoring. A weight would be a hypothesis and this story does not test
    hypotheses; if the rule matters it becomes a Phase 4 ablation, not a knob here.
    """

    bullish_evidence: tuple[str, ...] = ()
    bearish_evidence: tuple[str, ...] = ()
    bullish_score: int = 0
    bearish_score: int = 0
    bias: MarketBias = MarketBias.UNKNOWN


@dataclass(frozen=True)
class ICTMarketState:
    """Everything observable about ICT structure at one instant. Immutable."""

    symbol: str
    timeframe: str
    #: The decision instant. Equal to the anchoring bar's ``close_time``.
    as_of: datetime
    bar: ObservationBar
    structure: StructureContext
    liquidity: LiquidityContext
    imbalance: ImbalanceContext
    institutional: InstitutionalContext
    composites: CompositeContext
    daily_open: DailyOpenContext
    premium_discount: PremiumDiscountContext
    session: SessionContext
    bias: BiasContext
    state_version: str = STATE_VERSION

    def as_dict(self) -> dict:
        """Deterministic serialisation: dataclass field order, enums by value,
        timestamps ISO-8601 UTC, ``None`` for missing — never zero."""
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "as_of": self.as_of.isoformat(),
            "state_version": self.state_version,
            "bar": self.bar.as_dict(),
            "structure": _section(self.structure),
            "liquidity": _section(self.liquidity),
            "imbalance": _section(self.imbalance),
            "institutional": _section(self.institutional),
            "composites": _section(self.composites),
            "daily_open": _section(self.daily_open),
            "premium_discount": _section(self.premium_discount),
            "session": _section(self.session),
            "bias": _section(self.bias),
        }

    def source_ids(self) -> dict[str, tuple[str, ...]]:
        """Every provenance id this state emits, grouped by originating detector.

        Exists so ``assert_provenance_resolves`` can be pointed at a whole state rather
        than at hand-listed fields — a list that would rot the first time a field is
        added.
        """
        return {
            "structure": _ids(
                self.structure.latest_break_id,
                self.structure.latest_bos_id,
                self.structure.latest_mss_id,
                self.structure.latest_choch_id,
                # The dealing range's originating break is a STRUCTURE id and resolves
                # against the same registry. Omitting it left one emitted id outside
                # every provenance check -- exactly the rot this method exists to stop.
                self.premium_discount.source_break_id,
            ),
            "liquidity_level": _ids(
                *self.liquidity.active_buy_side_ids,
                *self.liquidity.active_sell_side_ids,
                *self.liquidity.swept_level_ids,
                self.liquidity.nearest_buy_side_id,
                self.liquidity.nearest_sell_side_id,
                self.liquidity.latest_sweep_level_id,
            ),
            "fvg": _ids(
                *self.imbalance.active_bullish_fvg_ids,
                *self.imbalance.active_bearish_fvg_ids,
                self.imbalance.latest_bullish_fvg_id,
                self.imbalance.latest_bearish_fvg_id,
                self.imbalance.nearest_bullish_fvg_id,
                self.imbalance.nearest_bearish_fvg_id,
                self.composites.latest_unicorn_fvg_id,
            ),
            "ifvg": _ids(*self.imbalance.active_ifvg_ids, self.imbalance.latest_ifvg_id),
            "bpr": _ids(*self.imbalance.active_bpr_ids, self.imbalance.latest_bpr_id),
            "order_block": _ids(
                *self.institutional.active_bullish_order_block_ids,
                *self.institutional.active_bearish_order_block_ids,
                self.institutional.latest_bullish_order_block_id,
                self.institutional.latest_bearish_order_block_id,
                self.institutional.latest_breaker_source_order_block_id,
                self.composites.latest_unicorn_order_block_id,
            ),
            "breaker": _ids(
                *self.institutional.active_bullish_breaker_ids,
                *self.institutional.active_bearish_breaker_ids,
                self.institutional.latest_breaker_id,
                self.composites.latest_unicorn_breaker_id,
            ),
            "rdrb": _ids(*self.composites.active_rdrb_ids, self.composites.latest_rdrb_id),
            "cisd": _ids(self.composites.latest_cisd_id),
            "unicorn": _ids(*self.composites.active_unicorn_ids, self.composites.latest_unicorn_id),
            "daily_open": _ids(self.daily_open.level_id),
            "dealing_range": _ids(self.premium_discount.range_id),
            "swing": _ids(self.premium_discount.high_anchor_id, self.premium_discount.low_anchor_id),
        }


def _ids(*values: str | None) -> tuple[str, ...]:
    """Deduplicated, sorted, ``None`` dropped — deterministic regardless of input order."""
    return tuple(sorted({v for v in values if v is not None}))


def _section(item) -> dict:
    """One frozen context as a plain dict, in dataclass field order."""
    out: dict = {}
    for spec in fields(item):
        value = getattr(item, spec.name)
        if isinstance(value, StrEnum):
            out[spec.name] = value.value
        elif isinstance(value, datetime):
            out[spec.name] = value.isoformat()
        elif isinstance(value, tuple):
            out[spec.name] = list(value)
        else:
            out[spec.name] = value
    return out


@dataclass(frozen=True)
class MarketStateConfig:
    """R2-07's own settings. Deliberately tiny — no detector semantics live here.

    A knob that changed what a detector means would make R2-07 a second place where
    ICT is defined, which is exactly what this layer exists not to be.
    """

    #: Include the per-bar dealing-range classification stream. Off skips R2-06's
    #: per-bar pass when only the ranges themselves are needed.
    classify_bars: bool = True

    def as_dict(self) -> dict:
        return {"classify_bars": self.classify_bars}


@dataclass
class ICTEngineView:
    """Every approved detector's analysis over one frame, computed once.

    Building a state calls a dozen point-in-time APIs; recomputing the analyses for
    each instant would be quadratic for no reason. The analyses are pure functions of
    the frame, so caching them changes nothing about what any state can see —
    observability is still decided per call, by ``as_of``.
    """

    symbol: Symbol
    timeframe: Timeframe
    frame: pd.DataFrame
    work: pd.DataFrame
    structure: StructureAnalysis
    liquidity: LiquidityAnalysis
    fvg: FvgAnalysis
    ifvg: IfvgAnalysis
    order_blocks: OrderBlockAnalysis
    breakers: BreakerAnalysis
    bpr: BprAnalysis
    rdrb: RdrbAnalysis
    cisd: CisdAnalysis
    unicorn: UnicornAnalysis
    dealing_range: DealingRangeAnalysis
    sessions: SessionDetector
    daily_open: TrueDailyOpenDetector
    #: Computed ONCE. ``latest_at`` would re-run detection for every instant, which
    #: would make this class quadratic in exactly the way its docstring says it is not.
    daily_open_levels: list = field(default_factory=list)
    #: Session windows spanning the frame, resolved once. Containment is then pure
    #: interval arithmetic — no second DST implementation, and no per-instant re-resolve.
    session_windows: list = field(default_factory=list)
    config: MarketStateConfig = field(default_factory=MarketStateConfig)

    # -------------------------------------------------------------- helpers

    def _bar_index(self, as_of: datetime) -> int | None:
        """Positional index of the bar whose close IS ``as_of``, if there is one."""
        matches = (self.work["close_time"] == pd.Timestamp(as_of)).to_numpy().nonzero()[0]
        return int(matches[0]) if len(matches) else None

    def _bars_since(self, moment: datetime | None, as_of: datetime) -> int | None:
        """Bars between two instants, counted in BARS rather than elapsed time.

        Across a weekend an elapsed-time measure would imply activity that did not
        occur; bars are what the detectors index.
        """
        if moment is None:
            return None
        closes = self.work["close_time"]
        after = (closes > pd.Timestamp(moment)) & (closes <= pd.Timestamp(as_of))
        return int(after.sum())

    def _bar_at(self, as_of: datetime) -> ObservationBar | None:
        index = self._bar_index(as_of)
        if index is None:
            return None
        row = self.work.iloc[index]
        volume = row.get("volume")
        return ObservationBar(
            symbol=self.symbol.value,
            timeframe=self.timeframe.value,
            timestamp=row["timestamp"].to_pydatetime(),
            close_time=row["close_time"].to_pydatetime(),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=None if volume is None or pd.isna(volume) else float(volume),
        )

    # -------------------------------------------------------------- sections

    def _structure_at(self, as_of: datetime) -> StructureContext:
        breaks = filter_observable(self.structure.breaks, as_of)
        state = self.structure.state_at(as_of)
        direction = {
            StructureState.BULLISH: Direction.BULLISH,
            StructureState.BEARISH: Direction.BEARISH,
        }.get(state, Direction.NEUTRAL)

        if not breaks:
            return StructureContext(state=state, direction=direction)

        latest = _latest(breaks, "reference_swing_timestamp")
        kinds = (EventType.BOS, EventType.MSS, EventType.CHOCH)
        by_type = {t: [b for b in breaks if b.event_type is t] for t in kinds}
        latest_of = {t: _latest(v, "reference_swing_timestamp") for t, v in by_type.items()}

        def break_id(kind):
            found = latest_of[kind]
            return structure_break_id(found) if found else None

        return StructureContext(
            state=state,
            direction=direction,
            latest_break_id=structure_break_id(latest),
            latest_break_type=latest.event_type,
            latest_break_direction=latest.direction,
            latest_break_event_timestamp=latest.event_timestamp,
            latest_break_confirmation=latest.confirmation_timestamp,
            latest_break_level=latest.reference_level,
            latest_break_distance_points=latest.break_distance_points,
            bars_since_break=self._bars_since(latest.confirmation_timestamp, as_of),
            latest_bos_id=break_id(EventType.BOS),
            latest_mss_id=break_id(EventType.MSS),
            latest_choch_id=break_id(EventType.CHOCH),
            bos_count=len(by_type[EventType.BOS]),
            mss_count=len(by_type[EventType.MSS]),
            choch_count=len(by_type[EventType.CHOCH]),
        )

    def _liquidity_at(self, as_of: datetime, price: float) -> LiquidityContext:
        active = self.liquidity.active_at(as_of)
        sweeps = self.liquidity.swept_by(as_of)

        buy_side = [x for x in active if x.side is LiquiditySide.BUY_SIDE]
        sell_side = [x for x in active if x.side is LiquiditySide.SELL_SIDE]

        def nearest(levels):
            if not levels:
                return None, None, None
            best = min(levels, key=lambda x: (abs(x.price_level - price), x.level_id))
            return best.level_id, best.price_level, _points(abs(best.price_level - price), self.symbol)

        buy_id, buy_price, buy_points = nearest(buy_side)
        sell_id, sell_price, sell_points = nearest(sell_side)
        latest_sweep = _latest(sweeps, "level_id")

        return LiquidityContext(
            active_buy_side_ids=tuple(sorted(x.level_id for x in buy_side)),
            active_sell_side_ids=tuple(sorted(x.level_id for x in sell_side)),
            buy_side_count=len(buy_side),
            sell_side_count=len(sell_side),
            nearest_buy_side_id=buy_id,
            nearest_buy_side_price=buy_price,
            nearest_buy_side_points=buy_points,
            nearest_sell_side_id=sell_id,
            nearest_sell_side_price=sell_price,
            nearest_sell_side_points=sell_points,
            swept_level_ids=tuple(sorted({s.level_id for s in sweeps})),
            latest_sweep_level_id=latest_sweep.level_id if latest_sweep else None,
            latest_sweep_side=latest_sweep.side if latest_sweep else None,
            latest_sweep_confirmation=latest_sweep.confirmation_timestamp if latest_sweep else None,
            latest_sweep_is_rejection=latest_sweep.is_rejection if latest_sweep else None,
            bars_since_sweep=(
                self._bars_since(latest_sweep.confirmation_timestamp, as_of) if latest_sweep else None
            ),
        )

    def _imbalance_at(self, as_of: datetime, price: float) -> ImbalanceContext:
        zones = self.fvg.active_at(as_of)
        bullish = [z for z in zones if z.is_bullish]
        bearish = [z for z in zones if not z.is_bullish]
        inverted = self.ifvg.active_at(as_of)
        ranges = [
            r
            for r in filter_observable(self.bpr.ranges, as_of)
            if self.bpr.status_at(r.bpr_id, as_of) is not ZoneStatus.MITIGATED
        ]

        def nearest(items):
            if not items:
                return None, None
            best = min(items, key=lambda z: (abs(z.midpoint - price), z.zone_id))
            return best.zone_id, _points(abs(best.midpoint - price), self.symbol)

        near_bull_id, near_bull_points = nearest(bullish)
        near_bear_id, near_bear_points = nearest(bearish)
        latest_bull = _latest(bullish, "zone_id")
        latest_bear = _latest(bearish, "zone_id")
        latest_ifvg = _latest(inverted, "ifvg_id")
        latest_bpr = _latest(ranges, "bpr_id")

        return ImbalanceContext(
            active_bullish_fvg_ids=tuple(sorted(z.zone_id for z in bullish)),
            active_bearish_fvg_ids=tuple(sorted(z.zone_id for z in bearish)),
            bullish_fvg_count=len(bullish),
            bearish_fvg_count=len(bearish),
            latest_bullish_fvg_id=latest_bull.zone_id if latest_bull else None,
            latest_bearish_fvg_id=latest_bear.zone_id if latest_bear else None,
            nearest_bullish_fvg_id=near_bull_id,
            nearest_bullish_fvg_points=near_bull_points,
            nearest_bearish_fvg_id=near_bear_id,
            nearest_bearish_fvg_points=near_bear_points,
            active_ifvg_ids=tuple(sorted(z.ifvg_id for z in inverted)),
            ifvg_count=len(inverted),
            latest_ifvg_id=latest_ifvg.ifvg_id if latest_ifvg else None,
            latest_ifvg_direction=latest_ifvg.direction if latest_ifvg else None,
            active_bpr_ids=tuple(sorted(r.bpr_id for r in ranges)),
            bpr_count=len(ranges),
            latest_bpr_id=latest_bpr.bpr_id if latest_bpr else None,
        )

    def _institutional_at(self, as_of: datetime) -> InstitutionalContext:
        blocks = self.order_blocks.active_at(as_of)
        bullish_ob = [b for b in blocks if b.direction is Direction.BULLISH]
        bearish_ob = [b for b in blocks if b.direction is Direction.BEARISH]

        live_breakers = [
            b
            for b in filter_observable(self.breakers.breakers, as_of)
            if self.breakers.status_at(b.breaker_id, as_of) is not ZoneStatus.MITIGATED
        ]
        bullish_bk = [b for b in live_breakers if b.direction is Direction.BULLISH]
        bearish_bk = [b for b in live_breakers if b.direction is Direction.BEARISH]

        latest_bull_ob = _latest(bullish_ob, "order_block_id")
        latest_bear_ob = _latest(bearish_ob, "order_block_id")
        latest_bk = _latest(live_breakers, "breaker_id")

        return InstitutionalContext(
            active_bullish_order_block_ids=tuple(sorted(b.order_block_id for b in bullish_ob)),
            active_bearish_order_block_ids=tuple(sorted(b.order_block_id for b in bearish_ob)),
            bullish_order_block_count=len(bullish_ob),
            bearish_order_block_count=len(bearish_ob),
            latest_bullish_order_block_id=latest_bull_ob.order_block_id if latest_bull_ob else None,
            latest_bearish_order_block_id=latest_bear_ob.order_block_id if latest_bear_ob else None,
            active_bullish_breaker_ids=tuple(sorted(b.breaker_id for b in bullish_bk)),
            active_bearish_breaker_ids=tuple(sorted(b.breaker_id for b in bearish_bk)),
            bullish_breaker_count=len(bullish_bk),
            bearish_breaker_count=len(bearish_bk),
            latest_breaker_id=latest_bk.breaker_id if latest_bk else None,
            latest_breaker_direction=latest_bk.direction if latest_bk else None,
            latest_breaker_source_order_block_id=(latest_bk.source_order_block_id if latest_bk else None),
        )

    def _composites_at(self, as_of: datetime) -> CompositeContext:
        zones = [
            z
            for z in filter_observable(self.rdrb.zones, as_of)
            if self.rdrb.status_at(z.rdrb_id, as_of) is not ZoneStatus.MITIGATED
        ]
        cisds = filter_observable(self.cisd.transitions, as_of)
        unicorns = self.unicorn.active_at(as_of)

        latest_rdrb = _latest(zones, "rdrb_id")
        latest_cisd = _latest(cisds, "cisd_id")
        latest_unicorn = _latest(unicorns, "unicorn_id")

        return CompositeContext(
            active_rdrb_ids=tuple(sorted(z.rdrb_id for z in zones)),
            rdrb_count=len(zones),
            latest_rdrb_id=latest_rdrb.rdrb_id if latest_rdrb else None,
            latest_rdrb_direction=latest_rdrb.direction if latest_rdrb else None,
            delivery_state=self.cisd.state_at(as_of),
            cisd_count=len(cisds),
            latest_cisd_id=latest_cisd.cisd_id if latest_cisd else None,
            latest_cisd_direction=latest_cisd.direction if latest_cisd else None,
            bars_since_cisd=(
                self._bars_since(latest_cisd.confirmation_timestamp, as_of) if latest_cisd else None
            ),
            active_unicorn_ids=tuple(sorted(u.unicorn_id for u in unicorns)),
            unicorn_count=len(unicorns),
            latest_unicorn_id=latest_unicorn.unicorn_id if latest_unicorn else None,
            latest_unicorn_direction=latest_unicorn.direction if latest_unicorn else None,
            latest_unicorn_fvg_id=latest_unicorn.source_fvg_id if latest_unicorn else None,
            latest_unicorn_breaker_id=latest_unicorn.source_breaker_id if latest_unicorn else None,
            latest_unicorn_order_block_id=(latest_unicorn.source_order_block_id if latest_unicorn else None),
        )

    def _daily_open_at(self, as_of: datetime, price: float) -> DailyOpenContext:
        visible = filter_observable(self.daily_open_levels, as_of)
        if not visible:
            return DailyOpenContext()
        level = visible[-1]

        # ``latest_at`` is "most recent", not "today's". The observation's own New York
        # trading date comes from the DETECTOR'S configured zone -- no zone is defined
        # here, so this is a conversion at the point of use, not a second DST rule.
        local_date = as_of.astimezone(self.daily_open.config.zone).date()
        return DailyOpenContext(
            level_id=level.level_id,
            trading_date=level.trading_date.isoformat(),
            timezone=level.timezone,
            price=level.price_level,
            timestamp=level.event_timestamp,
            distance_points=_points(price - level.price_level, self.symbol),
            is_current_trading_day=(level.trading_date == local_date),
        )

    def _premium_discount_at(self, as_of: datetime, price: float) -> PremiumDiscountContext:
        item = self.dealing_range.range_at(as_of)
        if item is None:
            return PremiumDiscountContext()

        # R2-06 returns NaN for a degenerate (zero-width) range -- ITS sentinel for
        # "position is undefined". This layer's sentinel for a value that cannot exist
        # is ``None`` (docs §7); NaN belongs to ``as_row`` alone. Translating here keeps
        # one missing-value convention instead of two, and keeps record equality
        # meaningful -- NaN is not equal to itself, so a state carrying one would fail
        # both the serialisation round-trip and the batch/prefix comparison.
        position = item.position_of(price)
        return PremiumDiscountContext(
            range_id=item.range_id,
            high_anchor_id=item.high_source_id,
            low_anchor_id=item.low_source_id,
            source_break_id=item.source_break_id,
            direction=item.direction,
            high_price=item.high_price,
            low_price=item.low_price,
            equilibrium_price=item.equilibrium_price,
            width_points=_points(item.width, self.symbol),
            # Carried through UNCLAMPED: outside [0, 1] is the common case, because
            # R2-06 anchors on the BROKEN structural level (dealing_range.md §11).
            percentage_position=None if math.isnan(position) else position,
            distance_from_equilibrium_points=_points(price - item.equilibrium_price, self.symbol),
            zone=self._zone_of(item, price),
        )

    def _zone_of(self, item, price: float) -> RangeZone:
        """R2-06 owns the rule; this only supplies the price."""
        return DealingRangeDetector(
            config=DealingRangeConfig(classify_bars=self.config.classify_bars)
        ).zone_of(item, price, self.symbol)

    def _session_at(self, as_of: datetime, daily_open: DailyOpenContext) -> SessionContext:
        live = [w for w in self.session_windows if w.contains(as_of)]
        # Sorted by name so the "primary" pick is deterministic when windows overlap;
        # overlap is real (London and New York share hours) and is NOT collapsed --
        # every active window is reported in ``active_sessions``.
        live.sort(key=lambda w: (w.name, w.start_utc))
        active = tuple(w.name for w in live)
        primary = active[0] if active else None

        elapsed = minute_of = None
        if live:
            delta = (as_of - live[0].start_utc).total_seconds() / 60.0
            elapsed = float(delta)
            minute_of = int(delta)

        age = None
        if daily_open.timestamp is not None:
            age = (as_of - daily_open.timestamp).total_seconds() / 60.0

        return SessionContext(
            active_sessions=active,
            primary_session=primary,
            session_elapsed_minutes=elapsed,
            minute_of_session=minute_of,
            day_of_week=as_of.weekday(),
            hour_of_day=as_of.hour,
            trading_day_age_minutes=age,
        )

    @staticmethod
    def _bias_from(
        structure: StructureContext,
        composites: CompositeContext,
        premium_discount: PremiumDiscountContext,
        liquidity: LiquidityContext,
    ) -> BiasContext:
        """Counting, not scoring. Four sources, at most one item each.

        Conflicting evidence yields NEUTRAL and absent evidence yields UNKNOWN — two
        different answers, and neither is a direction forced out of nothing.
        """
        bullish: list[str] = []
        bearish: list[str] = []

        if structure.direction is Direction.BULLISH:
            bullish.append("structure_bullish")
        elif structure.direction is Direction.BEARISH:
            bearish.append("structure_bearish")

        if composites.delivery_state is DeliveryState.BULLISH:
            bullish.append("delivery_bullish")
        elif composites.delivery_state is DeliveryState.BEARISH:
            bearish.append("delivery_bearish")

        # ICT describes buying in discount and selling in premium. Treating that as
        # directional EVIDENCE is this layer's reading, and it is one input of four.
        if premium_discount.zone is RangeZone.DISCOUNT:
            bullish.append("price_in_discount")
        elif premium_discount.zone is RangeZone.PREMIUM:
            bearish.append("price_in_premium")

        if liquidity.latest_sweep_side is LiquiditySide.SELL_SIDE:
            bullish.append("sell_side_liquidity_taken")
        elif liquidity.latest_sweep_side is LiquiditySide.BUY_SIDE:
            bearish.append("buy_side_liquidity_taken")

        if not bullish and not bearish:
            bias = MarketBias.UNKNOWN
        elif len(bullish) > len(bearish):
            bias = MarketBias.BULLISH
        elif len(bearish) > len(bullish):
            bias = MarketBias.BEARISH
        else:
            bias = MarketBias.NEUTRAL

        return BiasContext(
            bullish_evidence=tuple(bullish),
            bearish_evidence=tuple(bearish),
            bullish_score=len(bullish),
            bearish_score=len(bearish),
            bias=bias,
        )

    # -------------------------------------------------------------- assembly

    def state_at(self, as_of: datetime) -> ICTMarketState | None:
        """The state a decision at ``as_of`` may use, or ``None`` off a bar close.

        ``None`` rather than an interpolated state: an observation is anchored to a
        bar whose close is knowable, and inventing one between closes would be
        inventing a price.
        """
        bar = self._bar_at(as_of)
        if bar is None:
            return None

        price = bar.close
        structure = self._structure_at(as_of)
        liquidity = self._liquidity_at(as_of, price)
        imbalance = self._imbalance_at(as_of, price)
        institutional = self._institutional_at(as_of)
        composites = self._composites_at(as_of)
        daily_open = self._daily_open_at(as_of, price)
        premium_discount = self._premium_discount_at(as_of, price)
        session = self._session_at(as_of, daily_open)
        bias = self._bias_from(structure, composites, premium_discount, liquidity)

        return ICTMarketState(
            symbol=self.symbol.value,
            timeframe=self.timeframe.value,
            as_of=as_of,
            bar=bar,
            structure=structure,
            liquidity=liquidity,
            imbalance=imbalance,
            institutional=institutional,
            composites=composites,
            daily_open=daily_open,
            premium_discount=premium_discount,
            session=session,
            bias=bias,
        )

    def observation_instants(self) -> list[datetime]:
        """Every bar close in the frame, ascending — the instants a state can exist at."""
        return [t.to_pydatetime() for t in self.work["close_time"]]

    def states(self, instants: Sequence[datetime] | None = None) -> list[ICTMarketState]:
        """One state per bar close, or per supplied instant.

        ``instants`` exists because a state costs a dozen point-in-time queries; on a
        multi-thousand-bar 1m frame a caller may legitimately want a sample rather than
        every bar. Sampling changes nothing about any individual state.
        """
        moments = list(instants) if instants is not None else self.observation_instants()
        out = []
        for moment in moments:
            state = self.state_at(moment)
            if state is not None:
                out.append(state)
        return out


@dataclass
class MarketStateBuilder:
    """Builds :class:`ICTEngineView` by running every approved detector once.

    Every detector config is injectable so a caller can reproduce a dataset exactly,
    but **nothing here changes detector semantics** — the defaults are the approved
    defaults and this class adds no rule of its own.
    """

    config: MarketStateConfig = MarketStateConfig()
    swing_config: SwingConfig = SwingConfig()
    structure_config: StructureConfig = StructureConfig()
    liquidity_config: LiquidityConfig = LiquidityConfig()
    fvg_config: FvgConfig = FvgConfig()
    ifvg_config: IfvgConfig = IfvgConfig()
    order_block_config: OrderBlockConfig = OrderBlockConfig()
    breaker_config: BreakerConfig = BreakerConfig()
    bpr_config: BprConfig = BprConfig()
    rdrb_config: RdrbConfig = RdrbConfig()
    cisd_config: CisdConfig = CisdConfig()
    unicorn_config: UnicornConfig = UnicornConfig()
    dealing_range_config: DealingRangeConfig = DealingRangeConfig()
    true_daily_open_config: TrueDailyOpenConfig = TrueDailyOpenConfig()
    session_detector: SessionDetector = field(default_factory=SessionDetector)

    def analyse(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> ICTEngineView:
        work = with_close_time(frame, timeframe).sort_values("timestamp", kind="mergesort")
        work = work.reset_index(drop=True)

        range_config = replace(self.dealing_range_config, classify_bars=self.config.classify_bars)
        daily_open_detector = TrueDailyOpenDetector(self.true_daily_open_config)
        span_start = work["timestamp"].iloc[0].to_pydatetime() if len(work) else datetime.now(UTC)
        span_end = work["close_time"].iloc[-1].to_pydatetime() if len(work) else span_start

        return ICTEngineView(
            symbol=symbol,
            timeframe=timeframe,
            frame=frame,
            work=work,
            structure=StructureDetector(self.structure_config, self.swing_config).analyse(
                frame, symbol, timeframe
            ),
            liquidity=LiquidityDetector(
                self.liquidity_config, self.swing_config, self.session_detector
            ).analyse(frame, symbol, timeframe),
            fvg=FvgDetector(self.fvg_config).analyse(frame, symbol, timeframe),
            ifvg=IfvgDetector(self.ifvg_config, self.fvg_config).analyse(frame, symbol, timeframe),
            order_blocks=OrderBlockDetector(self.order_block_config, self.fvg_config).analyse(
                frame, symbol, timeframe
            ),
            breakers=BreakerDetector(
                self.breaker_config, self.order_block_config, self.structure_config
            ).analyse(frame, symbol, timeframe),
            bpr=BprDetector(self.bpr_config, self.fvg_config).analyse(frame, symbol, timeframe),
            rdrb=RdrbDetector(self.rdrb_config).analyse(frame, symbol, timeframe),
            cisd=CisdDetector(self.cisd_config).analyse(frame, symbol, timeframe),
            unicorn=UnicornDetector(
                self.unicorn_config,
                self.breaker_config,
                self.fvg_config,
                self.order_block_config,
                self.structure_config,
            ).analyse(frame, symbol, timeframe),
            dealing_range=DealingRangeDetector(
                range_config, self.swing_config, self.structure_config
            ).analyse(frame, symbol, timeframe),
            sessions=self.session_detector,
            daily_open=daily_open_detector,
            daily_open_levels=daily_open_detector.detect(frame, symbol, timeframe),
            session_windows=list(
                resolve_windows(
                    self.session_detector.definitions,
                    span_start - timedelta(days=2),
                    span_end + timedelta(days=1),
                )
            ),
            config=self.config,
        )

    def state_at(
        self, frame: pd.DataFrame, as_of: datetime, symbol: Symbol, timeframe: Timeframe
    ) -> ICTMarketState | None:
        return self.analyse(frame, symbol, timeframe).state_at(as_of)

    def states(
        self,
        frame: pd.DataFrame,
        symbol: Symbol,
        timeframe: Timeframe,
        instants: Sequence[datetime] | None = None,
    ) -> list[ICTMarketState]:
        return self.analyse(frame, symbol, timeframe).states(instants)

    def with_config(self, config: MarketStateConfig) -> MarketStateBuilder:
        return replace(self, config=config)


def swing_registry(frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe, config: SwingConfig):
    """``swing_point_id -> SwingPoint``, for resolving dealing-range anchor provenance.

    Lives here rather than in a test so the resolution path is part of the shipped
    contract instead of an assertion someone has to remember to write.
    """
    return {swing_point_id(s): s for s in SwingDetector(config).detect(frame, symbol, timeframe)}
