"""R2-01 leakage and streaming-replay tests.

Acceptance criterion 9 of the story. Two distinct guarantees:

1. **Nothing is observable before its `confirmation_timestamp`.** A completed
   session's high is not knowable until the session ends, and the running state at
   time *t* never reflects a bar closing after *t*.
2. **Batch == streaming replay.** Detecting over `history[:k]` must equal replaying
   bar by bar up to *k*, for every *k*. If those differ, the batch path saw
   something the live path could not.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pandas as pd
import pytest

from ict_kronos.domain import Symbol, Timeframe
from ict_kronos.ict import (
    DEFAULT_SESSIONS,
    EventType,
    SessionDefinition,
    SessionDetector,
    assert_no_leakage,
)

from .test_sessions import bars

pytestmark = pytest.mark.leakage

ONE_HOUR_UTC = (SessionDefinition("test", "UTC", time(9, 0), time(10, 0)),)
START = datetime(2024, 3, 8, 0, 0, tzinfo=UTC)


@pytest.fixture
def frame():
    """Three full UTC days of 5-minute bars."""
    return bars(START, 3 * 288, timeframe=Timeframe.M5)


@pytest.fixture
def detector():
    return SessionDetector(definitions=ONE_HOUR_UTC)


class TestConfirmationIsNotEarly:
    def test_contract_invariant_holds_for_every_event(self, detector, frame):
        events = detector.events(frame, Symbol.EURUSD, Timeframe.M5)
        assert events
        assert_no_leakage(events)

    def test_no_event_is_observable_before_its_session_ends(self, detector, frame):
        for event in detector.events(frame, Symbol.EURUSD, Timeframe.M5):
            session_end = event.confirmation_timestamp
            assert not event.is_observable_at(session_end - timedelta(seconds=1))
            assert event.is_observable_at(session_end)

    def test_session_high_is_not_stamped_at_the_extreme_bar(self, detector, frame):
        """The specific bug this design exists to prevent: confirming a session high
        at the bar that printed it, which is up to a whole session early."""
        highs = [
            e
            for e in detector.events(frame, Symbol.EURUSD, Timeframe.M5)
            if e.event_type is EventType.SESSION_HIGH
        ]
        assert highs
        assert all(e.confirmation_timestamp > e.event_timestamp for e in highs)

    def test_confirmation_equals_the_window_end_exactly(self, detector, frame):
        occurrences = detector.detect(frame, Symbol.EURUSD, Timeframe.M5)
        assert occurrences
        for occurrence in occurrences:
            assert occurrence.confirmation_timestamp == occurrence.window.end_utc
            # And the extreme genuinely happened before that instant.
            assert occurrence.high_timestamp < occurrence.window.end_utc

    def test_default_sessions_also_hold_the_invariant(self, frame):
        events = SessionDetector().events(frame, Symbol.EURUSD, Timeframe.M5)
        assert events
        assert_no_leakage(events)
        assert all(e.confirmation_timestamp >= e.event_timestamp for e in events)


class TestRunningStateIsPointInTime:
    @pytest.mark.parametrize("minutes", [0, 5, 15, 30, 45, 55, 60, 120])
    def test_running_state_never_sees_a_bar_closing_after_as_of(self, detector, frame, minutes):
        as_of = datetime(2024, 3, 8, 9, tzinfo=UTC) + timedelta(minutes=minutes)
        state = detector.session_state_at(frame, as_of, Symbol.EURUSD, Timeframe.M5)["test"]

        if state.bar_count == 0:
            assert state.high_price is None
            return

        # Recompute the ground truth from strictly-observable bars only.
        observable = frame.loc[
            (frame["timestamp"] + pd.Timedelta(minutes=5) <= pd.Timestamp(as_of))
            & (frame["timestamp"] >= pd.Timestamp("2024-03-08T09:00:00Z"))
            & (frame["timestamp"] < pd.Timestamp("2024-03-08T10:00:00Z"))
        ]
        assert state.bar_count == len(observable)
        assert state.high_price == pytest.approx(observable["high"].max())
        assert state.low_price == pytest.approx(observable["low"].min())

    def test_running_high_is_monotonic_as_time_advances(self, detector, frame):
        """A running maximum can only rise. A fall would mean an earlier reading had
        included a bar it should not have."""
        previous = None
        for minutes in range(5, 65, 5):
            as_of = datetime(2024, 3, 8, 9, tzinfo=UTC) + timedelta(minutes=minutes)
            state = detector.session_state_at(frame, as_of, Symbol.EURUSD, Timeframe.M5)["test"]
            if state.high_price is None:
                continue
            if previous is not None:
                assert state.high_price >= previous - 1e-12
            previous = state.high_price

    def test_running_state_mid_session_differs_from_the_final_value(self, detector, frame):
        """If they matched, the running state would already know the session's future."""
        mid = detector.session_state_at(
            frame, datetime(2024, 3, 8, 9, 30, tzinfo=UTC), Symbol.EURUSD, Timeframe.M5
        )["test"]
        final = detector.detect(frame, Symbol.EURUSD, Timeframe.M5)[0]

        assert mid.high_price < final.high_price

    def test_a_bar_still_forming_contributes_nothing(self, detector, frame):
        """At 09:02 the 09:00 five-minute bar has not closed; its high is unknown."""
        state = detector.session_state_at(
            frame, datetime(2024, 3, 8, 9, 2, tzinfo=UTC), Symbol.EURUSD, Timeframe.M5
        )["test"]
        assert state.bar_count == 0


