"""The shared Phase 2 detector event contract.

Every ICT detector emits :class:`IctEvent`. Defining it once, before the first
detector, is deliberate: the contract's whole purpose is that `confirmation_timestamp`
means the same thing everywhere, and a per-detector variation would defeat that.

**The central rule.**

    ``confirmation_timestamp`` is the earliest timestamp at which this event could
    have been known using ONLY information available at that time.

It is *not* where the event sits on a chart — that is ``event_timestamp``. The two
differ whenever a pattern needs later candles to confirm it, which is most of ICT:

===================  ==============================  ===============================
Event                event_timestamp                 confirmation_timestamp
===================  ==============================  ===============================
Swing high           the pivot bar's open            close of bar ``pivot + right``
Session high         the bar that set the high       the session's end
FVG                  candle 3's open                 candle 3's ``close_time``
BOS / MSS            the breaking bar's open         the breaking bar's ``close_time``
Liquidity sweep      the sweeping bar's open         the sweeping bar's ``close_time``
True daily open      the 00:00 NY boundary bar       the SAME instant
===================  ==============================  ===============================

The last row is the sole zero-lag case in Phase 2, and it is correct rather than
suspicious: it reads a bar's ``open``, which is fixed at the bar's first print and can
never be revised by what the bar does afterwards. Waiting for its close would be the
*opposite* error — publishing known information a bar late. See
``docs/ict/true_daily_open.md`` §4.

Collapsing these two into one field is the single most common way ICT research
leaks the future. `ForexQuant`'s FVG detector did exactly that, was "fixed" once,
and is *still* one bar early — see ``docs/financial-ai/LEGACY_RESEARCH.md`` §5.1.

**No LLM ever produces one of these events** (CLAUDE.md rules 2 and 3). Detectors are
pure, deterministic functions of observed bars.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

import pandas as pd


class EventType(StrEnum):
    """Every ICT event kind Phase 2 can emit.

    Declared up front across all seven stories so downstream encodings (R2-07's
    feature vector) stay stable as detectors land, rather than shifting each time.
    """

    # --- R2-01 sessions -----------------------------------------------------
    SESSION_HIGH = "session_high"
    SESSION_LOW = "session_low"
    SESSION_OPEN = "session_open"
    SESSION_CLOSE = "session_close"

    # --- R2-02 swings -------------------------------------------------------
    SWING_HIGH = "swing_high"
    SWING_LOW = "swing_low"

    # --- R2-03 market structure --------------------------------------------
    HIGHER_HIGH = "higher_high"
    HIGHER_LOW = "higher_low"
    LOWER_HIGH = "lower_high"
    LOWER_LOW = "lower_low"
    BOS = "bos"
    MSS = "mss"
    CHOCH = "choch"

    # --- R2-04 liquidity ----------------------------------------------------
    EQUAL_HIGHS = "equal_highs"
    EQUAL_LOWS = "equal_lows"
    PREVIOUS_DAY_HIGH = "previous_day_high"
    PREVIOUS_DAY_LOW = "previous_day_low"
    PREVIOUS_WEEK_HIGH = "previous_week_high"
    PREVIOUS_WEEK_LOW = "previous_week_low"
    LIQUIDITY_SWEEP = "liquidity_sweep"

    # --- R2-05 fair value gaps ---------------------------------------------
    FVG_BULLISH = "fvg_bullish"
    FVG_BEARISH = "fvg_bearish"

    # --- R2-05.1 true daily open -------------------------------------------
    #: 00:00 America/New_York. Emphatically NOT the 17:00 New York trading-day
    #: boundary that R2-04 uses for PREVIOUS_DAY_*; the two are different concepts
    #: that happen to both be daily. See ``docs/ict/true_daily_open.md`` §1.
    TRUE_DAILY_OPEN = "true_daily_open"

    # --- R2-05.2 composite PD arrays ---------------------------------------
    #: Every member below is a COMPOSITE: it references source events by id rather
    #: than re-deriving them. See ``docs/ict/R2-05x-CONCEPT-MAP.md``.
    IFVG_BULLISH = "ifvg_bullish"
    IFVG_BEARISH = "ifvg_bearish"
    ORDER_BLOCK_BULLISH = "order_block_bullish"
    ORDER_BLOCK_BEARISH = "order_block_bearish"
    BREAKER_BULLISH = "breaker_bullish"
    BREAKER_BEARISH = "breaker_bearish"
    BALANCED_PRICE_RANGE = "balanced_price_range"
    RDRB_BULLISH = "rdrb_bullish"
    RDRB_BEARISH = "rdrb_bearish"
    CISD_BULLISH = "cisd_bullish"
    CISD_BEARISH = "cisd_bearish"

    # --- R2-06 premium / discount ------------------------------------------
    DEALING_RANGE = "dealing_range"


class Direction(StrEnum):
    """Directional bias of an event.

    ``NEUTRAL`` is a real value, not a fallback for "unknown" — a dealing range has
    no direction. An event whose direction cannot be determined is a bug, not a
    ``NEUTRAL``.
    """

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class EventStatus(StrEnum):
    """Lifecycle of an event that can later be consumed or invalidated."""

    ACTIVE = "active"
    MITIGATED = "mitigated"
    INVALIDATED = "invalidated"
    SWEPT = "swept"


class ContractViolation(ValueError):
    """Raised when an event breaks the contract's invariants."""


