"""True Daily Open — the opening price at 00:00 America/New_York.

Definitions, the distinction from the 17:00 trading-day boundary, and every ambiguity
we did NOT silently resolve are in ``docs/ict/true_daily_open.md``. Read that first.

**The definition, in full.**

    TRUE_DAILY_OPEN = 00:00 America/New_York
                    = the OPEN price of the bar that begins exactly at that instant

Nothing else. Not the high, low or close; not the first bar after a weekend; not a
price derived from any other bar. If no bar starts exactly at the boundary, **no level
is produced** — a missing True Daily Open is information, and manufacturing one would
turn a known unknown into a plausible-looking lie.

**This is NOT the trading-day boundary.** R2-04 uses 17:00 America/New_York to
delimit trading days for ``previous_day_high`` / ``previous_day_low``. That boundary is
a *period delimiter*; this is a *price level*. They are different concepts that happen
to both be daily, they are never interchangeable, and this module changes nothing about
R2-04. They also disagree about date labels: at 20:00 NY on Monday the trading day is
already Tuesday's, while the True Daily Open in force is still Monday's.

**Confirmation lag is exactly zero, and that is the correct answer.** Every other
Phase 2 detector confirms at a bar's ``close_time`` because every other detector reads
a price that is not final until the bar closes — an FVG reads candle 3's low, a sweep
reads the sweeping bar's extreme. An *open* is fixed at the bar's first print. Waiting
for the close would be the opposite of the ForexQuant error: not leaking the future,
but discarding present information and publishing a level a full bar late.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from ..app.logging import get_logger
from ..domain import Symbol, Timeframe
from .contract import Direction, EventType, IctEvent, filter_observable, is_observable_at

# The single DST conversion of record, shared with every R2-01 session boundary.
# Imported rather than reimplemented: a second copy of the PEP-495 fold handling is
# exactly the kind of quiet divergence this codebase keeps one gate for.
from .sessions import BoundaryAnomaly, _local_to_utc

logger = get_logger(__name__)


@dataclass(frozen=True)
class TrueDailyOpenConfig:
    """Boundary configuration. The defaults ARE the ICT definition.

    Expressed as configuration rather than literals in logic (CLAUDE.md rule 4), but
    changing them changes what the level means — this is not a tuning knob.
    """

    timezone: str = "America/New_York"
    open_local: time = time(0, 0)

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except Exception as exc:  # noqa: BLE001 - re-raised as a config error
            raise ValueError(f"unknown timezone {self.timezone!r} for the true daily open") from exc

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def as_dict(self) -> dict:
        return {
            "timezone": self.timezone,
            "open_local": self.open_local.strftime("%H:%M"),
        }


@dataclass(frozen=True)
class TrueDailyOpen:
    """One day's True Daily Open. Immutable.

    Identity is ``(symbol, timeframe, trading_date)``. No positional dataframe index
    is stored — not as a convenience omitted, but so that one can never accidentally
    become a cross-timeframe join key.
    """

    level_id: str
    symbol: str
    timeframe: str
    #: The NEW YORK calendar date, never the UTC date. During EDT the boundary is
    #: 04:00Z on the same UTC date, but the two can diverge for other zones.
    trading_date: date
    #: The zone the boundary was defined in — carried so a consumer never has to
    #: assume which local midnight this was.
    timezone: str
    #: The boundary bar's OPEN. Never its high, low or close.
    price_level: float
    #: The 00:00 local boundary instant, in UTC. Equal to the bar's open time.
    event_timestamp: datetime
    created_timestamp: datetime
    #: The same instant — an open price is knowable when the bar opens (module docs).
    confirmation_timestamp: datetime
    #: What DST did to this local midnight. Always ``NONE`` under America/New_York,
    #: whose transitions occur at 02:00 local — recorded rather than assumed, because
    #: the zone is configuration and midnight transitions are real elsewhere (Brazil,
    #: Chile). See docs §2.1.
    boundary_anomaly: BoundaryAnomaly = BoundaryAnomaly.NONE

    def is_observable_at(self, as_of: datetime) -> bool:
        """Delegates to the ONE contract-level predicate — never a private copy."""
        return is_observable_at(self, as_of)

    def local_time(self) -> datetime:
        """The boundary rendered back in its own zone. Should always read 00:00."""
        return self.event_timestamp.astimezone(ZoneInfo(self.timezone))

    def distance_from(self, price: float) -> float:
        """Signed distance of ``price`` from the open: positive above, negative below.

        Provided so R2-07 never re-derives it and never gets the sign backwards.
        """
        return price - self.price_level

    def as_dict(self) -> dict:
        return {
            "level_id": self.level_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "trading_date": self.trading_date.isoformat(),
            "timezone": self.timezone,
            "price_level": self.price_level,
            "event_timestamp": self.event_timestamp.isoformat(),
            "created_timestamp": self.created_timestamp.isoformat(),
            "confirmation_timestamp": self.confirmation_timestamp.isoformat(),
            "boundary_anomaly": self.boundary_anomaly.value,
        }


@dataclass(frozen=True)
class TrueDailyOpenDetector:
    """Deterministic True Daily Open detection over a canonical candle frame."""

    config: TrueDailyOpenConfig = TrueDailyOpenConfig()

    # ------------------------------------------------------------------ core

    def boundary_for(self, local_day: date) -> tuple[datetime, BoundaryAnomaly]:
        """The UTC instant of ``open_local`` on ``local_day``, and its DST anomaly.

        The only place a boundary is computed. Note what is absent: any UTC offset.
        ``05:00Z`` and ``04:00Z`` are outputs of this function on particular dates,
        never inputs to it.
        """
        naive = datetime.combine(local_day, self.config.open_local)
        return _local_to_utc(naive, self.config.zone)

    def _local_dates(self, frame: pd.DataFrame) -> list[date]:
        """Every local date whose boundary could fall inside the observed span.

        A one-day margin on each side because the local date of an instant differs
        from its UTC date, and clipping at the UTC edges would silently drop the
        first or last boundary.
        """
        zone = self.config.zone
        first = frame["timestamp"].iloc[0].to_pydatetime().astimezone(zone).date() - timedelta(days=1)
        last = frame["timestamp"].iloc[-1].to_pydatetime().astimezone(zone).date() + timedelta(days=1)

        days: list[date] = []
        day = first
        while day <= last:
            days.append(day)
            day += timedelta(days=1)
        return days

    def detect(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[TrueDailyOpen]:
        """Every True Daily Open with an exact boundary bar in the observed data.

        Exact match or nothing. The lookup reads a single row per date and never
        consults a neighbour, which is what makes batch detection identical to
        streaming replay: there is no window to warm up and no state to carry.
        """
        if len(frame) == 0:
            # No data is not an error; it is simply too early.
            return []

        work = frame.sort_values("timestamp", kind="mergesort").reset_index(drop=True)

        boundaries: list[datetime] = []
        anomalies: list[BoundaryAnomaly] = []
        days: list[date] = []
        for local_day in self._local_dates(work):
            instant, anomaly = self.boundary_for(local_day)
            boundaries.append(instant)
            anomalies.append(anomaly)
            days.append(local_day)

        # Vectorised exact lookup: reindex onto the boundary instants. Rows that do
        # not exist come back as NaN, which IS the weekend/holiday/missing-bar answer
        # rather than something to be repaired.
        opens = (
            work.set_index("timestamp")["open"]
            .reindex(pd.DatetimeIndex(boundaries, tz="UTC"))
            .to_numpy(dtype="float64")
        )

        levels: list[TrueDailyOpen] = []
        for local_day, instant, anomaly, open_price in zip(days, boundaries, anomalies, opens, strict=True):
            if pd.isna(open_price):
                # No bar starts exactly here. Market shut, dataset hole, or a grid
                # that does not contain the boundary (4H under EST). Never
                # substituted, never interpolated, never carried forward — docs §3.1.
                continue

            levels.append(
                TrueDailyOpen(
                    level_id=f"tdo:{symbol.value}:{timeframe.value}:{local_day.isoformat()}",
                    symbol=symbol.value,
                    timeframe=timeframe.value,
                    trading_date=local_day,
                    timezone=self.config.timezone,
                    price_level=float(open_price),
                    event_timestamp=instant,
                    created_timestamp=instant,
                    confirmation_timestamp=instant,
                    boundary_anomaly=anomaly,
                )
            )

        levels.sort(key=lambda level: level.event_timestamp)
        return levels

    # ------------------------------------------------------------- consumers

    def events(self, frame: pd.DataFrame, symbol: Symbol, timeframe: Timeframe) -> list[IctEvent]:
        """Contract events, one per detected level.

        ``Direction.NEUTRAL`` is the true answer, not a fallback: an opening price
        carries no directional bias of its own. Bias is what a *consumer* derives by
        comparing price to it, which is R2-07's job.
        """
        return [
            IctEvent(
                symbol=level.symbol,
                timeframe=level.timeframe,
                event_type=EventType.TRUE_DAILY_OPEN,
                direction=Direction.NEUTRAL,
                event_timestamp=level.event_timestamp,
                confirmation_timestamp=level.confirmation_timestamp,
                price_level=level.price_level,
                created_timestamp=level.created_timestamp,
                metadata={
                    "level_id": level.level_id,
                    "trading_date": level.trading_date.isoformat(),
                    "timezone": level.timezone,
                    "local_time": level.local_time().strftime("%H:%M"),
                    "boundary_anomaly": level.boundary_anomaly.value,
                    **self.config.as_dict(),
                },
            )
            for level in self.detect(frame, symbol, timeframe)
        ]

    def observable_at(
        self, frame: pd.DataFrame, as_of: datetime, symbol: Symbol, timeframe: Timeframe
    ) -> list[TrueDailyOpen]:
        """The True Daily Opens a decision at ``as_of`` may use."""
        return filter_observable(self.detect(frame, symbol, timeframe), as_of)

    def latest_at(
        self, frame: pd.DataFrame, as_of: datetime, symbol: Symbol, timeframe: Timeframe
    ) -> TrueDailyOpen | None:
        """The most recent observable True Daily Open, or ``None`` before the first.

        **This is "most recent", not "today's".** On a date whose boundary bar is
        missing it returns the previous date's level, which is the honest answer to
        the question asked — but the caller must check ``trading_date`` before
        treating it as current. That is deliberately the caller's decision: the record
        is always labelled with the date it actually belongs to, so staleness is
        visible rather than silently laundered. Detection itself never carries a
        price forward (docs §3.1); this is a query convenience over what was found.
        """
        visible = self.observable_at(frame, as_of, symbol, timeframe)
        return visible[-1] if visible else None

    def with_config(self, config: TrueDailyOpenConfig) -> TrueDailyOpenDetector:
        return replace(self, config=config)


def reference_true_daily_opens(frame: pd.DataFrame, config: TrueDailyOpenConfig) -> list[tuple[date, float]]:
    """Deliberately simple reference implementation, for testing only.

    A plain Python loop with no reindexing, no numpy and no vectorisation — so its
    correctness is obvious by inspection. The tests assert the real detector agrees
    with it on real data. **Never call this on a full history.**
    """
    zone = config.zone
    rows = frame.sort_values("timestamp", kind="mergesort")
    by_timestamp = {row.timestamp.to_pydatetime(): float(row.open) for row in rows.itertuples(index=False)}

    if not by_timestamp:
        return []

    stamps = sorted(by_timestamp)
    day = stamps[0].astimezone(zone).date() - timedelta(days=1)
    last = stamps[-1].astimezone(zone).date() + timedelta(days=1)

    found: list[tuple[date, float]] = []
    while day <= last:
        instant = datetime.combine(day, config.open_local).replace(tzinfo=zone).astimezone(stamps[0].tzinfo)
        if instant in by_timestamp:
            found.append((day, by_timestamp[instant]))
        day += timedelta(days=1)

    return found
