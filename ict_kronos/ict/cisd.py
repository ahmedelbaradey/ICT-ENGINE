"""CISD — Change In State of Delivery. An early, close-based delivery-state signal.

Full semantics in ``docs/ict/cisd.md``. Read that first.

**The rule.** Price is modelled as being *delivered* in one direction at a time. A run
of consecutive same-direction candles is one delivery leg. Delivery changes state when
a candle **body closes** through the **opening price of that leg**:

    bullish CISD   a close ABOVE the opening price of the preceding bearish leg
    bearish CISD   a close BELOW the opening price of the preceding bullish leg

**Ignore the wicks.** Only opens and closes matter. A wick through the trigger level is
not a CISD; only a body close is. This is the single most important rule in the module
and it is why CISD cannot be derived from anything wick-based.

**CISD is not MSS, and this module does not consume R2-03.** They answer different
questions with different inputs:

===============  ====================  ==============================
                 reads                 confirms
===============  ====================  ==============================
CISD             opens and closes      earlier — on a candle close
BOS / MSS        swing levels          later — on a structural break
===============  ====================  ==============================

Both may fire on the same bar; neither implies the other. A source-level test asserts
this module never imports ``structure.py``, because deriving CISD from structure would
silently collapse the distinction the concept exists to make.

**Everything used is available at the signal candle's close.** The delivery leg is
built only from candles at or before the trigger, never from future pivots, future
breaks or future gaps, and CISD is never classified retroactively.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

import pandas as pd

from ..app.logging import get_logger
from ..data.resampler import with_close_time
from ..domain import Symbol, Timeframe
from .composites import composite_confirmation
from .contract import (
    Direction,
    EventType,
    IctEvent,
    filter_observable,
    is_observable_at,
)

logger = get_logger(__name__)


class DeliveryState(StrEnum):
    """Which side is currently delivering price."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    UNDEFINED = "undefined"


class CisdAnchor(StrEnum):
    """Which open of the delivery leg is the trigger level.

    ``SERIES_OPEN`` (default) — the open of the leg's **first** candle: the price at
    which that delivery series opened. This is the reading the source wording supports
    and it is knowable the moment the leg begins.

    ``EXTREME_OPEN`` — the highest open in a bearish leg (lowest in a bullish one).
    Configurable, not default: it can only be identified by scanning the whole leg,
    which invites reasoning about the leg with hindsight.
    """

    SERIES_OPEN = "series_open"
    EXTREME_OPEN = "extreme_open"


@dataclass(frozen=True)
class CisdConfig:
    """CISD parameters. Configuration, never literals in the detector."""

    anchor: CisdAnchor = CisdAnchor.SERIES_OPEN
    #: Minimum candles in a delivery leg. 1 is legal by definition — a single
    #: down-close candle is a (short) bearish delivery leg — and is the default, but
    #: raising it is how a caller suppresses noise on fast timeframes.
    min_leg_length: int = 1
    #: The close must exceed the trigger level by MORE than this many instrument
    #: points. 0 keeps a strict comparison; equality is never a transition.
    trigger_tolerance_points: float = 0.0
    #: Candles in a leg must be contiguous in time.
    require_contiguous_bars: bool = True
    #: How far past the leg the transition may occur before the leg is abandoned.
    max_bars_to_trigger: int = 50

    def __post_init__(self) -> None:
        if self.min_leg_length < 1:
            raise ValueError(f"min_leg_length must be >= 1; got {self.min_leg_length}")
        if self.trigger_tolerance_points < 0:
            raise ValueError(f"trigger_tolerance_points must be >= 0; got {self.trigger_tolerance_points}")
        if self.max_bars_to_trigger < 1:
            raise ValueError(f"max_bars_to_trigger must be >= 1; got {self.max_bars_to_trigger}")

    def as_dict(self) -> dict:
        return {
            "anchor": self.anchor.value,
            "min_leg_length": self.min_leg_length,
            "trigger_tolerance_points": self.trigger_tolerance_points,
            "require_contiguous_bars": self.require_contiguous_bars,
            "max_bars_to_trigger": self.max_bars_to_trigger,
        }