@runtime_checkable
class Confirmable(Protocol):
    """Anything whose observability is governed by a ``confirmation_timestamp``.

    Lets the one observability gate cover detector-native records (liquidity levels,
    sweeps) as well as :class:`IctEvent`, so no detector needs its own copy of the
    rule.
    """

    confirmation_timestamp: datetime


@dataclass(frozen=True)
class IctEvent:
    """One deterministic ICT observation.

    Required fields are the Phase 2 contract minimum. Optional fields apply where
    the concept supports them (liquidity levels, FVGs) and stay ``None`` elsewhere
    rather than being faked.
    """

    symbol: str
    timeframe: str
    event_type: EventType
    direction: Direction
    event_timestamp: datetime
    confirmation_timestamp: datetime
    price_level: float
    reference_level: float | None = None
    strength: float | None = None

    # --- optional, where applicable ----------------------------------------
    created_timestamp: datetime | None = None
    invalidation_timestamp: datetime | None = None
    distance_from_price: float | None = None
    age: int | None = None
    status: EventStatus = EventStatus.ACTIVE
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("event_timestamp", "confirmation_timestamp"):
            value = getattr(self, name)
            if value.tzinfo is None:
                raise ContractViolation(f"{name} must be timezone-aware UTC; got naive {value!r}")

        if self.confirmation_timestamp < self.event_timestamp:
            # Confirmation cannot precede the thing it confirms. This catches the
            # sign errors that silently create look-ahead.
            raise ContractViolation(
                f"confirmation_timestamp {self.confirmation_timestamp.isoformat()} precedes "
                f"event_timestamp {self.event_timestamp.isoformat()} for "
                f"{self.event_type.value} — an event cannot be confirmed before it occurs"
            )

    @property
    def confirmation_lag(self):
        """How long after the event it became knowable. Zero is legitimate (an event
        confirmed by its own bar's close) but is worth inspecting when unexpected."""
        return self.confirmation_timestamp - self.event_timestamp

    def is_observable_at(self, as_of: datetime) -> bool:
        """Whether this event may be used in a decision timestamped ``as_of``.

        The single predicate every leakage test and every feature assembler calls.
        Confirmation exactly at ``as_of`` counts as observable — the information is
        known at that instant.
        """
        return is_observable_at(self, as_of)

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
            "strength": self.strength,
            "created_timestamp": (self.created_timestamp.isoformat() if self.created_timestamp else None),
            "invalidation_timestamp": (
                self.invalidation_timestamp.isoformat() if self.invalidation_timestamp else None
            ),
            "distance_from_price": self.distance_from_price,
            "age": self.age,
            "status": self.status.value,
            "metadata": dict(self.metadata),
        }


