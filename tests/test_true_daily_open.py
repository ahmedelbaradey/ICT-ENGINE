"""R2-05.1 TrueDailyOpenDetector — definition, boundary, timezone and DST.

Leakage and replay live in ``test_true_daily_open_leakage.py``; real data in
``test_true_daily_open_real_data.py``.

Frames are built from an explicit UTC span so every expected boundary can be read off
the calendar rather than re-derived by running the detector inside the assertion.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from ict_kronos.app.config import TrueDailyOpenConfig as TdoSettings
from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame, empty_frame
from ict_kronos.ict import (
    Direction,
    EventType,
    TrueDailyOpen,
    TrueDailyOpenConfig,
    TrueDailyOpenDetector,
    reference_true_daily_opens,
)
from ict_kronos.ict.sessions import BoundaryAnomaly

NY = ZoneInfo("America/New_York")
H1 = Timeframe.H1


def span(start: datetime, hours: int, *, timeframe=H1, symbol=Symbol.EURUSD, skip=()):
    """A contiguous run of bars, each priced off its index so opens are identifiable.

    ``skip`` removes bars at the given UTC instants — how a weekend, a holiday or a
    dataset hole is expressed here.
    """
    candles = []
    for i in range(hours):
        stamp = start + timedelta(minutes=timeframe.minutes * i)
        if stamp in skip:
            continue
        base = 1.1000 + i / 10000
        candles.append(
            MarketCandle(
                timestamp=stamp,
                symbol=symbol,
                timeframe=timeframe,
                open=base,
                high=base + 0.0020,
                low=base - 0.0020,
                close=base + 0.0005,
                volume=1.0,
            )
        )
    return candles_to_frame(candles)


@pytest.fixture
def detector() -> TrueDailyOpenDetector:
    return TrueDailyOpenDetector()


# EST: 2024-03-08 00:00 NY == 2024-03-08 05:00 UTC.
EST_START = datetime(2024, 3, 8, 0, 0, tzinfo=UTC)
EST_BOUNDARY = datetime(2024, 3, 8, 5, 0, tzinfo=UTC)
# EDT: 2024-03-12 00:00 NY == 2024-03-12 04:00 UTC.
EDT_START = datetime(2024, 3, 12, 0, 0, tzinfo=UTC)
EDT_BOUNDARY = datetime(2024, 3, 12, 4, 0, tzinfo=UTC)


class TestTheDefinition:
    def test_one_level_per_valid_ny_date(self, detector):
        levels = detector.detect(span(EST_START, 24), Symbol.EURUSD, H1)
        assert len(levels) == 1
        assert levels[0].trading_date == date(2024, 3, 8)

    def test_the_price_is_the_boundary_bars_open(self, detector):
        frame = span(EST_START, 24)
        levels = detector.detect(frame, Symbol.EURUSD, H1)
        row = frame[frame["timestamp"] == EST_BOUNDARY].iloc[0]

        assert levels[0].price_level == pytest.approx(float(row["open"]))
        # And emphatically not any other price on that bar.
        assert levels[0].price_level != pytest.approx(float(row["high"]))
        assert levels[0].price_level != pytest.approx(float(row["low"]))
        assert levels[0].price_level != pytest.approx(float(row["close"]))

    def test_the_local_time_is_midnight_new_york(self, detector):
        for start in (EST_START, EDT_START):
            for level in detector.detect(span(start, 24), Symbol.EURUSD, H1):
                assert level.local_time().time() == time(0, 0)
                assert level.local_time().tzinfo is NY

    def test_the_boundary_instant_is_the_bars_open_time(self, detector):
        level = detector.detect(span(EST_START, 24), Symbol.EURUSD, H1)[0]
        assert level.event_timestamp == EST_BOUNDARY

    def test_high_low_and_close_are_never_read(self):
        """Source-level: the module must not reference the other OHLC columns."""
        from pathlib import Path

        source = Path("ict_kronos/ict/true_daily_open.py").read_text(encoding="utf-8")
        body = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
        for forbidden in ('"high"', '"low"', '"close"', "'high'", "'low'", "'close'"):
            assert forbidden not in body, f"true_daily_open.py reads {forbidden}"

    def test_the_record_is_immutable(self, detector):
        level = detector.detect(span(EST_START, 24), Symbol.EURUSD, H1)[0]
        with pytest.raises(FrozenInstanceError):
            level.price_level = 0.0  # type: ignore[misc]

    def test_identity_is_symbol_timeframe_and_ny_date(self, detector):
        level = detector.detect(span(EST_START, 24), Symbol.EURUSD, H1)[0]
        assert level.level_id == "tdo:EURUSD:1h:2024-03-08"

    def test_a_later_date_does_not_mutate_an_earlier_one(self, detector):
        levels = detector.detect(span(EST_START, 24 * 2), Symbol.EURUSD, H1)
        assert [level.trading_date for level in levels] == [date(2024, 3, 8), date(2024, 3, 9)]
        assert levels[0].price_level != levels[1].price_level
        assert len({level.level_id for level in levels}) == 2

    def test_records_are_ordered_by_time(self, detector):
        levels = detector.detect(span(EST_START, 24 * 3), Symbol.EURUSD, H1)
        stamps = [level.event_timestamp for level in levels]
        assert stamps == sorted(stamps)

    def test_the_timezone_is_carried_on_the_record(self, detector):
        level = detector.detect(span(EST_START, 24), Symbol.EURUSD, H1)[0]
        assert level.timezone == "America/New_York"

    def test_distance_from_is_signed_from_the_open(self, detector):
        level = detector.detect(span(EST_START, 24), Symbol.EURUSD, H1)[0]
        assert level.distance_from(level.price_level + 0.0010) == pytest.approx(0.0010)
        assert level.distance_from(level.price_level - 0.0010) == pytest.approx(-0.0010)


class TestConfirmation:
    def test_confirmation_equals_the_boundary_instant(self, detector):
        """Zero lag. Justified in docs §4: an open is fixed at the bar's first print."""
        for level in detector.detect(span(EST_START, 24 * 3), Symbol.EURUSD, H1):
            assert level.confirmation_timestamp == level.event_timestamp
            assert level.created_timestamp == level.event_timestamp

    def test_confirmation_does_not_wait_for_the_bar_to_close(self, detector):
        level = detector.detect(span(EST_START, 24), Symbol.EURUSD, H1)[0]
        assert level.confirmation_timestamp < level.event_timestamp + H1.duration

    def test_confirmation_does_not_wait_for_the_day_to_end(self, detector):
        level = detector.detect(span(EST_START, 24), Symbol.EURUSD, H1)[0]
        assert level.confirmation_timestamp < level.event_timestamp + timedelta(days=1)

    def test_all_timestamps_are_timezone_aware(self, detector):
        for level in detector.detect(span(EST_START, 24 * 2), Symbol.EURUSD, H1):
            for stamp in (
                level.event_timestamp,
                level.created_timestamp,
                level.confirmation_timestamp,
            ):
                assert stamp.tzinfo is not None