@dataclass(frozen=True)
class Cisd:
    """One confirmed change in the state of delivery. Immutable."""

    cisd_id: str
    symbol: str
    timeframe: str
    direction: Direction
    #: The delivery leg's opening price — the level a body close had to clear.
    trigger_level: float
    #: The close that cleared it.
    trigger_close: float
    #: Provenance: the delivery leg this transition acted against.
    leg_start_timestamp: datetime
    leg_end_timestamp: datetime
    leg_length: int
    #: The crossing bar's open.
    event_timestamp: datetime
    #: That bar's close_time. The rule reads a close, so this is one bar of lag.
    confirmation_timestamp: datetime
    previous_state: DeliveryState
    resulting_state: DeliveryState

    def is_observable_at(self, as_of: datetime) -> bool:
        """Delegates to the ONE contract-level predicate — never a private copy."""
        return is_observable_at(self, as_of)

    @property
    def is_bullish(self) -> bool:
        return self.direction is Direction.BULLISH

    def as_dict(self) -> dict:
        return {
            "cisd_id": self.cisd_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction.value,
            "trigger_level": self.trigger_level,
            "trigger_close": self.trigger_close,
            "leg_start_timestamp": self.leg_start_timestamp.isoformat(),
            "leg_end_timestamp": self.leg_end_timestamp.isoformat(),
            "leg_length": self.leg_length,
            "event_timestamp": self.event_timestamp.isoformat(),
            "confirmation_timestamp": self.confirmation_timestamp.isoformat(),
            "previous_state": self.previous_state.value,
            "resulting_state": self.resulting_state.value,
        }


@dataclass
class CisdAnalysis:
    """Transitions plus the delivery-state timeline they imply."""

    transitions: list[Cisd] = field(default_factory=list)

    def state_at(self, as_of: datetime) -> DeliveryState:
        """Delivery state as known at ``as_of``.

        Derived from observable transitions only — never from a transition confirmed
        after ``as_of``. Records themselves are immutable; a later CISD supersedes an
        earlier state without rewriting anything.
        """
        seen = [t for t in self.transitions if is_observable_at(t, as_of)]
        return seen[-1].resulting_state if seen else DeliveryState.UNDEFINED

    def transition_by_id(self, cisd_id: str) -> Cisd | None:
        return next((t for t in self.transitions if t.cisd_id == cisd_id), None)