class TestBatchEqualsStreamingReplay:
    def test_prefix_detection_matches_full_history_prefix(self, detector, frame):
        """batch(history[:k]) == prefix of batch(history), for many k."""
        full = detector.detect(frame, Symbol.EURUSD, Timeframe.M5)
        full_keys = [(o.window.name, o.window.start_utc, o.high_price, o.low_price) for o in full]

        for cut in range(60, len(frame) + 1, 60):
            partial = detector.detect(frame.iloc[:cut], Symbol.EURUSD, Timeframe.M5)
            partial_keys = [(o.window.name, o.window.start_utc, o.high_price, o.low_price) for o in partial]
            assert partial_keys == full_keys[: len(partial_keys)], f"divergence at cut={cut}"

    def test_events_replay_identically(self, detector, frame):
        full = detector.events(frame, Symbol.EURUSD, Timeframe.M5)
        full_keys = [
            (e.event_type.value, e.event_timestamp, e.confirmation_timestamp, e.price_level) for e in full
        ]

        for cut in range(288, len(frame) + 1, 288):
            partial = detector.events(frame.iloc[:cut], Symbol.EURUSD, Timeframe.M5)
            partial_keys = [
                (e.event_type.value, e.event_timestamp, e.confirmation_timestamp, e.price_level)
                for e in partial
            ]
            assert partial_keys == full_keys[: len(partial_keys)], f"divergence at cut={cut}"

    def test_an_incomplete_session_is_never_emitted_early(self, detector, frame):
        """Cut mid-session: the session in progress must not appear."""
        cut = frame.loc[frame["timestamp"] < pd.Timestamp("2024-03-08T09:30:00Z")]
        assert detector.detect(cut, Symbol.EURUSD, Timeframe.M5) == []

    def test_a_session_appears_exactly_once_its_window_elapses(self, detector, frame):
        before = frame.loc[frame["timestamp"] < pd.Timestamp("2024-03-08T09:55:00Z")]
        after = frame.loc[frame["timestamp"] < pd.Timestamp("2024-03-08T10:00:00Z")]

        assert detector.detect(before, Symbol.EURUSD, Timeframe.M5) == []
        assert len(detector.detect(after, Symbol.EURUSD, Timeframe.M5)) == 1

    def test_replay_with_default_sessions(self, frame):
        detector = SessionDetector()
        full = detector.detect(frame, Symbol.EURUSD, Timeframe.M5)
        keys = [(o.window.name, o.window.start_utc) for o in full]

        for cut in (288, 576, len(frame)):
            partial = detector.detect(frame.iloc[:cut], Symbol.EURUSD, Timeframe.M5)
            assert [(o.window.name, o.window.start_utc) for o in partial] == keys[: len(partial)]


class TestNoFutureContamination:
    def test_appending_future_bars_does_not_change_past_sessions(self, detector):
        """The decisive property: what happens later must never alter what a
        completed session already reported."""
        early = bars(START, 288, timeframe=Timeframe.M5)
        extended = bars(START, 3 * 288, timeframe=Timeframe.M5)

        first_day_early = detector.detect(early, Symbol.EURUSD, Timeframe.M5)
        first_day_later = [
            o
            for o in detector.detect(extended, Symbol.EURUSD, Timeframe.M5)
            if o.window.local_date == first_day_early[0].window.local_date
        ]

        assert len(first_day_early) == 1
        assert first_day_early[0].as_dict() == first_day_later[0].as_dict()

    def test_windows_use_only_calendar_arithmetic(self):
        """Window boundaries must not depend on the data at all — otherwise a future
        bar could shift a past session's span."""
        detector = SessionDetector(definitions=DEFAULT_SESSIONS)
        assert detector.active_sessions_at(datetime(2024, 3, 11, 13, tzinfo=UTC))