class TestTimezoneAndDst:
    def test_est_boundary_is_05_utc(self, detector):
        level = detector.detect(span(EST_START, 24), Symbol.EURUSD, H1)[0]
        assert level.event_timestamp == datetime(2024, 3, 8, 5, 0, tzinfo=UTC)

    def test_edt_boundary_is_04_utc(self, detector):
        level = detector.detect(span(EDT_START, 24), Symbol.EURUSD, H1)[0]
        assert level.event_timestamp == datetime(2024, 3, 12, 4, 0, tzinfo=UTC)

    def test_the_spring_transition_shifts_the_utc_hour(self, detector):
        """2024-03-10 02:00 local. Dates on either side must land an hour apart."""
        frame = span(datetime(2024, 3, 8, 0, 0, tzinfo=UTC), 24 * 6)
        by_date = {
            level.trading_date: level.event_timestamp for level in detector.detect(frame, Symbol.EURUSD, H1)
        }

        assert by_date[date(2024, 3, 9)] == datetime(2024, 3, 9, 5, 0, tzinfo=UTC)  # EST
        assert by_date[date(2024, 3, 10)] == datetime(2024, 3, 10, 5, 0, tzinfo=UTC)  # still EST at 00:00
        assert by_date[date(2024, 3, 11)] == datetime(2024, 3, 11, 4, 0, tzinfo=UTC)  # EDT

    def test_the_autumn_transition_shifts_the_utc_hour_back(self, detector):
        """2024-11-03 02:00 local. No real-data coverage — the fixture is in March."""
        frame = span(datetime(2024, 11, 1, 0, 0, tzinfo=UTC), 24 * 5)
        by_date = {
            level.trading_date: level.event_timestamp for level in detector.detect(frame, Symbol.EURUSD, H1)
        }

        assert by_date[date(2024, 11, 2)] == datetime(2024, 11, 2, 4, 0, tzinfo=UTC)  # EDT
        assert by_date[date(2024, 11, 3)] == datetime(2024, 11, 3, 4, 0, tzinfo=UTC)  # still EDT at 00:00
        assert by_date[date(2024, 11, 4)] == datetime(2024, 11, 4, 5, 0, tzinfo=UTC)  # EST

    def test_the_local_invariant_holds_across_both_transitions(self, detector):
        """THE assertion. Never 'the UTC hour is constant' — that passes 8 months a year."""
        for start, days in ((datetime(2024, 3, 8, tzinfo=UTC), 6), (datetime(2024, 11, 1, tzinfo=UTC), 5)):
            levels = detector.detect(span(start, 24 * days), Symbol.EURUSD, H1)
            assert len(levels) == days
            for level in levels:
                assert level.local_time().time() == time(0, 0)

    def test_utc_midnight_is_not_used(self, detector):
        for level in detector.detect(span(EST_START, 24 * 4), Symbol.EURUSD, H1):
            assert level.event_timestamp.hour in (4, 5)
            assert level.event_timestamp.hour != 0

    def test_ny_midnight_carries_no_dst_anomaly(self, detector):
        """Observation, not assumption — US transitions are at 02:00 local."""
        for start, days in ((datetime(2024, 3, 8, tzinfo=UTC), 6), (datetime(2024, 11, 1, tzinfo=UTC), 5)):
            for level in detector.detect(span(start, 24 * days), Symbol.EURUSD, H1):
                assert level.boundary_anomaly is BoundaryAnomaly.NONE

    def test_no_utc_offset_is_hardcoded_in_the_module(self):
        from pathlib import Path

        source = Path("ict_kronos/ict/true_daily_open.py").read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#") and '"""' not in line
        )
        for forbidden in ("timedelta(hours=5)", "timedelta(hours=4)", "utcoffset(", "UTC-5", "UTC-4"):
            assert forbidden not in code, f"true_daily_open.py hardcodes {forbidden}"

    def test_one_level_per_date_with_no_timezone_duplicates(self, detector):
        levels = detector.detect(span(datetime(2024, 3, 8, tzinfo=UTC), 24 * 6), Symbol.EURUSD, H1)
        dates = [level.trading_date for level in levels]
        assert len(dates) == len(set(dates))


