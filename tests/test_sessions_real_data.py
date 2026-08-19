"""R2-01 real-data acceptance — EURUSD + XAUUSD, 2024-03-08 → 2024-03-12.

Runs against the validated Phase 1.5 dataset. That data is **gitignored**, so these
tests SKIP cleanly when it is absent; the offline gate never depends on them.
Reproduce it with the command in ``docs/financial-ai/DATA_PROOF.md`` §12.

This period is an **engineering validation fixture only**. It contains a weekend
closure and the 2024-03-10 US DST transition, which is exactly what session logic
needs to be tested against. **No trading claim is made from it** — four days is far
too little for any statement about market behaviour.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from ict_kronos.domain import Symbol, Timeframe
from ict_kronos.ict import BoundaryAnomaly, EventType, SessionDetector, assert_no_leakage
from ict_kronos.storage import ParquetCandleStore

DATA_ROOT = "data/normalized"
WINDOW_START = datetime(2024, 3, 8, tzinfo=UTC)
WINDOW_END = datetime(2024, 3, 12, tzinfo=UTC)

#: Observed in Phase 1.5: after the SAME weekend closure the two instruments
#: reopened an hour apart, because the US DST transition moved the effective reopen
#: differently per instrument. This is the story's explicit acceptance case.
OBSERVED_REOPEN = {
    Symbol.EURUSD: datetime(2024, 3, 10, 21, 0, tzinfo=UTC),
    Symbol.XAUUSD: datetime(2024, 3, 10, 22, 0, tzinfo=UTC),
}


def load(symbol: Symbol, timeframe: Timeframe) -> pd.DataFrame:
    store = ParquetCandleStore(DATA_ROOT)
    frame = store.read(symbol, timeframe, start=pd.Timestamp(WINDOW_START), end=pd.Timestamp(WINDOW_END))
    if len(frame) == 0:
        pytest.skip(
            f"real data absent for {symbol.value}/{timeframe.value} — "
            f"see docs/financial-ai/DATA_PROOF.md §12 to reproduce it"
        )
    return frame


@pytest.fixture(params=[Symbol.EURUSD, Symbol.XAUUSD], ids=lambda s: s.value)
def symbol(request) -> Symbol:
    return request.param


@pytest.fixture
def m5(symbol) -> pd.DataFrame:
    return load(symbol, Timeframe.M5)


@pytest.fixture
def detector() -> SessionDetector:
    return SessionDetector()


class TestRealDataDetection:
    def test_sessions_are_detected(self, detector, m5, symbol):
        occurrences = detector.detect(m5, symbol, Timeframe.M5)
        assert occurrences, "no sessions detected in four days of real data"
        assert {o.window.name for o in occurrences} <= {
            "asian",
            "london",
            "new_york",
            "london_kill_zone",
            "new_york_kill_zone",
        }

    def test_every_session_has_valid_ohlc(self, detector, m5, symbol):
        for occurrence in detector.detect(m5, symbol, Timeframe.M5):
            assert occurrence.high_price >= max(occurrence.open_price, occurrence.close_price)
            assert occurrence.low_price <= min(occurrence.open_price, occurrence.close_price)
            assert occurrence.high_price >= occurrence.low_price
            assert occurrence.bar_count > 0

    def test_session_extremes_are_bounded_by_the_underlying_bars(self, detector, m5, symbol):
        """The session high must actually appear in the data, not be interpolated."""
        for occurrence in detector.detect(m5, symbol, Timeframe.M5):
            members = m5.loc[
                (m5["timestamp"] >= pd.Timestamp(occurrence.window.start_utc))
                & (m5["timestamp"] < pd.Timestamp(occurrence.window.end_utc))
            ]
            assert occurrence.high_price <= members["high"].max() + 1e-9
            assert occurrence.low_price >= members["low"].min() - 1e-9

    def test_no_session_is_reported_over_the_weekend(self, detector, m5, symbol):
        """Sat 2024-03-09 is fully closed. A session there would be fabricated."""
        occurrences = detector.detect(m5, symbol, Timeframe.M5)
        saturday = [
            o
            for o in occurrences
            if o.window.start_utc >= datetime(2024, 3, 9, 0, tzinfo=UTC)
            and o.window.end_utc <= datetime(2024, 3, 10, 12, tzinfo=UTC)
        ]
        assert saturday == [], f"{symbol.value}: sessions reported during the weekend closure"

    def test_events_carry_the_full_contract(self, detector, m5, symbol):
        events = detector.events(m5, symbol, Timeframe.M5)
        assert events
        for event in events:
            assert event.symbol == symbol.value
            assert event.timeframe == "5m"
            assert event.price_level > 0
            assert event.reference_level is not None
            assert event.strength is not None and event.strength >= 0


class TestRealDataLeakage:
    def test_no_event_leaks(self, detector, m5, symbol):
        events = detector.events(m5, symbol, Timeframe.M5)
        assert_no_leakage(events)

    def test_no_event_is_observable_before_its_session_ends(self, detector, m5, symbol):
        for event in detector.events(m5, symbol, Timeframe.M5):
            assert not event.is_observable_at(event.confirmation_timestamp - pd.Timedelta(seconds=1))

    def test_session_highs_confirm_after_they_print(self, detector, m5, symbol):
        highs = [
            e for e in detector.events(m5, symbol, Timeframe.M5) if e.event_type is EventType.SESSION_HIGH
        ]
        assert highs
        assert all(e.confirmation_timestamp > e.event_timestamp for e in highs)

    def test_batch_equals_streaming_replay(self, detector, m5, symbol):
        full = detector.detect(m5, symbol, Timeframe.M5)
        keys = [(o.window.name, o.window.start_utc, o.high_price, o.low_price) for o in full]

        for cut in range(200, len(m5) + 1, 200):
            partial = detector.detect(m5.iloc[:cut], symbol, Timeframe.M5)
            partial_keys = [(o.window.name, o.window.start_utc, o.high_price, o.low_price) for o in partial]
            assert partial_keys == keys[: len(partial_keys)], f"{symbol.value}: divergence at {cut}"


class TestRealDataDst:
    def test_new_york_session_shifts_across_the_us_transition(self, detector, m5, symbol):
        """EST 08:00 = 13:00 UTC before 2024-03-10; EDT 08:00 = 12:00 UTC after."""
        windows = {
            (w.name, w.local_date): w for w in detector.windows_for(m5, Timeframe.M5) if w.name == "new_york"
        }
        before = next(w for (n, d), w in windows.items() if d.isoformat() == "2024-03-08")
        after = next(w for (n, d), w in windows.items() if d.isoformat() == "2024-03-11")

        assert before.start_utc.hour == 13
        assert after.start_utc.hour == 12

    def test_instruments_reopen_at_different_utc_hours(self, symbol):
        """THE Phase 1.5 acceptance case. EURUSD's first post-weekend bar is
        21:00 UTC; XAUUSD's is 22:00 UTC. The engine must never assume one fixed
        reopen time for every instrument."""
        frame = load(symbol, Timeframe.M5)
        after_weekend = frame.loc[frame["timestamp"] >= pd.Timestamp("2024-03-10T00:00:00Z")]
        assert len(after_weekend) > 0

        first_bar = after_weekend["timestamp"].iloc[0].to_pydatetime()
        assert first_bar == OBSERVED_REOPEN[symbol], (
            f"{symbol.value} reopened at {first_bar.isoformat()}, "
            f"expected {OBSERVED_REOPEN[symbol].isoformat()} — the Phase 1.5 observation"
        )

    def test_detector_handles_both_reopen_times_without_special_casing(self, detector):
        """Same detector, same config, two instruments with different reopens — both
        must produce coherent sessions."""
        results = {}
        for instrument in (Symbol.EURUSD, Symbol.XAUUSD):
            frame = load(instrument, Timeframe.M5)
            results[instrument] = detector.detect(frame, instrument, Timeframe.M5)

        assert all(occurrences for occurrences in results.values())
        # Sunday-evening sessions differ between the instruments precisely because
        # their data starts an hour apart. Neither is "wrong".
        assert results[Symbol.EURUSD] != results[Symbol.XAUUSD]

    def test_london_kill_zone_is_short_on_us_spring_forward_day(self, detector, m5):
        """A real DST artefact this window exposes, and a good reason the anomaly is
        surfaced rather than swallowed.

        The London Kill Zone is defined 02:00-05:00 America/New_York. On 2024-03-10
        the clocks jump 02:00 -> 03:00, so **02:00 local does not exist that day**.
        The window is therefore genuinely 2 hours, not 3, and is flagged
        ``NONEXISTENT``. Anyone comparing kill-zone ranges across days needs to know
        that — silently normalising it would hide a real property of the market.
        """
        windows = {(w.name, w.local_date.isoformat()): w for w in detector.windows_for(m5, Timeframe.M5)}
        transition = windows[("london_kill_zone", "2024-03-10")]

        assert transition.anomaly is BoundaryAnomaly.NONEXISTENT
        assert transition.duration == pd.Timedelta(hours=2)
        assert transition.start_utc == datetime(2024, 3, 10, 7, tzinfo=UTC)

    def test_kill_zone_is_normal_length_on_ordinary_days(self, detector, m5):
        """The control: every other day that window is its nominal 3 hours."""
        windows = {(w.name, w.local_date.isoformat()): w for w in detector.windows_for(m5, Timeframe.M5)}
        for day in ("2024-03-08", "2024-03-11"):
            window = windows[("london_kill_zone", day)]
            assert window.anomaly is BoundaryAnomaly.NONE
            assert window.duration == pd.Timedelta(hours=3)

    def test_only_the_transition_day_is_flagged(self, detector, m5):
        """Anomalies must be rare and specific — a detector that flags everything is
        as useless as one that flags nothing."""
        anomalies = [
            w for w in detector.windows_for(m5, Timeframe.M5) if w.anomaly is not BoundaryAnomaly.NONE
        ]
        assert [(w.name, w.local_date.isoformat()) for w in anomalies] == [("london_kill_zone", "2024-03-10")]


class TestRealDataAcrossTimeframes:
    @pytest.mark.parametrize("timeframe", [Timeframe.M1, Timeframe.M5, Timeframe.M15])
    def test_detection_is_consistent_across_timeframes(self, detector, symbol, timeframe):
        """Session extremes computed on finer bars must bound those from coarser bars:
        a 15m bar's high is the max of its 1m highs, so aggregation can only lose
        detail inside the window, never exceed it."""
        frame = load(symbol, timeframe)
        occurrences = detector.detect(frame, symbol, timeframe)
        assert occurrences
        for occurrence in occurrences:
            assert occurrence.high_price >= occurrence.low_price
            assert occurrence.bar_count > 0

    def test_finer_timeframe_sees_at_least_as_many_sessions(self, detector, symbol):
        """Coarse bars can be excluded by the fully-contained membership rule, so 1m
        can only see the same or more sessions than 15m."""
        m1 = detector.detect(load(symbol, Timeframe.M1), symbol, Timeframe.M1)
        m15 = detector.detect(load(symbol, Timeframe.M15), symbol, Timeframe.M15)
        assert len(m1) >= len(m15)
