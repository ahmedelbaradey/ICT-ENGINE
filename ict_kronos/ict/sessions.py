"""Trading sessions and kill zones — deterministic, timezone- and DST-aware.

Full definitions, chosen defaults and the ambiguities we did NOT silently resolve are
documented in ``docs/ict/sessions.md``.

**Why sessions come first.** Every other ICT concept is conditioned on when in the
trading day price acted: liquidity uses session highs/lows, kill zones gate setup
quality, and the "is this a weekend or a data fault?" judgement — which the
normalizer deliberately withheld — lives here.

**How DST is handled.** A session is defined in its own *local* time
(``08:00 Europe/London``), never as a fixed UTC hour. Boundaries are resolved in that
local zone and converted to UTC, so when London moves to BST the UTC boundary moves
with it, automatically and without a special case. Stored bar timestamps are never
converted; only session boundaries are computed.

This matters concretely. In the validated Phase 1.5 data, after the same weekend
closure EURUSD's first bar was ``2024-03-10 21:00 UTC`` while XAUUSD's was
``22:00 UTC`` — the US DST transition moving the effective reopen differently per
instrument. **No fixed UTC opening time is assumed for any instrument.** Sessions come
from local-time definitions plus the bars that actually exist.

**Bar membership.** A bar belongs to a session window when it is *fully contained*:
``window.start <= bar.timestamp`` and ``bar.close_time <= window.end``. Partial bars
straddling a boundary are excluded, so a session's high can never be contaminated by
price action outside it. See ``docs/ict/sessions.md`` for the trade-off.

**Confirmation semantics.** A completed session's high/low/close is only *knowable*
at ``window.end`` — at the moment the extreme was printed you could not yet know
another bar would not exceed it. Running (in-progress) state is a separate, explicitly
point-in-time API.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

from ..app.logging import get_logger
from ..data.resampler import with_close_time
from ..domain import Symbol, Timeframe
from .contract import Direction, EventType, IctEvent

logger = get_logger(__name__)


class SessionKind(StrEnum):
    """Kill zones are modelled as first-class windows, not a flag on a session:
    they have their own high/low and their own confirmation timing."""

    SESSION = "session"
    KILL_ZONE = "kill_zone"


class BoundaryAnomaly(StrEnum):
    """What DST did to a session boundary on a given local date.

    Recorded rather than swallowed. On a spring-forward date a local time like
    02:30 does not exist; on a fall-back date it happens twice. Both are real and
    both change the session's UTC span, so they are surfaced instead of silently
    resolved.
    """

    NONE = "none"
    NONEXISTENT = "nonexistent"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class SessionDefinition:
    """A session as configuration: a name, a timezone, and local start/end times.

    Never a UTC hour — that is the whole point. ``end_local <= start_local`` means the
    window crosses local midnight (the Asian range does), and the occurrence is
    anchored to the local date of its **start**.
    """

    name: str
    timezone: str
    start_local: time
    end_local: time
    kind: SessionKind = SessionKind.SESSION

    @property
    def crosses_midnight(self) -> bool:
        return self.end_local <= self.start_local

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "timezone": self.timezone,
            "start_local": self.start_local.strftime("%H:%M"),
            "end_local": self.end_local.strftime("%H:%M"),
            "kind": self.kind.value,
            "crosses_midnight": self.crosses_midnight,
        }


#: Defaults. Contested in the ICT community — see ``docs/ict/sessions.md`` for the
#: reasoning, the sources, and the alternatives we did NOT silently adopt. Every
#: boundary here is overridable via configuration.
DEFAULT_SESSIONS: tuple[SessionDefinition, ...] = (
    # Broad market sessions, each in its own exchange's local time.
    SessionDefinition("asian", "Asia/Tokyo", time(9, 0), time(18, 0)),
    SessionDefinition("london", "Europe/London", time(8, 0), time(16, 30)),
    SessionDefinition("new_york", "America/New_York", time(8, 0), time(17, 0)),
    # ICT kill zones, conventionally quoted in New York local time.
    SessionDefinition("london_kill_zone", "America/New_York", time(2, 0), time(5, 0), SessionKind.KILL_ZONE),
    SessionDefinition(
        "new_york_kill_zone", "America/New_York", time(7, 0), time(10, 0), SessionKind.KILL_ZONE
    ),
)


def load_definitions(spec: str | None) -> tuple[SessionDefinition, ...]:
    """Build session definitions from a JSON spec, or return the documented defaults.

    ``spec`` may be an inline JSON array or a path to a JSON file. Each element needs
    ``name``, ``timezone``, ``start_local`` (``HH:MM``), ``end_local``, and optionally
    ``kind``.

    Validation is strict and loud: an unknown timezone or a malformed time is a
    configuration error that must surface at startup, not silently fall back to
    defaults and produce quietly wrong sessions for months.
    """
    if not spec or not spec.strip():
        return DEFAULT_SESSIONS

    raw = spec.strip()
    if not raw.startswith("["):
        path = Path(raw)
        if not path.is_file():
            raise ValueError(f"ICT_SESSIONS_JSON is neither inline JSON nor an existing file: {raw!r}")
        raw = path.read_text(encoding="utf-8")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"ICT_SESSIONS_JSON is not valid JSON: {exc}") from exc

    if not isinstance(payload, list) or not payload:
        raise ValueError("ICT_SESSIONS_JSON must be a non-empty JSON array of session definitions")

    definitions: list[SessionDefinition] = []
    for index, entry in enumerate(payload):
        try:
            definitions.append(
                SessionDefinition(
                    name=str(entry["name"]),
                    timezone=str(entry["timezone"]),
                    start_local=_parse_local_time(entry["start_local"]),
                    end_local=_parse_local_time(entry["end_local"]),
                    kind=SessionKind(str(entry.get("kind", SessionKind.SESSION.value))),
                )
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(f"ICT_SESSIONS_JSON entry {index} is invalid: {exc}") from exc

        try:
            definitions[-1].zone  # noqa: B018 - validates the tz name eagerly
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"ICT_SESSIONS_JSON entry {index}: unknown timezone {entry['timezone']!r}"
            ) from exc

    names = [d.name for d in definitions]
    if len(set(names)) != len(names):
        raise ValueError(f"ICT_SESSIONS_JSON has duplicate session names: {names}")

    return tuple(definitions)


def _parse_local_time(raw: object) -> time:
    """Parse ``HH:MM`` / ``HH:MM:SS`` into a :class:`datetime.time`."""
    text = str(raw).strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"expected a local time as HH:MM or HH:MM:SS; got {raw!r}")


@dataclass(frozen=True)
class SessionWindow:
    """One occurrence of a session definition on one local date, resolved to UTC."""

    name: str
    kind: SessionKind
    timezone: str
    local_date: date
    start_utc: datetime
    end_utc: datetime
    anomaly: BoundaryAnomaly = BoundaryAnomaly.NONE

    @property
    def duration(self) -> timedelta:
        return self.end_utc - self.start_utc

    def contains(self, moment: datetime) -> bool:
        """Half-open ``[start, end)`` so consecutive occurrences never double-count."""
        return self.start_utc <= moment < self.end_utc

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "timezone": self.timezone,
            "local_date": self.local_date.isoformat(),
            "start_utc": self.start_utc.isoformat(),
            "end_utc": self.end_utc.isoformat(),
            "duration_minutes": int(self.duration.total_seconds() // 60),
            "anomaly": self.anomaly.value,
        }


@dataclass(frozen=True)
class SessionOccurrence:
    """A session window that actually contained bars, with its OHLC extremes.

    A window with no bars produces **no occurrence** — absence is preserved, never
    fabricated. That is what makes a weekend distinguishable from a data fault.
    """

    window: SessionWindow
    symbol: str
    timeframe: str
    bar_count: int
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    open_timestamp: datetime
    close_timestamp: datetime
    high_timestamp: datetime
    low_timestamp: datetime

    @property
    def confirmation_timestamp(self) -> datetime:
        """When this session's extremes became final and knowable.

        ``window.end_utc``, not the extreme-setting bar: at the instant the high was
        printed you could not know a later in-window bar would not exceed it.
        """
        return self.window.end_utc

    @property
    def range_size(self) -> float:
        return self.high_price - self.low_price

    def as_dict(self) -> dict:
        return {
            **self.window.as_dict(),
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "bar_count": self.bar_count,
            "open": self.open_price,
            "high": self.high_price,
            "low": self.low_price,
            "close": self.close_price,
            "open_timestamp": self.open_timestamp.isoformat(),
            "close_timestamp": self.close_timestamp.isoformat(),
            "high_timestamp": self.high_timestamp.isoformat(),
            "low_timestamp": self.low_timestamp.isoformat(),
            "confirmation_timestamp": self.confirmation_timestamp.isoformat(),
            "range": self.range_size,
        }


@dataclass(frozen=True)
class RunningSessionState:
    """In-progress session state at an instant — the point-in-time feature API.

    Built only from bars whose ``close_time <= as_of``. Nothing here has been
    confirmed final; ``is_complete`` says whether the window has elapsed.
    """

    window: SessionWindow
    as_of: datetime
    bar_count: int
    open_price: float | None
    high_price: float | None
    low_price: float | None
    last_price: float | None
    is_active: bool
    is_complete: bool

    @property
    def range_size(self) -> float | None:
        if self.high_price is None or self.low_price is None:
            return None
        return self.high_price - self.low_price

    @property
    def position_in_range(self) -> float | None:
        """Where the last observed price sits inside the running session range."""
        span = self.range_size
        if span is None or self.last_price is None or span <= 0:
            return None
        return (self.last_price - self.low_price) / span


# ---------------------------------------------------------------------------
# Boundary resolution
# ---------------------------------------------------------------------------


def _local_to_utc(local_naive: datetime, zone: ZoneInfo) -> tuple[datetime, BoundaryAnomaly]:
    """Convert a naive local datetime to UTC, reporting DST anomalies.

    * **Nonexistent** (spring forward): the wall-clock time is skipped. Python resolves
      it using the pre-transition offset; we report it so the caller knows the window
      is an hour shorter than nominal that day.
    * **Ambiguous** (fall back): the wall-clock time occurs twice. We take the FIRST
      occurrence (``fold=0``, still-DST) and report it.

    Both are documented rather than silently normalised — a session that is 23 or 25
    hours from its neighbour once a year is a real property of the market, not a bug
    to paper over.
    """
    aware = local_naive.replace(tzinfo=zone)
    as_utc = aware.astimezone(UTC)

    # ORDER MATTERS. Under PEP 495 BOTH nonexistent and ambiguous local times have
    # differing utcoffsets between fold=0 and fold=1, so a fold comparison alone
    # cannot tell them apart. The round-trip is what discriminates:
    #
    #   nonexistent — the wall clock was skipped, so UTC -> local returns a
    #                 DIFFERENT wall time than the one asked for.
    #   ambiguous   — the wall clock happened twice, so UTC -> local returns the
    #                 SAME wall time for either fold.
    #
    # Checking ambiguity first therefore mislabels every spring-forward boundary.
    if as_utc.astimezone(zone).replace(tzinfo=None) != local_naive:
        return as_utc, BoundaryAnomaly.NONEXISTENT

    if aware.replace(fold=0).utcoffset() != aware.replace(fold=1).utcoffset():
        return aware.replace(fold=0).astimezone(UTC), BoundaryAnomaly.AMBIGUOUS

    return as_utc, BoundaryAnomaly.NONE


def resolve_window(definition: SessionDefinition, local_day: date) -> SessionWindow:
    """Resolve one session occurrence on one local date into a UTC window."""
    zone = definition.zone
    start_naive = datetime.combine(local_day, definition.start_local)
    end_day = local_day + timedelta(days=1) if definition.crosses_midnight else local_day
    end_naive = datetime.combine(end_day, definition.end_local)

    start_utc, start_anomaly = _local_to_utc(start_naive, zone)
    end_utc, end_anomaly = _local_to_utc(end_naive, zone)

    anomaly = start_anomaly if start_anomaly is not BoundaryAnomaly.NONE else end_anomaly
    return SessionWindow(
        name=definition.name,
        kind=definition.kind,
        timezone=definition.timezone,
        local_date=local_day,
        start_utc=start_utc,
        end_utc=end_utc,
        anomaly=anomaly,
    )


def resolve_windows(
    definitions: tuple[SessionDefinition, ...],
    start: datetime,
    end: datetime,
) -> list[SessionWindow]:
    """All session windows overlapping the UTC half-open range ``[start, end)``.

    Local dates are enumerated with a one-day margin on each side so windows that
    cross midnight, or whose UTC span reaches back into the previous local day, are
    not clipped away at the edges.
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware UTC")

    windows: list[SessionWindow] = []
    for definition in definitions:
        zone = definition.zone
        first_day = start.astimezone(zone).date() - timedelta(days=1)
        last_day = end.astimezone(zone).date() + timedelta(days=1)

        day = first_day
        while day <= last_day:
            window = resolve_window(definition, day)
            if window.end_utc > start and window.start_utc < end:
                windows.append(window)
            day += timedelta(days=1)

    windows.sort(key=lambda w: (w.start_utc, w.name))
    return windows


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