class TestMissingBoundaryBars:
    def test_a_missing_boundary_bar_produces_nothing(self, detector):
        frame = span(EST_START, 24, skip=(EST_BOUNDARY,))
        assert detector.detect(frame, Symbol.EURUSD, H1) == []

    def test_a_weekend_produces_no_saturday_or_sunday_level(self, detector):
        """Friday through Monday with the weekend genuinely absent."""
        friday = datetime(2024, 3, 8, 0, 0, tzinfo=UTC)
        closed = {
            friday + timedelta(hours=h)
            for h in range(24 * 4)
            if datetime(2024, 3, 9, tzinfo=UTC)
            <= friday + timedelta(hours=h)
            < datetime(2024, 3, 10, 21, 0, tzinfo=UTC)
        }
        levels = detector.detect(span(friday, 24 * 4, skip=tuple(closed)), Symbol.EURUSD, H1)

        assert [level.trading_date for level in levels] == [date(2024, 3, 8), date(2024, 3, 11)]

    def test_the_sunday_reopen_is_not_substituted_for_sunday_midnight(self, detector):
        """The reopen bar exists and is nowhere near 00:00 NY. It must not be used."""
        friday = datetime(2024, 3, 8, 0, 0, tzinfo=UTC)
        reopen = datetime(2024, 3, 10, 21, 0, tzinfo=UTC)
        closed = {
            friday + timedelta(hours=h)
            for h in range(24 * 4)
            if datetime(2024, 3, 9, tzinfo=UTC) <= friday + timedelta(hours=h) < reopen
        }
        frame = span(friday, 24 * 4, skip=tuple(closed))
        levels = detector.detect(frame, Symbol.EURUSD, H1)

        reopen_open = float(frame[frame["timestamp"] == reopen].iloc[0]["open"])
        assert date(2024, 3, 10) not in {level.trading_date for level in levels}
        assert reopen_open not in {level.price_level for level in levels}

    def test_a_holiday_closure_produces_no_level(self, detector):
        """A holiday is just a date whose boundary bar is absent. No calendar needed."""
        start = datetime(2024, 3, 8, 0, 0, tzinfo=UTC)
        holiday = {start + timedelta(hours=h) for h in range(24, 48)}
        levels = detector.detect(span(start, 24 * 3, skip=tuple(holiday)), Symbol.EURUSD, H1)

        assert date(2024, 3, 9) not in {level.trading_date for level in levels}
        assert len(levels) == 2

    def test_a_late_start_after_midnight_produces_nothing_for_that_date(self, detector):
        """First bar at 06:00 UTC — past the 05:00 boundary. No back-fill."""
        levels = detector.detect(span(datetime(2024, 3, 8, 6, 0, tzinfo=UTC), 12), Symbol.EURUSD, H1)
        assert levels == []

    def test_the_previous_level_is_never_carried_forward(self, detector):
        start = datetime(2024, 3, 8, 0, 0, tzinfo=UTC)
        gap = {datetime(2024, 3, 9, 5, 0, tzinfo=UTC)}
        levels = detector.detect(span(start, 24 * 3, skip=tuple(gap)), Symbol.EURUSD, H1)

        assert [level.trading_date for level in levels] == [date(2024, 3, 8), date(2024, 3, 10)]

    def test_a_reopen_exactly_at_the_boundary_does_produce_a_level(self, detector):
        """Not a special case — the rule is 'a bar starts exactly here', and one does."""
        levels = detector.detect(span(EST_BOUNDARY, 12), Symbol.EURUSD, H1)
        assert len(levels) == 1
        assert levels[0].event_timestamp == EST_BOUNDARY

    def test_an_empty_frame_is_not_an_error(self, detector):
        assert detector.detect(empty_frame(), Symbol.EURUSD, H1) == []