def events_to_frame(events: list[IctEvent]) -> pd.DataFrame:
    """Tabular view of a detector's output, for inspection and testing.

    Timestamp columns are real UTC datetimes rather than the ISO strings
    :meth:`IctEvent.as_dict` produces, so they can be compared and filtered directly.
    """
    if not events:
        return pd.DataFrame(
            {
                "symbol": pd.Series(dtype="string"),
                "timeframe": pd.Series(dtype="string"),
                "event_type": pd.Series(dtype="string"),
                "direction": pd.Series(dtype="string"),
                "event_timestamp": pd.Series(dtype="datetime64[ns, UTC]"),
                "confirmation_timestamp": pd.Series(dtype="datetime64[ns, UTC]"),
                "price_level": pd.Series(dtype="float64"),
                "reference_level": pd.Series(dtype="float64"),
                "strength": pd.Series(dtype="float64"),
                "status": pd.Series(dtype="string"),
            }
        )

    frame = pd.DataFrame([e.as_dict() for e in events])
    for column in ("event_timestamp", "confirmation_timestamp"):
        frame[column] = pd.to_datetime(frame[column], utc=True)
    for column in ("symbol", "timeframe", "event_type", "direction", "status"):
        frame[column] = frame[column].astype("string")
    return frame


def is_observable_at(item: Confirmable, as_of: datetime) -> bool:
    """THE observability predicate. Every check in the codebase resolves to this.

    Defined once as a free function so it applies to anything carrying a
    ``confirmation_timestamp`` — ``IctEvent``, but also detector-native records such
    as ``LiquidityLevel`` and ``LiquiditySweep``. Detectors must not hand-roll
    ``x.confirmation_timestamp <= t``: five private copies of a rule are five places
    it can silently drift.

    Confirmation exactly at ``as_of`` counts as observable — the information is known
    at that instant.
    """
    if as_of.tzinfo is None:
        raise ContractViolation(f"as_of must be timezone-aware UTC; got naive {as_of!r}")
    return item.confirmation_timestamp <= as_of


def filter_observable(items: Sequence[Confirmable], as_of: datetime) -> list:
    """What a decision timestamped ``as_of`` is allowed to see.

    **The single gate every downstream consumer must go through.** R2-07 assembles
    feature vectors from detector output, and the only thing standing between "a swing
    exists at 09:05" and "a model at 09:05 knew about it" is this filter. Keeping it
    here — one reviewed function, like ``align_htf_context`` for timeframes — is what
    makes the rule enforceable rather than merely stated.

    Accepts any confirmable record, not only :class:`IctEvent`.
    """
    if as_of.tzinfo is None:
        raise ContractViolation(f"as_of must be timezone-aware UTC; got naive {as_of!r}")
    return [item for item in items if is_observable_at(item, as_of)]


def assert_observable(items: Sequence[Confirmable], as_of: datetime) -> None:
    """Fail loudly if anything in ``items`` was not yet knowable at ``as_of``.

    The assertion form of :func:`filter_observable`, for tests and for defensive
    checks inside feature assembly.
    """
    leaking = [x for x in items if not is_observable_at(x, as_of)]
    if leaking:
        first = leaking[0]
        detail = first.as_dict() if hasattr(first, "as_dict") else repr(first)
        raise ContractViolation(
            f"{len(leaking)} item(s) are not observable at {as_of.isoformat()}; " f"first offender: {detail}"
        )


def assert_no_leakage(events: list[IctEvent]) -> None:
    """Fail loudly if any event claims to be knowable before it happened.

    A cheap invariant every detector's tests can call, so the check does not have to
    be re-derived per detector.
    """
    for event in events:
        if event.confirmation_timestamp < event.event_timestamp:  # pragma: no cover - constructor guards
            raise ContractViolation(f"leaking event: {event.as_dict()}")
