"""Market structure — HH/HL/LH/LL, BOS, MSS/CHoCH.

Formal definitions, the state machine, and every interpretation we did NOT silently
adopt are in ``docs/ict/structure.md``. Read that first; this module implements it.

**Hard dependency on R2-02.** The detector consumes *only* swings that are already
observable, admitted through the shared contract's observability rule:

    at bar j, usable swings == filter_observable(swings, bar_j.close_time)

A swing that has not confirmed cannot classify, cannot become a reference level, and
cannot be broken. Structure therefore inherits the swing confirmation lag instead of
bypassing it — which is the entire point, since a "BOS" of a pivot nobody could yet
see is exactly the leak this project exists to prevent.

**Five distinctions kept apart** (usually collapsed elsewhere): swing occurrence,
swing confirmation, structure classification, break occurrence, break confirmation.

**BOS vs MSS is the prior state, not a different algorithm.** Same break; the label
depends on whether it continues or opposes the prevailing structure. CHoCH is, by
default, a synonym for MSS and is not emitted — stated plainly rather than faked into
a separate code path.
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
from .contract import Direction, EventType, IctEvent, filter_observable
from .swings import SwingConfig, SwingDetector, SwingPoint

logger = get_logger(__name__)


class StructureState(StrEnum):
    """The prevailing structural trend.

    ``UNDEFINED`` is a real starting state, not "unknown": before any break there is
    no established character, and the first break establishes one.
    """

    UNDEFINED = "undefined"
    BULLISH = "bullish"
    BEARISH = "bearish"


class BreakMode(StrEnum):
    """What price action constitutes a break.

    ``CLOSE`` (default) requires the bar's close beyond the level. ``WICK`` accepts the
    bar's extreme. Close is the default because a wick break fires on every stop-run
    and liquidity sweep — which R2-04 will model as *sweeps*, not structural breaks.
    Defaulting to WICK would conflate the two concepts.

    **Both modes still confirm at the bar's close**: with bar data the intrabar
    sequence is unknowable, so even a wick break is only *knowable* once the bar ends.
    """

    CLOSE = "close"
    WICK = "wick"


class ChochPolicy(StrEnum):
    """Whether CHoCH is a distinct event from MSS.

    ``SYNONYM`` (default) — they are the same thing. Counter-trend breaks emit ``MSS``
    and ``CHOCH`` is never emitted. Said plainly rather than pretending two algorithms
    exist.

    ``DISTINCT_BY_DISPLACEMENT`` — a counter-trend break emits ``MSS`` when the
    breaking bar shows displacement (range >= factor x mean range of the previous N
    bars), else ``CHOCH``. This is the one distinction in circulation that is both
    deterministic and defensible.
    """

    SYNONYM = "synonym"
    DISTINCT_BY_DISPLACEMENT = "distinct_by_displacement"


@dataclass(frozen=True)
class StructureConfig:
    """Structure parameters. Configuration, never literals in the detector."""

    break_mode: BreakMode = BreakMode.CLOSE
    #: Price must exceed the level by MORE than this (in instrument points) to break
    #: it. 0 means a strict comparison; equality is never a break.
    break_tolerance_points: float = 0.0
    #: Two swing levels within this many points are "equal" and yield no HH/HL/LH/LL
    #: label. Keeps "equal" from being a floating-point accident.
    equal_level_tolerance_points: float = 0.0
    #: Significance filter: swings with R2-02 prominence below this are excluded from
    #: structure entirely. 0 keeps every swing. Deliberately the simplest possible
    #: deterministic mechanism — ranking is deferred.
    min_swing_strength_points: float = 0.0
    choch_policy: ChochPolicy = ChochPolicy.SYNONYM
    displacement_lookback: int = 20
    displacement_factor: float = 1.5

    def __post_init__(self) -> None:
        if self.displacement_lookback < 1:
            raise ValueError(f"displacement_lookback must be >= 1; got {self.displacement_lookback}")
        if self.displacement_factor <= 0:
            raise ValueError(f"displacement_factor must be > 0; got {self.displacement_factor}")
        for name in ("break_tolerance_points", "equal_level_tolerance_points", "min_swing_strength_points"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0; got {getattr(self, name)}")

    def as_dict(self) -> dict:
        return {
            "break_mode": self.break_mode.value,
            "break_tolerance_points": self.break_tolerance_points,
            "equal_level_tolerance_points": self.equal_level_tolerance_points,
            "min_swing_strength_points": self.min_swing_strength_points,
            "choch_policy": self.choch_policy.value,
            "displacement_lookback": self.displacement_lookback,
            "displacement_factor": self.displacement_factor,
        }


@dataclass(frozen=True)
class SwingLabel:
    """A confirmed swing classified against the previous swing of the same type."""

    symbol: str
    timeframe: str
    label: EventType  # HIGHER_HIGH | HIGHER_LOW | LOWER_HIGH | LOWER_LOW
    direction: Direction
    event_timestamp: datetime
    confirmation_timestamp: datetime
    price_level: float
    reference_level: float
    #: Distance from the previous same-type swing, in instrument points. Signed by
    #: the label's own direction, so it is always >= 0.
    strength: float
    previous_swing_timestamp: datetime

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "label": self.label.value,
            "direction": self.direction.value,
            "event_timestamp": self.event_timestamp.isoformat(),
            "confirmation_timestamp": self.confirmation_timestamp.isoformat(),
            "price_level": self.price_level,
            "reference_level": self.reference_level,
            "strength": self.strength,
            "previous_swing_timestamp": self.previous_swing_timestamp.isoformat(),
        }


@dataclass(frozen=True)
class StructureBreak:
    """A confirmed BOS / MSS / CHoCH, with the full transition recorded.

    Everything R2-07 needs is on this record: which swing was referenced, which level
    was broken, when it happened, when it became observable, the state before and
    after, and a deterministic magnitude. The detector must not be an opaque pattern
    recogniser.
    """

    symbol: str
    timeframe: str
    event_type: EventType  # BOS | MSS | CHOCH
    direction: Direction
    event_timestamp: datetime
    confirmation_timestamp: datetime
    #: The breaking bar's close (CLOSE mode) or extreme (WICK mode).
    price_level: float
    #: The swing level that was broken.
    reference_level: float
    previous_state: StructureState
    resulting_state: StructureState
    reference_swing_timestamp: datetime
    reference_swing_confirmation: datetime
    break_distance_points: float
    displacement_ratio: float | None
    bar_index: int

    @property
    def strength(self) -> float:
        """How far beyond the level price broke, in instrument points. A documented
        magnitude, not a tuned score."""
        return self.break_distance_points

    @property
    def is_reversal(self) -> bool:
        return self.previous_state is not StructureState.UNDEFINED and (
            self.previous_state != self.resulting_state
        )

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "event_type": self.event_type.value,
            "direction": self.direction.value,
            "event_timestamp": self.event_timestamp.isoformat(),
            "confirmation_timestamp": self.confirmation_timestamp.isoformat(),
            "price_level": self.price_level,
            "reference_level": self.reference_level,
            "previous_state": self.previous_state.value,
            "resulting_state": self.resulting_state.value,
            "reference_swing_timestamp": self.reference_swing_timestamp.isoformat(),
            "reference_swing_confirmation": self.reference_swing_confirmation.isoformat(),
            "break_distance_points": self.break_distance_points,
            "displacement_ratio": self.displacement_ratio,
            "bar_index": self.bar_index,
            "is_reversal": self.is_reversal,
        }


@dataclass
class StructureAnalysis:
    """Everything one structure run produced."""

    labels: list[SwingLabel] = field(default_factory=list)
    breaks: list[StructureBreak] = field(default_factory=list)
    final_state: StructureState = StructureState.UNDEFINED
    #: References still active at the end of the observed data — the levels a future
    #: bar could break. Exposed so a caller can see what is *pending*.
    pending_high: SwingPoint | None = None
    pending_low: SwingPoint | None = None
    swings_used: int = 0
    swings_filtered_out: int = 0

    def state_at(self, as_of: datetime) -> StructureState:
        """The structural state a decision timestamped ``as_of`` may assume.

        Derived only from breaks that had already confirmed — so it can never reflect
        a transition the caller could not have seen.
        """
        if as_of.tzinfo is None:
            raise ValueError(f"as_of must be timezone-aware UTC; got naive {as_of!r}")
        observable = filter_observable(self.breaks, as_of)
        return observable[-1].resulting_state if observable else StructureState.UNDEFINED


@dataclass
class StructureDetector:
    """Deterministic market-structure detection over confirmed swings."""

    config: StructureConfig = StructureConfig()
    swing_config: SwingConfig = SwingConfig()

    # ------------------------------------------------------------------ setup

    @property
    def swing_detector(self) -> SwingDetector:
        return SwingDetector(self.swing_config)

    def _displacement(self, work: pd.DataFrame) -> np.ndarray:
        """Bar range divided by the mean range of the previous N bars.

        The mean uses bars **strictly before** the current one (``shift(1)``), so the
        ratio introduces no look-ahead: at the breaking bar's close both its own range
        and the prior mean are known.
        """
        ranges = (work["high"] - work["low"]).astype("float64")
        baseline = ranges.rolling(self.config.displacement_lookback).mean().shift(1)
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = ranges.to_numpy() / baseline.to_numpy()
        return ratio

    # ------------------------------------------------------------------- core

    def analyse(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> StructureAnalysis:
        """Walk the bars forward, admitting swings only once they are observable."""
        analysis = StructureAnalysis()
        if len(frame) == 0:
            return analysis

        work = with_close_time(frame, timeframe).sort_values("timestamp", kind="mergesort")
        work = work.reset_index(drop=True)

        all_swings = self.swing_detector.detect(frame, symbol, timeframe)
        threshold = self.config.min_swing_strength_points
        swings = [s for s in all_swings if s.strength >= threshold]
        analysis.swings_used = len(swings)
        analysis.swings_filtered_out = len(all_swings) - len(swings)
        if not swings:
            return analysis

        point = symbol.spec.point_value
        break_tol = self.config.break_tolerance_points * point
        equal_tol = self.config.equal_level_tolerance_points * point

        close_times = work["close_time"].to_numpy()
        open_times = work["timestamp"].to_numpy()
        closes = work["close"].to_numpy(dtype="float64")
        highs = work["high"].to_numpy(dtype="float64")
        lows = work["low"].to_numpy(dtype="float64")
        displacement = self._displacement(work)

        state = StructureState.UNDEFINED
        previous_high: SwingPoint | None = None  # for HH/LH classification
        previous_low: SwingPoint | None = None
        active_high: SwingPoint | None = None  # the protected reference levels
        active_low: SwingPoint | None = None
        cursor = 0

        for index in range(len(work)):
            now = close_times[index]

            # --- 1. Admit swings that have become observable BY THIS BAR'S CLOSE.
            # This is the R2-02 dependency, enforced structurally rather than by
            # convention: a swing simply is not in scope until its confirmation.
            while cursor < len(swings) and pd.Timestamp(
                swings[cursor].confirmation_timestamp
            ) <= pd.Timestamp(now):
                swing = swings[cursor]
                cursor += 1

                if swing.is_high:
                    label = self._classify(swing, previous_high, equal_tol, point, is_high=True)
                    if label is not None:
                        analysis.labels.append(label)
                    previous_high = swing
                    # Absorbed BEFORE the break check — see docs/ict/structure.md §3.
                    active_high = swing
                else:
                    label = self._classify(swing, previous_low, equal_tol, point, is_high=False)
                    if label is not None:
                        analysis.labels.append(label)
                    previous_low = swing
                    active_low = swing

            # --- 2. Evaluate a break of the active references.
            bullish_probe = closes[index] if self.config.break_mode is BreakMode.CLOSE else highs[index]
            bearish_probe = closes[index] if self.config.break_mode is BreakMode.CLOSE else lows[index]

            if active_high is not None and bullish_probe > active_high.price_level + break_tol:
                analysis.breaks.append(
                    self._make_break(
                        symbol=symbol,
                        timeframe=timeframe,
                        direction=Direction.BULLISH,
                        reference=active_high,
                        probe=bullish_probe,
                        state=state,
                        index=index,
                        open_times=open_times,
                        close_times=close_times,
                        displacement=displacement,
                        point=point,
                    )
                )
                state = StructureState.BULLISH
                active_high = None  # consumed: one level cannot break twice

            elif active_low is not None and bearish_probe < active_low.price_level - break_tol:
                analysis.breaks.append(
                    self._make_break(
                        symbol=symbol,
                        timeframe=timeframe,
                        direction=Direction.BEARISH,
                        reference=active_low,
                        probe=bearish_probe,
                        state=state,
                        index=index,
                        open_times=open_times,
                        close_times=close_times,
                        displacement=displacement,
                        point=point,
                    )
                )
                state = StructureState.BEARISH
                active_low = None

        analysis.final_state = state
        analysis.pending_high = active_high
        analysis.pending_low = active_low

        logger.info(
            "structure %s %s: %d label(s), %d break(s), final state=%s "
            "(swings used=%d filtered=%d, mode=%s)",
            symbol.value,
            timeframe.value,
            len(analysis.labels),
            len(analysis.breaks),
            state.value,
            analysis.swings_used,
            analysis.swings_filtered_out,
            self.config.break_mode.value,
        )
        return analysis

    # -------------------------------------------------------------- internals

    def _classify(
        self,
        swing: SwingPoint,
        previous: SwingPoint | None,
        equal_tol: float,
        point: float,
        *,
        is_high: bool,
    ) -> SwingLabel | None:
        """HH/HL/LH/LL against the PREVIOUS same-type swing only.

        Never a later swing — a classification must be final the moment it is made.
        The first swing of each type has no predecessor and is unlabelled.
        Equal levels are unlabelled: equal highs are liquidity (R2-04), not structure.
        """
        if previous is None:
            return None

        delta = swing.price_level - previous.price_level
        if abs(delta) <= equal_tol:
            return None

        if is_high:
            label = EventType.HIGHER_HIGH if delta > 0 else EventType.LOWER_HIGH
            direction = Direction.BULLISH if delta > 0 else Direction.BEARISH
        else:
            label = EventType.HIGHER_LOW if delta > 0 else EventType.LOWER_LOW
            direction = Direction.BULLISH if delta > 0 else Direction.BEARISH

        # max() guards the gap case, where a later pivot can confirm earlier than an
        # earlier one if a market break sits between them.
        confirmation = max(swing.confirmation_timestamp, previous.confirmation_timestamp)

        return SwingLabel(
            symbol=swing.symbol,
            timeframe=swing.timeframe,
            label=label,
            direction=direction,
            event_timestamp=swing.event_timestamp,
            confirmation_timestamp=confirmation,
            price_level=swing.price_level,
            reference_level=previous.price_level,
            strength=abs(delta) / point if point else abs(delta),
            previous_swing_timestamp=previous.event_timestamp,
        )

    def _make_break(
        self,
        *,
        symbol: Symbol,
        timeframe: Timeframe,
        direction: Direction,
        reference: SwingPoint,
        probe: float,
        state: StructureState,
        index: int,
        open_times: np.ndarray,
        close_times: np.ndarray,
        displacement: np.ndarray,
        point: float,
    ) -> StructureBreak:
        resulting = StructureState.BULLISH if direction is Direction.BULLISH else StructureState.BEARISH
        continuation = state is StructureState.UNDEFINED or state is resulting

        ratio = displacement[index]
        ratio_value = None if np.isnan(ratio) or np.isinf(ratio) else float(ratio)

        if continuation:
            # From UNDEFINED the first break establishes the trend — there is no prior
            # character to change, so it is a BOS.
            event_type = EventType.BOS
        elif self.config.choch_policy is ChochPolicy.SYNONYM:
            event_type = EventType.MSS
        else:
            displaced = ratio_value is not None and ratio_value >= self.config.displacement_factor
            event_type = EventType.MSS if displaced else EventType.CHOCH

        distance = abs(probe - reference.price_level)

        return StructureBreak(
            symbol=symbol.value,
            timeframe=timeframe.value,
            event_type=event_type,
            direction=direction,
            event_timestamp=pd.Timestamp(open_times[index]).to_pydatetime(),
            confirmation_timestamp=pd.Timestamp(close_times[index]).to_pydatetime(),
            price_level=float(probe),
            reference_level=reference.price_level,
            previous_state=state,
            resulting_state=resulting,
            reference_swing_timestamp=reference.event_timestamp,
            reference_swing_confirmation=reference.confirmation_timestamp,
            break_distance_points=distance / point if point else distance,
            displacement_ratio=ratio_value,
            bar_index=index,
        )

    # ---------------------------------------------------------------- public

    def events(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[IctEvent]:
        """Contract events for every label and break, ordered by confirmation."""
        analysis = self.analyse(frame, symbol, timeframe)
        events: list[IctEvent] = []

        for label in analysis.labels:
            events.append(
                IctEvent(
                    symbol=label.symbol,
                    timeframe=label.timeframe,
                    event_type=label.label,
                    direction=label.direction,
                    event_timestamp=label.event_timestamp,
                    confirmation_timestamp=label.confirmation_timestamp,
                    price_level=label.price_level,
                    reference_level=label.reference_level,
                    strength=label.strength,
                    metadata={
                        "previous_swing_timestamp": label.previous_swing_timestamp.isoformat(),
                        **self.config.as_dict(),
                    },
                )
            )

        for structure_break in analysis.breaks:
            events.append(
                IctEvent(
                    symbol=structure_break.symbol,
                    timeframe=structure_break.timeframe,
                    event_type=structure_break.event_type,
                    direction=structure_break.direction,
                    event_timestamp=structure_break.event_timestamp,
                    confirmation_timestamp=structure_break.confirmation_timestamp,
                    price_level=structure_break.price_level,
                    reference_level=structure_break.reference_level,
                    strength=structure_break.strength,
                    metadata={
                        "previous_state": structure_break.previous_state.value,
                        "resulting_state": structure_break.resulting_state.value,
                        "reference_swing_timestamp": structure_break.reference_swing_timestamp.isoformat(),
                        "reference_swing_confirmation": (
                            structure_break.reference_swing_confirmation.isoformat()
                        ),
                        "displacement_ratio": structure_break.displacement_ratio,
                        "is_reversal": structure_break.is_reversal,
                        **self.config.as_dict(),
                    },
                )
            )

        events.sort(key=lambda e: (e.confirmation_timestamp, e.event_timestamp, e.event_type.value))
        return events

    def observable_at(
        self, frame: pd.DataFrame, as_of: datetime, symbol: Symbol, timeframe: Timeframe
    ) -> StructureAnalysis:
        """The structure a decision timestamped ``as_of`` may use.

        Proven equal to running the detector over only the bars visible at ``as_of``.
        """
        if as_of.tzinfo is None:
            raise ValueError(f"as_of must be timezone-aware UTC; got naive {as_of!r}")

        full = self.analyse(frame, symbol, timeframe)
        limited = StructureAnalysis(
            labels=filter_observable(full.labels, as_of),
            breaks=filter_observable(full.breaks, as_of),
            swings_used=full.swings_used,
            swings_filtered_out=full.swings_filtered_out,
        )
        limited.final_state = limited.state_at(as_of)
        return limited

    def with_config(
        self, config: StructureConfig | None = None, swing_config: SwingConfig | None = None
    ) -> StructureDetector:
        return replace(
            self,
            config=config or self.config,
            swing_config=swing_config or self.swing_config,
        )