class TestCoarseTimeframes:
    def test_a_straddling_bar_is_not_used(self, detector):
        """4H under EST: the 05:00 boundary falls INSIDE the 04:00 bar. Nothing is emitted."""
        frame = span(datetime(2024, 3, 8, 0, 0, tzinfo=UTC), 6, timeframe=Timeframe.H4)
        assert detector.detect(frame, Symbol.EURUSD, Timeframe.H4) == []

    def test_4h_works_when_the_grid_contains_the_boundary(self, detector):
        """Under EDT the boundary is 04:00 UTC, which IS a 4H grid point."""
        frame = span(datetime(2024, 3, 12, 0, 0, tzinfo=UTC), 6, timeframe=Timeframe.H4)
        levels = detector.detect(frame, Symbol.EURUSD, Timeframe.H4)

        assert len(levels) == 1
        assert levels[0].event_timestamp == datetime(2024, 3, 12, 4, 0, tzinfo=UTC)

    def test_daily_bars_never_contain_the_boundary(self, detector):
        """A UTC-midnight-anchored grid can never land on 00:00 New York."""
        frame = span(datetime(2024, 3, 8, 0, 0, tzinfo=UTC), 5, timeframe=Timeframe.D1)
        assert detector.detect(frame, Symbol.EURUSD, Timeframe.D1) == []

    def test_no_price_is_manufactured_from_the_straddling_bar(self, detector):
        frame = span(datetime(2024, 3, 8, 0, 0, tzinfo=UTC), 6, timeframe=Timeframe.H4)
        straddling = frame[frame["timestamp"] == datetime(2024, 3, 8, 4, 0, tzinfo=UTC)]

        assert len(straddling) == 1  # the bar exists...
        assert detector.detect(frame, Symbol.EURUSD, Timeframe.H4) == []  # ...and is not used