@dataclass
class CisdDetector:
    """Deterministic delivery-state transitions. Independent of R2-03 structure."""

    config: CisdConfig = CisdConfig()

    def _legs(self, work: pd.DataFrame) -> list[tuple[int, int, Direction]]:
        """Maximal contiguous runs of same-close-direction candles.

        A doji (``close == open``) belongs to no leg and terminates the one in
        progress — stated explicitly so "consecutive series" is not a judgement call.
        """
        opens = work["open"].to_numpy(dtype="float64")
        closes = work["close"].to_numpy(dtype="float64")
        stamps = work["timestamp"].to_numpy()
        close_times = work["close_time"].to_numpy()

        legs: list[tuple[int, int, Direction]] = []
        start: int | None = None
        current: Direction | None = None

        for i in range(len(work)):
            if closes[i] > opens[i]:
                kind: Direction | None = Direction.BULLISH
            elif closes[i] < opens[i]:
                kind = Direction.BEARISH
            else:
                kind = None

            contiguous = (
                start is not None
                and i > 0
                and (not self.config.require_contiguous_bars or close_times[i - 1] == stamps[i])
            )

            if kind is not None and kind is current and contiguous:
                continue

            if start is not None and current is not None:
                legs.append((start, i - 1, current))

            if kind is None:
                start, current = None, None
            else:
                start, current = i, kind

        if start is not None and current is not None:
            legs.append((start, len(work) - 1, current))
        return legs

    def detect(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[Cisd]:
        """Every delivery-state transition confirmed within the observed data."""
        if len(frame) < 2:
            return []

        work = with_close_time(frame, timeframe).sort_values("timestamp", kind="mergesort")
        work = work.reset_index(drop=True)

        opens = work["open"].to_numpy(dtype="float64")
        closes = work["close"].to_numpy(dtype="float64")
        stamps = work["timestamp"].to_numpy()
        close_times = work["close_time"].to_numpy()

        point = symbol.spec.point_value
        tolerance = self.config.trigger_tolerance_points * point

        # Candidates are collected first and the state machine is applied strictly in
        # TRIGGER order. Legs are discovered in start order, but a short later leg can
        # trigger before a long earlier one resolves — walking the state machine in leg
        # order would then apply transitions out of chronological sequence and make
        # batch disagree with replay.
        candidates: list[tuple[int, int, int, Direction, float]] = []

        for start, end, leg_direction in self._legs(work):
            length = end - start + 1
            if length < self.config.min_leg_length:
                continue

            # A BEARISH delivery leg is broken by a BULLISH change of state.
            up = leg_direction is Direction.BEARISH
            leg_opens = opens[start : end + 1]
            if self.config.anchor is CisdAnchor.SERIES_OPEN:
                level = float(leg_opens[0])
            else:
                level = float(leg_opens.max() if up else leg_opens.min())

            threshold = level + tolerance if up else level - tolerance

            limit = min(end + 1 + self.config.max_bars_to_trigger, len(work))
            hit = None
            for i in range(end + 1, limit):
                if (closes[i] > threshold) if up else (closes[i] < threshold):
                    hit = i
                    break
            if hit is None:
                continue
            candidates.append((hit, start, end, leg_direction, level))

        transitions: list[Cisd] = []
        state = DeliveryState.UNDEFINED

        for hit, start, end, leg_direction, level in sorted(candidates, key=lambda c: (c[0], c[1])):
            up = leg_direction is Direction.BEARISH
            length = end - start + 1
            direction = Direction.BULLISH if up else Direction.BEARISH
            resulting = DeliveryState.BULLISH if up else DeliveryState.BEARISH
            if resulting is state:
                # Delivery is already on this side; a repeat is not a change of state.
                continue

            event_timestamp = pd.Timestamp(stamps[hit]).to_pydatetime()
            confirmation = composite_confirmation(
                [], own_trigger=pd.Timestamp(close_times[hit]).to_pydatetime()
            )
            transitions.append(
                Cisd(
                    cisd_id=(
                        f"cisd:{symbol.value}:{timeframe.value}:"
                        f"{direction.value}:{event_timestamp.isoformat()}"
                    ),
                    symbol=symbol.value,
                    timeframe=timeframe.value,
                    direction=direction,
                    trigger_level=level,
                    trigger_close=float(closes[hit]),
                    leg_start_timestamp=pd.Timestamp(stamps[start]).to_pydatetime(),
                    leg_end_timestamp=pd.Timestamp(stamps[end]).to_pydatetime(),
                    leg_length=length,
                    event_timestamp=event_timestamp,
                    confirmation_timestamp=confirmation,
                    previous_state=state,
                    resulting_state=resulting,
                )
            )
            state = resulting

        transitions.sort(key=lambda t: (t.confirmation_timestamp, t.cisd_id))
        return transitions

    def analyse(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> CisdAnalysis:
        return CisdAnalysis(transitions=self.detect(frame, symbol, timeframe))

    def events(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[IctEvent]:
        events = [
            IctEvent(
                symbol=t.symbol,
                timeframe=t.timeframe,
                event_type=(EventType.CISD_BULLISH if t.is_bullish else EventType.CISD_BEARISH),
                direction=t.direction,
                event_timestamp=t.event_timestamp,
                confirmation_timestamp=t.confirmation_timestamp,
                price_level=t.trigger_level,
                reference_level=t.trigger_close,
                created_timestamp=t.event_timestamp,
                metadata={
                    "cisd_id": t.cisd_id,
                    "leg_start_timestamp": t.leg_start_timestamp.isoformat(),
                    "leg_end_timestamp": t.leg_end_timestamp.isoformat(),
                    "leg_length": t.leg_length,
                    "previous_state": t.previous_state.value,
                    "resulting_state": t.resulting_state.value,
                    **self.config.as_dict(),
                },
            )
            for t in self.detect(frame, symbol, timeframe)
        ]
        events.sort(key=lambda e: (e.confirmation_timestamp, e.event_timestamp))
        return events

    def observable_at(
        self, frame: pd.DataFrame, as_of: datetime, symbol: Symbol, timeframe: Timeframe
    ) -> CisdAnalysis:
        full = self.analyse(frame, symbol, timeframe)
        return CisdAnalysis(transitions=filter_observable(full.transitions, as_of))

    def with_config(self, config: CisdConfig) -> CisdDetector:
        return replace(self, config=config)
