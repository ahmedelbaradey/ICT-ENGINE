"""R2-01 SessionDetector — boundaries, DST, midnight crossing, confirmation, leakage.

The premise: sessions are defined in *local* time and resolved to UTC, so DST moves
the UTC boundary automatically. Bars are never converted; only boundaries are computed.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame
from ict_kronos.ict import (
    DEFAULT_SESSIONS,
    BoundaryAnomaly,
    Direction,
    EventType,
    SessionDefinition,
    SessionDetector,
    SessionKind,
    load_definitions,
    resolve_window,
    resolve_windows,
)

# 2024 DST transitions as UTC instants.
US_DST_START = datetime(2024, 3, 10, 7, 0, tzinfo=UTC)
EU_DST_START = datetime(2024, 3, 31, 1, 0, tzinfo=UTC)

# A simple single-session config used wherever the five defaults would only add noise.
ONE_HOUR_UTC = (SessionDefinition("test", "UTC", time(9, 0), time(10, 0)),)


def bars(
    start: datetime,
    count: int,
    *,
    timeframe: Timeframe = Timeframe.M5,
    symbol: Symbol = Symbol.EURUSD,
    base: float = 1.0800,
    step: float = 0.0001,
) -> pd.DataFrame:
    """Contiguous rising bars — extremes are then trivially checkable by hand."""
    candles = []
    for i in range(count):
        open_ = base + i * step
        close = open_ + step / 2
        candles.append(
            MarketCandle(
                timestamp=start + timedelta(minutes=timeframe.minutes * i),
                symbol=symbol,
                timeframe=timeframe,
                open=open_,
                high=close + step / 4,
                low=open_ - step / 4,
                close=close,
                volume=10.0,
            )
        )
    return candles_to_frame(candles)


# ---------------------------------------------------------------- definitions


class TestSessionDefinition:
    def test_defaults_cover_the_required_sessions_and_kill_zones(self):
        names = {d.name for d in DEFAULT_SESSIONS}
        assert {"asian", "london", "new_york"} <= names
        assert {"london_kill_zone", "new_york_kill_zone"} <= names

    def test_kill_zones_are_first_class_windows(self):
        kill_zones = [d for d in DEFAULT_SESSIONS if d.kind is SessionKind.KILL_ZONE]
        assert len(kill_zones) == 2

    def test_definitions_are_local_time_never_utc(self):
        """The whole DST design rests on this: no default is anchored to UTC."""
        assert all(d.timezone != "UTC" for d in DEFAULT_SESSIONS)

    def test_crosses_midnight_detection(self):
        assert SessionDefinition("x", "UTC", time(22, 0), time(4, 0)).crosses_midnight
        assert not SessionDefinition("y", "UTC", time(8, 0), time(16, 0)).crosses_midnight

    def test_definitions_are_immutable(self):
        with pytest.raises(Exception):  # noqa: B017 - FrozenInstanceError
            DEFAULT_SESSIONS[0].name = "changed"


class TestLoadDefinitions:
    def test_none_yields_documented_defaults(self):
        assert load_definitions(None) == DEFAULT_SESSIONS
        assert load_definitions("   ") == DEFAULT_SESSIONS

    def test_inline_json_override(self):
        spec = '[{"name":"tokyo","timezone":"Asia/Tokyo","start_local":"09:00","end_local":"15:00"}]'
        definitions = load_definitions(spec)
        assert [d.name for d in definitions] == ["tokyo"]
        assert definitions[0].start_local == time(9, 0)

    def test_json_file_override(self, tmp_path):
        path = tmp_path / "sessions.json"
        path.write_text(
            '[{"name":"a","timezone":"UTC","start_local":"01:00","end_local":"02:00","kind":"kill_zone"}]',
            encoding="utf-8",
        )
        definitions = load_definitions(str(path))
        assert definitions[0].kind is SessionKind.KILL_ZONE

    def test_seconds_precision_is_accepted(self):
        spec = '[{"name":"a","timezone":"UTC","start_local":"01:00:30","end_local":"02:00:00"}]'
        assert load_definitions(spec)[0].start_local == time(1, 0, 30)

    @pytest.mark.parametrize(
        ("spec", "match"),
        [
            ("not json at all", "neither inline JSON nor an existing file"),
            ("[}", "not valid JSON"),
            ("[]", "non-empty JSON array"),
            (
                '[{"name":"a","timezone":"Nowhere/Bad","start_local":"01:00","end_local":"02:00"}]',
                "unknown timezone",
            ),
            ('[{"name":"a","timezone":"UTC","start_local":"9am","end_local":"02:00"}]', "invalid"),
            ('[{"timezone":"UTC","start_local":"01:00","end_local":"02:00"}]', "invalid"),
        ],
    )
    def test_bad_config_fails_loudly(self, spec, match):
        """A misconfiguration must surface at startup, never silently fall back to
        defaults and produce quietly wrong sessions."""
        with pytest.raises(ValueError, match=match):
            load_definitions(spec)

    def test_duplicate_names_are_rejected(self):
        spec = (
            '[{"name":"a","timezone":"UTC","start_local":"01:00","end_local":"02:00"},'
            '{"name":"a","timezone":"UTC","start_local":"03:00","end_local":"04:00"}]'
        )
        with pytest.raises(ValueError, match="duplicate session names"):
            load_definitions(spec)


# ------------------------------------------------------------ window resolution


class TestWindowResolution:
    def test_simple_utc_window(self):
        window = resolve_window(ONE_HOUR_UTC[0], date(2024, 3, 8))
        assert window.start_utc == datetime(2024, 3, 8, 9, tzinfo=UTC)
        assert window.end_utc == datetime(2024, 3, 8, 10, tzinfo=UTC)
        assert window.anomaly is BoundaryAnomaly.NONE

    def test_local_time_converts_to_utc(self):
        tokyo = SessionDefinition("asian", "Asia/Tokyo", time(9, 0), time(18, 0))
        window = resolve_window(tokyo, date(2024, 3, 8))
        # JST is UTC+9 year-round.
        assert window.start_utc == datetime(2024, 3, 8, 0, tzinfo=UTC)
        assert window.end_utc == datetime(2024, 3, 8, 9, tzinfo=UTC)

    def test_window_crossing_midnight_spans_two_local_days(self):
        overnight = SessionDefinition("overnight", "UTC", time(22, 0), time(4, 0))
        window = resolve_window(overnight, date(2024, 3, 8))
        assert window.start_utc == datetime(2024, 3, 8, 22, tzinfo=UTC)
        assert window.end_utc == datetime(2024, 3, 9, 4, tzinfo=UTC)
        assert window.duration == timedelta(hours=6)

    def test_occurrence_is_anchored_to_the_start_local_date(self):
        overnight = SessionDefinition("overnight", "UTC", time(22, 0), time(4, 0))
        assert resolve_window(overnight, date(2024, 3, 8)).local_date == date(2024, 3, 8)

    def test_windows_are_half_open(self):
        window = resolve_window(ONE_HOUR_UTC[0], date(2024, 3, 8))
        assert window.contains(datetime(2024, 3, 8, 9, tzinfo=UTC))
        assert window.contains(datetime(2024, 3, 8, 9, 59, tzinfo=UTC))
        assert not window.contains(datetime(2024, 3, 8, 10, tzinfo=UTC))

    def test_resolve_windows_filters_to_the_requested_range(self):
        windows = resolve_windows(
            ONE_HOUR_UTC,
            datetime(2024, 3, 8, tzinfo=UTC),
            datetime(2024, 3, 11, tzinfo=UTC),
        )
        assert [w.local_date for w in windows] == [date(2024, 3, 8), date(2024, 3, 9), date(2024, 3, 10)]

    def test_resolve_windows_rejects_naive_bounds(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            resolve_windows(ONE_HOUR_UTC, datetime(2024, 3, 8), datetime(2024, 3, 9))  # noqa: DTZ001

    def test_windows_are_sorted(self):
        windows = resolve_windows(
            DEFAULT_SESSIONS, datetime(2024, 3, 11, tzinfo=UTC), datetime(2024, 3, 12, tzinfo=UTC)
        )
        starts = [w.start_utc for w in windows]
        assert starts == sorted(starts)


# ----------------------------------------------------------------------- DST


class TestDaylightSaving:
    def test_london_utc_boundary_moves_across_eu_dst(self):
        """GMT 08:00 local = 08:00 UTC; BST 08:00 local = 07:00 UTC. The UTC boundary
        moves by itself because the definition is local, not UTC."""
        london = next(d for d in DEFAULT_SESSIONS if d.name == "london")

        before = resolve_window(london, date(2024, 3, 25))
        after = resolve_window(london, date(2024, 4, 1))

        assert before.start_utc.hour == 8
        assert after.start_utc.hour == 7

    def test_new_york_utc_boundary_moves_across_us_dst(self):
        """EST 08:00 local = 13:00 UTC; EDT 08:00 local = 12:00 UTC."""
        ny = next(d for d in DEFAULT_SESSIONS if d.name == "new_york")

        before = resolve_window(ny, date(2024, 3, 8))
        after = resolve_window(ny, date(2024, 3, 11))

        assert before.start_utc.hour == 13
        assert after.start_utc.hour == 12

    def test_kill_zone_boundary_moves_too(self):
        kz = next(d for d in DEFAULT_SESSIONS if d.name == "new_york_kill_zone")
        assert resolve_window(kz, date(2024, 3, 8)).start_utc.hour == 12
        assert resolve_window(kz, date(2024, 3, 11)).start_utc.hour == 11

    def test_tokyo_never_moves(self):
        """Japan has no DST — a control that proves the movement above is DST and not
        an artefact of the conversion code."""
        asian = next(d for d in DEFAULT_SESSIONS if d.name == "asian")
        assert resolve_window(asian, date(2024, 3, 8)).start_utc.hour == 0
        assert resolve_window(asian, date(2024, 7, 8)).start_utc.hour == 0

    def test_session_duration_is_preserved_across_a_transition(self):
        london = next(d for d in DEFAULT_SESSIONS if d.name == "london")
        for day in (date(2024, 3, 25), date(2024, 4, 1)):
            assert resolve_window(london, day).duration == timedelta(hours=8, minutes=30)

    def test_nonexistent_local_time_is_reported_not_swallowed(self):
        """02:30 America/New_York does not exist on 2024-03-10 (spring forward)."""
        spring = SessionDefinition("spring", "America/New_York", time(2, 30), time(4, 0))
        window = resolve_window(spring, date(2024, 3, 10))
        assert window.anomaly is BoundaryAnomaly.NONEXISTENT

    def test_ambiguous_local_time_is_reported_not_swallowed(self):
        """01:30 America/New_York occurs twice on 2024-11-03 (fall back)."""
        fall = SessionDefinition("fall", "America/New_York", time(1, 30), time(3, 0))
        window = resolve_window(fall, date(2024, 11, 3))
        assert window.anomaly is BoundaryAnomaly.AMBIGUOUS

    def test_ordinary_days_report_no_anomaly(self):
        ny = next(d for d in DEFAULT_SESSIONS if d.name == "new_york")
        assert resolve_window(ny, date(2024, 3, 8)).anomaly is BoundaryAnomaly.NONE

    def test_local_transition_is_real(self):
        """Guards against the test dates going stale and passing vacuously."""
        zone = ZoneInfo("America/New_York")
        before = (US_DST_START - timedelta(minutes=1)).astimezone(zone)
        after = (US_DST_START + timedelta(minutes=1)).astimezone(zone)
        assert before.utcoffset() != after.utcoffset()


# ------------------------------------------------------------------ detection


class TestDetection:
    def test_detects_a_session_containing_bars(self):
        detector = SessionDetector(definitions=ONE_HOUR_UTC)
        # 09:00-10:00 window; bars 08:00-11:00 so the window is fully elapsed.
        frame = bars(datetime(2024, 3, 8, 8, tzinfo=UTC), 36)

        occurrences = detector.detect(frame, Symbol.EURUSD, Timeframe.M5)
        assert len(occurrences) == 1
        assert occurrences[0].bar_count == 12

    def test_extremes_come_only_from_in_window_bars(self):
        detector = SessionDetector(definitions=ONE_HOUR_UTC)
        frame = bars(datetime(2024, 3, 8, 8, tzinfo=UTC), 36)
        occurrence = detector.detect(frame, Symbol.EURUSD, Timeframe.M5)[0]

        in_window = frame.loc[
            (frame["timestamp"] >= pd.Timestamp("2024-03-08T09:00:00Z"))
            & (frame["timestamp"] < pd.Timestamp("2024-03-08T10:00:00Z"))
        ]
        assert occurrence.high_price == pytest.approx(in_window["high"].max())
        assert occurrence.low_price == pytest.approx(in_window["low"].min())
        assert occurrence.open_price == pytest.approx(in_window["open"].iloc[0])
        assert occurrence.close_price == pytest.approx(in_window["close"].iloc[-1])

    def test_a_window_with_no_bars_yields_no_occurrence(self):
        """Weekend/holiday: absence is preserved, never fabricated."""
        detector = SessionDetector(definitions=ONE_HOUR_UTC)
        # Bars only 11:00-13:00; the 09:00 window is empty but fully elapsed.
        frame = bars(datetime(2024, 3, 8, 11, tzinfo=UTC), 24)
        assert detector.detect(frame, Symbol.EURUSD, Timeframe.M5) == []

    def test_a_window_not_yet_elapsed_is_not_emitted(self):
        """Otherwise the still-open session would look complete and batch would
        disagree with streaming replay."""
        detector = SessionDetector(definitions=ONE_HOUR_UTC)
        frame = bars(datetime(2024, 3, 8, 9, tzinfo=UTC), 6)  # 09:00-09:30 only
        assert detector.detect(frame, Symbol.EURUSD, Timeframe.M5) == []

    def test_partial_bars_straddling_a_boundary_are_excluded(self):
        """A session high must never be set by price action outside the session."""
        detector = SessionDetector(definitions=ONE_HOUR_UTC)
        # Hourly bars: 09:00 bar closes exactly at 10:00 -> contained. 08:00 is not.
        frame = bars(datetime(2024, 3, 8, 6, tzinfo=UTC), 8, timeframe=Timeframe.H1)

        occurrence = detector.detect(frame, Symbol.EURUSD, Timeframe.H1)[0]
        assert occurrence.bar_count == 1
        assert occurrence.open_timestamp == datetime(2024, 3, 8, 9, tzinfo=UTC)

    def test_empty_frame_yields_nothing(self):
        detector = SessionDetector(definitions=ONE_HOUR_UTC)
        assert detector.detect(bars(datetime(2024, 3, 8, tzinfo=UTC), 0), Symbol.EURUSD, Timeframe.M5) == []

    def test_multiple_days_produce_multiple_occurrences(self):
        detector = SessionDetector(definitions=ONE_HOUR_UTC)
        frame = bars(datetime(2024, 3, 8, 8, tzinfo=UTC), 3 * 288, timeframe=Timeframe.M5)
        occurrences = detector.detect(frame, Symbol.EURUSD, Timeframe.M5)
        assert len(occurrences) == 3
        assert [o.window.local_date for o in occurrences] == [
            date(2024, 3, 8),
            date(2024, 3, 9),
            date(2024, 3, 10),
        ]

    def test_first_extreme_wins_on_ties(self):
        """Documented tie behaviour: the earliest timestamp is the honest one."""
        detector = SessionDetector(definitions=ONE_HOUR_UTC)
        flat = []
        for i in range(24):
            ts = datetime(2024, 3, 8, 8, tzinfo=UTC) + timedelta(minutes=5 * i)
            flat.append(
                MarketCandle(
                    timestamp=ts,
                    symbol=Symbol.EURUSD,
                    timeframe=Timeframe.M5,
                    open=1.08,
                    high=1.081,
                    low=1.079,
                    close=1.08,
                    volume=1.0,
                )
            )
        occurrence = detector.detect(candles_to_frame(flat), Symbol.EURUSD, Timeframe.M5)[0]
        assert occurrence.high_timestamp == datetime(2024, 3, 8, 9, tzinfo=UTC)

    def test_active_sessions_at_needs_no_bars(self):
        detector = SessionDetector()
        active = detector.active_sessions_at(datetime(2024, 3, 11, 13, tzinfo=UTC))
        assert "new_york" in active
        assert "new_york_kill_zone" in active


# --------------------------------------------------------------------- events


class TestEvents:
    @pytest.fixture
    def events(self):
        detector = SessionDetector(definitions=ONE_HOUR_UTC)
        frame = bars(datetime(2024, 3, 8, 8, tzinfo=UTC), 36)
        return detector.events(frame, Symbol.EURUSD, Timeframe.M5)

    def test_emits_the_four_session_event_types(self, events):
        assert {e.event_type for e in events} == {
            EventType.SESSION_HIGH,
            EventType.SESSION_LOW,
            EventType.SESSION_OPEN,
            EventType.SESSION_CLOSE,
        }

    def test_confirmation_is_the_session_end_not_the_extreme_bar(self, events):
        """THE core R2-01 semantic. At the instant the high printed you could not
        know a later in-window bar would not exceed it."""
        high = next(e for e in events if e.event_type is EventType.SESSION_HIGH)
        assert high.confirmation_timestamp == datetime(2024, 3, 8, 10, tzinfo=UTC)
        assert high.event_timestamp < high.confirmation_timestamp

    def test_all_four_share_the_session_end_confirmation(self, events):
        assert {e.confirmation_timestamp for e in events} == {datetime(2024, 3, 8, 10, tzinfo=UTC)}

    def test_direction_convention(self, events):
        """Session high = buy-side liquidity above; session low = sell-side below."""
        by_type = {e.event_type: e for e in events}
        assert by_type[EventType.SESSION_HIGH].direction is Direction.BULLISH
        assert by_type[EventType.SESSION_LOW].direction is Direction.BEARISH
        assert by_type[EventType.SESSION_OPEN].direction is Direction.NEUTRAL

    def test_contract_fields_are_populated(self, events):
        for event in events:
            assert event.symbol == "EURUSD"
            assert event.timeframe == "5m"
            assert event.reference_level is not None
            assert event.strength is not None
            assert event.created_timestamp is not None

    def test_strength_is_the_range_in_points(self, events):
        high = next(e for e in events if e.event_type is EventType.SESSION_HIGH)
        low = next(e for e in events if e.event_type is EventType.SESSION_LOW)
        expected = (high.price_level - low.price_level) / Symbol.EURUSD.spec.point_value
        assert high.strength == pytest.approx(expected)

    def test_metadata_names_the_session(self, events):
        assert all(e.metadata["session"] == "test" for e in events)

    def test_emit_types_are_configurable(self):
        detector = SessionDetector(definitions=ONE_HOUR_UTC, emit_event_types=(EventType.SESSION_HIGH,))
        frame = bars(datetime(2024, 3, 8, 8, tzinfo=UTC), 36)
        events = detector.events(frame, Symbol.EURUSD, Timeframe.M5)
        assert {e.event_type for e in events} == {EventType.SESSION_HIGH}


# ------------------------------------------------------------- running state


class TestRunningState:
    def test_running_high_uses_only_closed_bars(self):
        detector = SessionDetector(definitions=ONE_HOUR_UTC)
        frame = bars(datetime(2024, 3, 8, 8, tzinfo=UTC), 36)

        as_of = datetime(2024, 3, 8, 9, 30, tzinfo=UTC)
        state = detector.session_state_at(frame, as_of, Symbol.EURUSD, Timeframe.M5)["test"]

        observable = frame.loc[frame["timestamp"] < pd.Timestamp(as_of)]
        in_window = observable.loc[observable["timestamp"] >= pd.Timestamp("2024-03-08T09:00:00Z")]
        assert state.bar_count == len(in_window)
        assert state.high_price == pytest.approx(in_window["high"].max())

    def test_active_flag(self):
        detector = SessionDetector(definitions=ONE_HOUR_UTC)
        frame = bars(datetime(2024, 3, 8, 8, tzinfo=UTC), 36)

        during = detector.session_state_at(
            frame, datetime(2024, 3, 8, 9, 30, tzinfo=UTC), Symbol.EURUSD, Timeframe.M5
        )["test"]
        after = detector.session_state_at(
            frame, datetime(2024, 3, 8, 10, 30, tzinfo=UTC), Symbol.EURUSD, Timeframe.M5
        )["test"]

        assert during.is_active and not during.is_complete
        assert not after.is_active and after.is_complete

    def test_state_before_any_bars_is_empty_not_zero(self):
        """Zero is a real price. 'Unknown' must be None."""
        detector = SessionDetector(definitions=ONE_HOUR_UTC)
        frame = bars(datetime(2024, 3, 8, 9, tzinfo=UTC), 12)

        state = detector.session_state_at(
            frame, datetime(2024, 3, 8, 9, 0, tzinfo=UTC), Symbol.EURUSD, Timeframe.M5
        )["test"]
        assert state.bar_count == 0
        assert state.high_price is None
        assert state.range_size is None
        assert state.position_in_range is None

    def test_position_in_range(self):
        detector = SessionDetector(definitions=ONE_HOUR_UTC)
        frame = bars(datetime(2024, 3, 8, 8, tzinfo=UTC), 36)
        state = detector.session_state_at(
            frame, datetime(2024, 3, 8, 9, 30, tzinfo=UTC), Symbol.EURUSD, Timeframe.M5
        )["test"]
        assert 0.0 <= state.position_in_range <= 1.0

    def test_naive_as_of_is_rejected(self):
        detector = SessionDetector(definitions=ONE_HOUR_UTC)
        frame = bars(datetime(2024, 3, 8, 8, tzinfo=UTC), 36)
        with pytest.raises(ValueError, match="timezone-aware"):
            detector.session_state_at(
                frame, datetime(2024, 3, 8, 9, 30), Symbol.EURUSD, Timeframe.M5
            )  # noqa: DTZ001


# -------------------------------------------------------- config independence


class TestConfigurability:
    def test_with_definitions_swaps_the_calendar(self):
        base = SessionDetector()
        custom = base.with_definitions(ONE_HOUR_UTC)
        assert [d.name for d in custom.definitions] == ["test"]
        assert base.definitions == DEFAULT_SESSIONS  # original untouched

    def test_detector_logic_holds_no_session_literals(self):
        """Changing only configuration must change the result — proving boundaries
        are not baked into detection (CLAUDE.md rule 4)."""
        frame = bars(datetime(2024, 3, 8, 8, tzinfo=UTC), 36)

        nine = SessionDetector(definitions=ONE_HOUR_UTC).detect(frame, Symbol.EURUSD, Timeframe.M5)
        ten = SessionDetector(
            definitions=(SessionDefinition("test", "UTC", time(10, 0), time(11, 0)),)
        ).detect(frame, Symbol.EURUSD, Timeframe.M5)

        assert nine[0].window.start_utc.hour == 9
        assert ten[0].window.start_utc.hour == 10
        assert nine[0].high_price != ten[0].high_price