class TestConfiguration:
    def test_the_defaults_are_the_ict_definition(self):
        config = TrueDailyOpenConfig()
        assert config.timezone == "America/New_York"
        assert config.open_local == time(0, 0)

    def test_an_unknown_timezone_is_rejected_loudly(self):
        with pytest.raises(ValueError, match="unknown timezone"):
            TrueDailyOpenConfig(timezone="Mars/Olympus_Mons")

    def test_the_boundary_follows_the_configured_zone(self):
        detector = TrueDailyOpenDetector(TrueDailyOpenConfig(timezone="Europe/London"))
        level = detector.detect(span(EST_START, 24), Symbol.EURUSD, H1)[0]
        # 2024-03-08 is GMT in London, so local midnight is 00:00 UTC.
        assert level.event_timestamp == datetime(2024, 3, 8, 0, 0, tzinfo=UTC)
        assert level.timezone == "Europe/London"

    def test_with_config_returns_a_new_detector(self, detector):
        other = detector.with_config(TrueDailyOpenConfig(timezone="Europe/London"))
        assert detector.config.timezone == "America/New_York"
        assert other.config.timezone == "Europe/London"

    def test_the_app_setting_is_separate_from_the_liquidity_day_boundary(self):
        """R2-04's 17:00 and R2-05.1's 00:00 must never share a default."""
        from ict_kronos.app.config import LiquidityDetectionConfig

        assert TdoSettings().open_local == "00:00"
        assert LiquidityDetectionConfig().day_boundary_local == "17:00"

    def test_the_app_setting_is_env_overridable(self, monkeypatch):
        monkeypatch.setenv("ICT_TRUE_DAILY_OPEN_TIMEZONE", "Europe/London")
        assert TdoSettings.from_env().timezone == "Europe/London"


class TestEvents:
    def test_the_event_type_is_true_daily_open(self, detector):
        events = detector.events(span(EST_START, 24), Symbol.EURUSD, H1)
        assert [e.event_type for e in events] == [EventType.TRUE_DAILY_OPEN]

    def test_the_event_is_directionless(self, detector):
        """An opening price carries no bias of its own; bias is what a consumer derives."""
        events = detector.events(span(EST_START, 24), Symbol.EURUSD, H1)
        assert events[0].direction is Direction.NEUTRAL

    def test_the_event_price_is_the_open(self, detector):
        frame = span(EST_START, 24)
        event = detector.events(frame, Symbol.EURUSD, H1)[0]
        row = frame[frame["timestamp"] == EST_BOUNDARY].iloc[0]
        assert event.price_level == pytest.approx(float(row["open"]))

    def test_the_event_carries_the_ny_date_and_local_time(self, detector):
        event = detector.events(span(EST_START, 24), Symbol.EURUSD, H1)[0]
        assert event.metadata["trading_date"] == "2024-03-08"
        assert event.metadata["local_time"] == "00:00"
        assert event.metadata["timezone"] == "America/New_York"

    def test_event_timestamps_match_the_level(self, detector):
        frame = span(EST_START, 24)
        level = detector.detect(frame, Symbol.EURUSD, H1)[0]
        event = detector.events(frame, Symbol.EURUSD, H1)[0]

        assert event.event_timestamp == level.event_timestamp
        assert event.confirmation_timestamp == level.confirmation_timestamp
        assert event.confirmation_lag == timedelta(0)

    def test_the_contract_declares_the_event_type(self):
        assert EventType.TRUE_DAILY_OPEN.value == "true_daily_open"
        assert EventType.TRUE_DAILY_OPEN is not EventType.SESSION_OPEN
        assert EventType.TRUE_DAILY_OPEN not in {
            EventType.PREVIOUS_DAY_HIGH,
            EventType.PREVIOUS_DAY_LOW,
        }

    def test_as_dict_round_trips_the_essentials(self, detector):
        level = detector.detect(span(EST_START, 24), Symbol.EURUSD, H1)[0]
        payload = level.as_dict()

        assert payload["trading_date"] == "2024-03-08"
        assert payload["confirmation_timestamp"] == payload["event_timestamp"]
        assert payload["timezone"] == "America/New_York"