@dataclass
class SessionDetector:
    """Deterministic session detection over a canonical candle frame."""

    definitions: tuple[SessionDefinition, ...] = DEFAULT_SESSIONS
    #: Emitted per completed session; overridable so callers can narrow the output.
    emit_event_types: tuple[EventType, ...] = (
        EventType.SESSION_HIGH,
        EventType.SESSION_LOW,
        EventType.SESSION_OPEN,
        EventType.SESSION_CLOSE,
    )
    _cache: dict = field(default_factory=dict, repr=False, compare=False)

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _prepare(frame: pd.DataFrame, timeframe: Timeframe) -> pd.DataFrame:
        """Attach ``close_time`` and sort. All membership and observability logic
        below keys on ``close_time``, never on the open timestamp."""
        if len(frame) == 0:
            return with_close_time(frame, timeframe)
        work = with_close_time(frame, timeframe)
        return work.sort_values("timestamp", kind="mergesort").reset_index(drop=True)

    @staticmethod
    def _members(work: pd.DataFrame, window: SessionWindow) -> pd.DataFrame:
        """Bars FULLY contained in the window.

        Requiring containment (not merely an open inside the window) means a session
        high can never be set by price action that occurred outside it.
        """
        return work.loc[
            (work["timestamp"] >= pd.Timestamp(window.start_utc))
            & (work["close_time"] <= pd.Timestamp(window.end_utc))
        ]

    # -- public API ---------------------------------------------------------

    def windows_for(self, frame: pd.DataFrame, timeframe: Timeframe) -> list[SessionWindow]:
        """Session windows spanning the frame's time range."""
        work = self._prepare(frame, timeframe)
        if len(work) == 0:
            return []
        return resolve_windows(
            self.definitions,
            work["timestamp"].iloc[0].to_pydatetime(),
            work["close_time"].iloc[-1].to_pydatetime(),
        )

    def detect(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[SessionOccurrence]:
        """Completed session occurrences that actually contained bars.

        A window is only reported once it has **fully elapsed within observed data** —
        i.e. the frame extends to or past ``window.end_utc``. Without that rule the
        last, still-open session would appear complete, and batch would disagree with
        streaming replay.
        """
        work = self._prepare(frame, timeframe)
        if len(work) == 0:
            return []

        observed_end = work["close_time"].iloc[-1]
        occurrences: list[SessionOccurrence] = []

        for window in self.windows_for(frame, timeframe):
            if observed_end < pd.Timestamp(window.end_utc):
                # The window has not finished inside the data we can see. Emitting it
                # now would be a claim we cannot support.
                continue

            members = self._members(work, window)
            if len(members) == 0:
                # Weekend, holiday, or a genuinely dark market. No occurrence.
                continue

            highs, lows = members["high"], members["low"]
            # idxmax/idxmin take the FIRST extreme on ties — documented, and the
            # earliest timestamp is the honest one.
            high_row = members.loc[highs.idxmax()]
            low_row = members.loc[lows.idxmin()]

            occurrences.append(
                SessionOccurrence(
                    window=window,
                    symbol=symbol.value,
                    timeframe=timeframe.value,
                    bar_count=len(members),
                    open_price=float(members["open"].iloc[0]),
                    high_price=float(highs.max()),
                    low_price=float(lows.min()),
                    close_price=float(members["close"].iloc[-1]),
                    open_timestamp=members["timestamp"].iloc[0].to_pydatetime(),
                    close_timestamp=members["timestamp"].iloc[-1].to_pydatetime(),
                    high_timestamp=high_row["timestamp"].to_pydatetime(),
                    low_timestamp=low_row["timestamp"].to_pydatetime(),
                )
            )

        logger.info(
            "sessions %s %s: %d occurrence(s) from %d bar(s)",
            symbol.value,
            timeframe.value,
            len(occurrences),
            len(work),
        )
        return occurrences

    def events(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[IctEvent]:
        """Contract events for every completed session occurrence.

        All four event types share ``confirmation_timestamp = window.end_utc``: none of
        them is final until the session is over.

        ``strength`` is the session range in instrument points — a deterministic,
        documented magnitude, not a tuned score.
        """
        instrument = Symbol.from_string(symbol.value) if isinstance(symbol, str) else symbol
        point = instrument.spec.point_value

        events: list[IctEvent] = []
        for occurrence in self.detect(frame, instrument, timeframe):
            confirmation = occurrence.confirmation_timestamp
            strength = occurrence.range_size / point if point else None
            common = {
                "symbol": occurrence.symbol,
                "timeframe": occurrence.timeframe,
                "confirmation_timestamp": confirmation,
                "reference_level": occurrence.open_price,
                "strength": strength,
                "created_timestamp": occurrence.window.start_utc,
                "metadata": {
                    "session": occurrence.window.name,
                    "kind": occurrence.window.kind.value,
                    "local_date": occurrence.window.local_date.isoformat(),
                    "bar_count": occurrence.bar_count,
                    "anomaly": occurrence.window.anomaly.value,
                },
            }

            # A session high is buy-side liquidity resting above; a low is sell-side
            # below. That is the direction convention, documented in docs/ict/sessions.md.
            candidates = {
                EventType.SESSION_HIGH: (
                    Direction.BULLISH,
                    occurrence.high_price,
                    occurrence.high_timestamp,
                ),
                EventType.SESSION_LOW: (
                    Direction.BEARISH,
                    occurrence.low_price,
                    occurrence.low_timestamp,
                ),
                EventType.SESSION_OPEN: (
                    Direction.NEUTRAL,
                    occurrence.open_price,
                    occurrence.open_timestamp,
                ),
                EventType.SESSION_CLOSE: (
                    Direction.NEUTRAL,
                    occurrence.close_price,
                    occurrence.close_timestamp,
                ),
            }
            for event_type in self.emit_event_types:
                direction, level, moment = candidates[event_type]
                events.append(
                    IctEvent(
                        event_type=event_type,
                        direction=direction,
                        event_timestamp=moment,
                        price_level=level,
                        **common,
                    )
                )

        events.sort(key=lambda e: (e.confirmation_timestamp, e.event_timestamp, e.event_type.value))
        return events

    def session_state_at(
        self,
        frame: pd.DataFrame,
        as_of: datetime,
        symbol: Symbol,
        timeframe: Timeframe,
    ) -> dict[str, RunningSessionState]:
        """Point-in-time running state for every window overlapping ``as_of``.

        Strictly observable: only bars with ``close_time <= as_of`` contribute. A bar
        still forming at ``as_of`` cannot influence the running high, because its high
        is not yet known.
        """
        if as_of.tzinfo is None:
            raise ValueError(f"as_of must be timezone-aware UTC; got naive {as_of!r}")

        work = self._prepare(frame, timeframe)
        observable = work.loc[work["close_time"] <= pd.Timestamp(as_of)]

        states: dict[str, RunningSessionState] = {}
        for window in resolve_windows(self.definitions, as_of - timedelta(days=2), as_of + timedelta(days=1)):
            if not (window.start_utc <= as_of or window.end_utc <= as_of):
                continue

            members = self._members(observable, window)
            has_bars = len(members) > 0
            states[window.name] = RunningSessionState(
                window=window,
                as_of=as_of,
                bar_count=len(members),
                open_price=float(members["open"].iloc[0]) if has_bars else None,
                high_price=float(members["high"].max()) if has_bars else None,
                low_price=float(members["low"].min()) if has_bars else None,
                last_price=float(members["close"].iloc[-1]) if has_bars else None,
                is_active=window.contains(as_of),
                is_complete=as_of >= window.end_utc,
            )
        return states

    def active_sessions_at(self, as_of: datetime) -> list[str]:
        """Names of the windows containing ``as_of``. Pure calendar arithmetic — no
        bars needed, so it is usable for labelling before any data is loaded."""
        return [
            w.name
            for w in resolve_windows(self.definitions, as_of - timedelta(days=2), as_of + timedelta(days=1))
            if w.contains(as_of)
        ]

    def with_definitions(self, definitions: tuple[SessionDefinition, ...]) -> SessionDetector:
        """A detector using different session definitions. Configuration, not a
        subclass — session boundaries are data (CLAUDE.md rule 4)."""
        return replace(self, definitions=definitions, _cache={})
