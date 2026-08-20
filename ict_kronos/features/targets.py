"""Prediction targets — the only place in the engine allowed to read the future (R2-08).

Full semantics in ``docs/features/targets.md``. Read that first.

The engine's temporal contract has exactly two halves, and this module is the second:

.. code-block:: text

    FEATURES(T)  <- information observable at T, and nothing else   (R2-07)
    TARGET(T)    <- may use information strictly after T            (here)

That asymmetry is deliberate and is what makes supervised learning possible at all.
It is also the single most dangerous line in the repository, so it is drawn *between
modules*: nothing here imports ``market_state`` or ``feature_vector``, nothing there
imports this, and a source guard asserts both directions. A target can never leak into
a feature because a feature has no way to reach one.

Every convention below is a **choice**, not ICT doctrine, and each is named in the docs
with its alternative recorded:

* **Reference price is the close of the bar whose close is ``as_of``** — the last price
  knowable at T, and the same number the feature vector reports as ``close``.
* **The future window is bars ``i+1 … i+H`` inclusive**, so no target can touch the bar
  the observation was made on.
* **Thresholds and distances are in instrument points**, matching R2-07 §8 — never a
  return fraction, so nothing silently rescales between EURUSD and XAUUSD.
* **Unresolved is a real answer.** A target that cannot be computed is ``None`` with an
  explicit reason. It is never 0, never NEUTRAL, never False.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

import pandas as pd

from ..domain import Symbol, Timeframe

#: Bumped whenever a target's MEANING changes — a new convention, a changed boundary
#: rule, a different reference price. A dataset records it so a result can be tied to
#: the exact definition that produced it.
TARGET_SCHEMA_VERSION = "r2-08.1"


class TargetType(StrEnum):
    """What a specification computes. One type, one formula, one documented meaning."""

    FUTURE_RETURN = "future_return"
    DIRECTION = "direction"
    EXCURSION = "excursion"
    TP_BEFORE_SL = "tp_before_sl"


class TargetDirection(StrEnum):
    """The classification produced by a ``DIRECTION`` target.

    ``UNRESOLVED`` is a fourth answer and never collapses into ``NEUTRAL``: "the market
    barely moved" and "we cannot know yet" are different facts, and a model trained on
    the two conflated is being taught that missing data means indecision.
    """

    UP = "up"
    DOWN = "down"
    NEUTRAL = "neutral"
    UNRESOLVED = "unresolved"


class TradeSide(StrEnum):
    """Which way a TP/SL specification is oriented. Explicit, never inferred."""

    LONG = "long"
    SHORT = "short"


class TpSlOutcome(StrEnum):
    """Outcome of a TP-before-SL race."""

    TP_FIRST = "tp_first"
    SL_FIRST = "sl_first"
    UNRESOLVED = "unresolved"


class UnresolvedReason(StrEnum):
    """WHY a target is unresolved. Required, because the reasons are not interchangeable.

    ``SAME_BAR_AMBIGUITY`` is the one that matters most: a bar that touches both the
    take-profit and the stop-loss carries no information about which came first. The
    honest answer is that we do not know — inventing an order (open-to-high-to-low, or
    "stops always fill first") would fabricate the label a model then learns from.
    """

    #: The horizon extends past the last available bar.
    INSUFFICIENT_HISTORY = "insufficient_history"
    #: The full horizon is available and neither barrier was touched.
    NO_TOUCH_WITHIN_HORIZON = "no_touch_within_horizon"
    #: One bar touched BOTH barriers. Intrabar order is unknowable from OHLC.
    SAME_BAR_AMBIGUITY = "same_bar_ambiguity"
    #: A bar inside the window carries a non-finite price. Never repaired, never skipped.
    MALFORMED_FUTURE_BAR = "malformed_future_bar"


class TargetSpecError(ValueError):
    """Raised when a specification is impossible rather than merely unsatisfiable."""


@dataclass(frozen=True)
class TargetSpec:
    """One target definition. Immutable, versioned, serialisable, fully explicit.

    A specification is the *question*; a :class:`TargetValue` is one answer. Everything
    that changes the meaning of the answer lives here, so two datasets built from
    different conventions can never be mistaken for each other.
    """

    name: str
    target_type: TargetType
    #: Bars strictly after the observation. Never implicit — §4 of the brief.
    horizon_bars: int
    #: DIRECTION only. In instrument points. Configuration, never fitted from data.
    threshold_points: float | None = None
    #: TP_BEFORE_SL only.
    side: TradeSide | None = None
    take_profit_points: float | None = None
    stop_loss_points: float | None = None
    version: str = TARGET_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.name:
            raise TargetSpecError("a target specification must be named")
        if self.horizon_bars < 1:
            raise TargetSpecError(
                f"{self.name}: horizon_bars must be >= 1; got {self.horizon_bars}. A "
                "horizon of 0 would make the target readable at prediction time."
            )

        if self.target_type is TargetType.DIRECTION:
            if self.threshold_points is None:
                raise TargetSpecError(f"{self.name}: a DIRECTION target requires threshold_points")
            if self.threshold_points < 0:
                raise TargetSpecError(
                    f"{self.name}: threshold_points must be >= 0; got {self.threshold_points}"
                )
        elif self.threshold_points is not None:
            raise TargetSpecError(f"{self.name}: threshold_points is meaningful only for DIRECTION")

        if self.target_type is TargetType.TP_BEFORE_SL:
            if self.side is None:
                raise TargetSpecError(
                    f"{self.name}: a TP_BEFORE_SL target requires an explicit side. Whether a "
                    "barrier is a profit or a loss depends on it, and guessing hides the "
                    "assumption inside the label."
                )
            for label, value in (
                ("take_profit_points", self.take_profit_points),
                ("stop_loss_points", self.stop_loss_points),
            ):
                if value is None:
                    raise TargetSpecError(f"{self.name}: a TP_BEFORE_SL target requires {label}")
                if value <= 0:
                    raise TargetSpecError(f"{self.name}: {label} must be > 0; got {value}")
        elif (
            self.side is not None or self.take_profit_points is not None or self.stop_loss_points is not None
        ):
            raise TargetSpecError(
                f"{self.name}: side / take_profit_points / stop_loss_points are meaningful "
                "only for TP_BEFORE_SL"
            )

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "target_type": self.target_type.value,
            "horizon_bars": self.horizon_bars,
            "threshold_points": self.threshold_points,
            "side": None if self.side is None else self.side.value,
            "take_profit_points": self.take_profit_points,
            "stop_loss_points": self.stop_loss_points,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> TargetSpec:
        return cls(
            name=payload["name"],
            target_type=TargetType(payload["target_type"]),
            horizon_bars=payload["horizon_bars"],
            threshold_points=payload["threshold_points"],
            side=None if payload["side"] is None else TradeSide(payload["side"]),
            take_profit_points=payload["take_profit_points"],
            stop_loss_points=payload["stop_loss_points"],
            version=payload["version"],
        )


@dataclass(frozen=True)
class TargetValue:
    """One answer for one instant. Immutable, and it carries its own provenance.

    ``value`` is deliberately loosely typed because the four target types answer in four
    different vocabularies — a float, an enum, a pair of excursions, an outcome. What is
    *not* loose is the resolution: ``resolved`` is a boolean and ``unresolved_reason``
    explains every ``False``.
    """

    spec_name: str
    target_type: TargetType
    horizon_bars: int
    version: str
    resolved: bool
    unresolved_reason: UnresolvedReason | None = None

    #: FUTURE_RETURN: the simple close-to-close return as a fraction.
    future_return: float | None = None
    #: FUTURE_RETURN: the same move in instrument points. Both are reported because the
    #: fraction is comparable across instruments and the points are comparable to every
    #: other distance in the engine.
    future_move_points: float | None = None
    #: DIRECTION.
    direction: TargetDirection | None = None
    #: EXCURSION: highest high minus reference, and reference minus lowest low, in
    #: points. SIGNED — see the module docs; a window that never trades above the
    #: reference has a negative upward excursion, and clamping it to 0 would assert the
    #: market touched a price it never touched.
    up_excursion_points: float | None = None
    down_excursion_points: float | None = None
    #: TP_BEFORE_SL.
    outcome: TpSlOutcome | None = None
    #: Index of the bar that resolved a TP/SL race, when one did.
    resolving_bar_timestamp: datetime | None = None

    # --- metadata: enough to explain the answer without recomputing it ---------
    reference_price: float | None = None
    reference_timestamp: datetime | None = None
    #: Close time of bar i+1 — the first instant the target is allowed to see.
    future_window_start: datetime | None = None
    #: Close time of bar i+H. With a weekend or a holiday inside the horizon this is
    #: further away in wall-clock time than H bar durations, and that is visible rather
    #: than smoothed over.
    future_window_end: datetime | None = None

    def as_dict(self) -> dict:
        return {
            "spec_name": self.spec_name,
            "target_type": self.target_type.value,
            "horizon_bars": self.horizon_bars,
            "version": self.version,
            "resolved": self.resolved,
            "unresolved_reason": (None if self.unresolved_reason is None else self.unresolved_reason.value),
            "future_return": self.future_return,
            "future_move_points": self.future_move_points,
            "direction": None if self.direction is None else self.direction.value,
            "up_excursion_points": self.up_excursion_points,
            "down_excursion_points": self.down_excursion_points,
            "outcome": None if self.outcome is None else self.outcome.value,
            "resolving_bar_timestamp": _iso(self.resolving_bar_timestamp),
            "reference_price": self.reference_price,
            "reference_timestamp": _iso(self.reference_timestamp),
            "future_window_start": _iso(self.future_window_start),
            "future_window_end": _iso(self.future_window_end),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> TargetValue:
        return cls(
            spec_name=payload["spec_name"],
            target_type=TargetType(payload["target_type"]),
            horizon_bars=payload["horizon_bars"],
            version=payload["version"],
            resolved=payload["resolved"],
            unresolved_reason=(
                None
                if payload["unresolved_reason"] is None
                else UnresolvedReason(payload["unresolved_reason"])
            ),
            future_return=payload["future_return"],
            future_move_points=payload["future_move_points"],
            direction=(None if payload["direction"] is None else TargetDirection(payload["direction"])),
            up_excursion_points=payload["up_excursion_points"],
            down_excursion_points=payload["down_excursion_points"],
            outcome=None if payload["outcome"] is None else TpSlOutcome(payload["outcome"]),
            resolving_bar_timestamp=_parse(payload["resolving_bar_timestamp"]),
            reference_price=payload["reference_price"],
            reference_timestamp=_parse(payload["reference_timestamp"]),
            future_window_start=_parse(payload["future_window_start"]),
            future_window_end=_parse(payload["future_window_end"]),
        )


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _parse(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


#: Horizons the repository uses by default. **Not ICT doctrine** — powers of two are a
#: convenient sweep, nothing more. Every target carries its own horizon, so nothing
#: downstream may assume this tuple.
DEFAULT_HORIZONS: tuple[int, ...] = (1, 2, 4, 8, 16)


#: Decimal places kept when converting a price difference to instrument points.
#:
#: A price is quantised to ``point_value`` by construction, so a move measured in points
#: is a whole number of points up to binary representation noise -- and that noise is
#: what makes an exactly-on-threshold move classify the wrong way: on EURUSD,
#: ``(1.0002 - 1.0) / 1e-5`` evaluates to ``19.999999999997797``, not ``20``.
#:
#: Six decimals is a million times finer than any supported instrument can express, so
#: this removes representation noise without rounding away anything a market can print.
#: It is a numerical-safety constant, not a claim about how precisely price moves.
_POINT_DECIMALS = 6


def _points(difference: float, point_value: float) -> float:
    return round(difference / point_value, _POINT_DECIMALS)


@dataclass
class TargetEngine:
    """Computes targets for a frame. Reads bars; reads no feature and no detector.

    Deliberately not a detector: there is no pattern here, no lifecycle and no
    observability gate, because a target is *defined* to look forward. Keeping it in
    its own module with its own vocabulary is what stops that licence from spreading.
    """

    symbol: Symbol
    timeframe: Timeframe
    frame: pd.DataFrame
    #: The frame with ``close_time`` attached, sorted. Set in ``__post_init__``.
    work: pd.DataFrame | None = field(default=None, repr=False)
    #: Closes, highs, lows and close times as plain lists — resolved once, because a
    #: dataset asks the same frame thousands of questions.
    _closes: list = field(default_factory=list, repr=False)
    _highs: list = field(default_factory=list, repr=False)
    _lows: list = field(default_factory=list, repr=False)
    _close_times: list = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        from ..data.resampler import with_close_time

        work = with_close_time(self.frame, self.timeframe).sort_values("timestamp", kind="mergesort")
        work = work.reset_index(drop=True)
        self.work = work
        self._closes = [float(x) for x in work["close"]]
        self._highs = [float(x) for x in work["high"]]
        self._lows = [float(x) for x in work["low"]]
        self._close_times = [t.to_pydatetime() for t in work["close_time"]]

    # ------------------------------------------------------------------ lookup

    @property
    def point(self) -> float:
        return self.symbol.spec.point_value

    def observation_instants(self) -> list[datetime]:
        return list(self._close_times)

    def index_of(self, as_of: datetime) -> int | None:
        """Position of the bar whose close IS ``as_of``. ``None`` between closes."""
        try:
            return self._close_times.index(as_of)
        except ValueError:
            return None

    # ----------------------------------------------------------------- compute

    def value_at(self, spec: TargetSpec, as_of: datetime) -> TargetValue:
        """The answer ``spec`` gives at ``as_of``, resolved or explicitly not."""
        index = self.index_of(as_of)
        if index is None:
            raise TargetSpecError(
                f"{as_of.isoformat()} is not a bar close on {self.symbol.value}/{self.timeframe.value}; "
                "an observation is anchored to a knowable close"
            )
        return self._value_at_index(spec, index)

    def values(self, specs: list[TargetSpec], instants: list[datetime] | None = None) -> dict:
        """``as_of -> tuple(TargetValue, ...)`` for every spec, in the order given."""
        chosen = self.observation_instants() if instants is None else instants
        out: dict = {}
        for moment in chosen:
            out[moment] = tuple(self.value_at(spec, moment) for spec in specs)
        return out

    # ------------------------------------------------------------- per formula

    def _value_at_index(self, spec: TargetSpec, index: int) -> TargetValue:
        reference = self._closes[index]
        last = index + spec.horizon_bars
        window_start = self._close_times[index + 1] if index + 1 < len(self._closes) else None
        window_end = self._close_times[last] if last < len(self._closes) else None

        base = dict(
            spec_name=spec.name,
            target_type=spec.target_type,
            horizon_bars=spec.horizon_bars,
            version=spec.version,
            reference_price=reference,
            reference_timestamp=self._close_times[index],
            future_window_start=window_start,
            future_window_end=window_end,
        )

        if not math.isfinite(reference):
            return TargetValue(
                resolved=False, unresolved_reason=UnresolvedReason.MALFORMED_FUTURE_BAR, **base
            )

        if spec.target_type is TargetType.TP_BEFORE_SL:
            return self._tp_before_sl(spec, index, reference, base)

        # The remaining three all need the COMPLETE window: a partial one understates an
        # excursion and answers a different question than the one that was asked.
        if last >= len(self._closes):
            return TargetValue(
                resolved=False, unresolved_reason=UnresolvedReason.INSUFFICIENT_HISTORY, **base
            )

        window = range(index + 1, last + 1)
        if spec.target_type is TargetType.EXCURSION:
            highs = [self._highs[i] for i in window]
            lows = [self._lows[i] for i in window]
            if not all(math.isfinite(x) for x in (*highs, *lows)):
                return TargetValue(
                    resolved=False, unresolved_reason=UnresolvedReason.MALFORMED_FUTURE_BAR, **base
                )
            return TargetValue(
                resolved=True,
                up_excursion_points=_points(max(highs) - reference, self.point),
                down_excursion_points=_points(reference - min(lows), self.point),
                **base,
            )

        final = self._closes[last]
        if not math.isfinite(final):
            return TargetValue(
                resolved=False, unresolved_reason=UnresolvedReason.MALFORMED_FUTURE_BAR, **base
            )

        move_points = _points(final - reference, self.point)
        if spec.target_type is TargetType.FUTURE_RETURN:
            return TargetValue(
                resolved=True,
                future_return=(final - reference) / reference,
                future_move_points=move_points,
                **base,
            )

        # DIRECTION. Precedence is declared rather than emergent: with a threshold of 0
        # the UP and DOWN conditions overlap at exactly zero and NEUTRAL becomes
        # unreachable. Checking UP first makes that deterministic; docs §3.2 records it
        # as a property of a zero threshold rather than a hidden tie-break.
        threshold = float(spec.threshold_points or 0.0)
        if move_points >= threshold:
            direction = TargetDirection.UP
        elif move_points <= -threshold:
            direction = TargetDirection.DOWN
        else:
            direction = TargetDirection.NEUTRAL
        return TargetValue(
            resolved=True,
            direction=direction,
            future_return=(final - reference) / reference,
            future_move_points=move_points,
            **base,
        )

    def _tp_before_sl(self, spec: TargetSpec, index: int, reference: float, base: dict) -> TargetValue:
        """First barrier touched, or an explicit admission that it cannot be known.

        Resolution is checked bar by bar, so a race decided inside the available data is
        answered even when the full horizon runs off the end — the later bars could not
        have changed an outcome that had already happened.
        """
        take_profit = float(spec.take_profit_points or 0.0) * self.point
        stop_loss = float(spec.stop_loss_points or 0.0) * self.point
        long = spec.side is TradeSide.LONG

        upper = reference + (take_profit if long else stop_loss)
        lower = reference - (stop_loss if long else take_profit)

        last = min(index + spec.horizon_bars, len(self._closes) - 1)
        for i in range(index + 1, last + 1):
            high, low = self._highs[i], self._lows[i]
            if not (math.isfinite(high) and math.isfinite(low)):
                return TargetValue(
                    resolved=False, unresolved_reason=UnresolvedReason.MALFORMED_FUTURE_BAR, **base
                )

            touched_upper = high >= upper
            touched_lower = low <= lower
            if not (touched_upper or touched_lower):
                continue

            if touched_upper and touched_lower:
                # Both barriers inside one bar. OHLC records no sequence, so there is no
                # honest answer -- see docs §3.4.
                return TargetValue(
                    resolved=False,
                    unresolved_reason=UnresolvedReason.SAME_BAR_AMBIGUITY,
                    outcome=TpSlOutcome.UNRESOLVED,
                    resolving_bar_timestamp=self._close_times[i],
                    **base,
                )

            hit_tp = touched_upper if long else touched_lower
            return TargetValue(
                resolved=True,
                outcome=TpSlOutcome.TP_FIRST if hit_tp else TpSlOutcome.SL_FIRST,
                resolving_bar_timestamp=self._close_times[i],
                **base,
            )

        reason = (
            UnresolvedReason.NO_TOUCH_WITHIN_HORIZON
            if index + spec.horizon_bars < len(self._closes)
            else UnresolvedReason.INSUFFICIENT_HISTORY
        )
        return TargetValue(resolved=False, unresolved_reason=reason, outcome=TpSlOutcome.UNRESOLVED, **base)


__all__ = [
    "DEFAULT_HORIZONS",
    "TARGET_SCHEMA_VERSION",
    "TargetDirection",
    "TargetEngine",
    "TargetSpec",
    "TargetSpecError",
    "TargetType",
    "TargetValue",
    "TpSlOutcome",
    "TradeSide",
    "UnresolvedReason",
]