class TestLatestAt:
    def test_none_before_the_first_boundary(self, detector):
        frame = span(EST_START, 24)
        assert detector.latest_at(frame, EST_BOUNDARY - timedelta(seconds=1), Symbol.EURUSD, H1) is None

    def test_the_level_at_the_boundary_instant(self, detector):
        frame = span(EST_START, 24)
        level = detector.latest_at(frame, EST_BOUNDARY, Symbol.EURUSD, H1)
        assert level is not None and level.trading_date == date(2024, 3, 8)

    def test_the_most_recent_of_several(self, detector):
        frame = span(EST_START, 24 * 3)
        as_of = datetime(2024, 3, 10, 12, 0, tzinfo=UTC)
        level = detector.latest_at(frame, as_of, Symbol.EURUSD, H1)
        assert level is not None and level.trading_date == date(2024, 3, 10)

    def test_a_stale_level_stays_labelled_with_its_own_date(self, detector):
        """``latest_at`` is "most recent", NOT "today's".

        On a date with no boundary bar it returns the previous date's level — the
        honest answer to the question asked. What must never happen is relabelling:
        the record keeps its own ``trading_date`` so the caller can see the staleness
        and decide. Detection itself still carries nothing forward.
        """
        start = datetime(2024, 3, 8, 0, 0, tzinfo=UTC)
        frame = span(start, 24 * 2, skip=(datetime(2024, 3, 9, 5, 0, tzinfo=UTC),))
        as_of = datetime(2024, 3, 9, 6, 0, tzinfo=UTC)

        level = detector.latest_at(frame, as_of, Symbol.EURUSD, H1)
        assert level is not None
        assert level.trading_date == date(2024, 3, 8)  # yesterday's, and says so
        assert level.trading_date != as_of.astimezone(NY).date()
        # No record was invented for the missing date.
        assert [lvl.trading_date for lvl in detector.detect(frame, Symbol.EURUSD, H1)] == [date(2024, 3, 8)]


class TestReferenceImplementation:
    def test_the_vectorised_detector_matches_the_naive_reference(self, detector):
        frame = span(datetime(2024, 3, 8, tzinfo=UTC), 24 * 6)
        levels = detector.detect(frame, Symbol.EURUSD, H1)
        reference = reference_true_daily_opens(frame, TrueDailyOpenConfig())

        assert [(level.trading_date, level.price_level) for level in levels] == reference

    def test_they_agree_when_bars_are_missing(self, detector):
        start = datetime(2024, 3, 8, tzinfo=UTC)
        frame = span(start, 24 * 4, skip=(datetime(2024, 3, 9, 5, 0, tzinfo=UTC),))

        levels = detector.detect(frame, Symbol.EURUSD, H1)
        reference = reference_true_daily_opens(frame, TrueDailyOpenConfig())
        assert [(level.trading_date, level.price_level) for level in levels] == reference

    def test_they_agree_on_an_empty_frame(self):
        assert reference_true_daily_opens(empty_frame(), TrueDailyOpenConfig()) == []


class TestUnsortedInput:
    def test_shuffled_bars_produce_the_same_result(self, detector):
        frame = span(EST_START, 24 * 2)
        shuffled = frame.iloc[::-1].reset_index(drop=True)

        assert detector.detect(frame, Symbol.EURUSD, H1) == detector.detect(shuffled, Symbol.EURUSD, H1)


class TestTypeSanity:
    def test_detect_returns_true_daily_open_records(self, detector):
        levels = detector.detect(span(EST_START, 24), Symbol.EURUSD, H1)
        assert all(isinstance(level, TrueDailyOpen) for level in levels)

    def test_trading_date_is_a_date_not_a_datetime(self, detector):
        level = detector.detect(span(EST_START, 24), Symbol.EURUSD, H1)[0]
        assert isinstance(level.trading_date, date)
        assert not isinstance(level.trading_date, datetime)
