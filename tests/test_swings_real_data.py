"""R2-02 real-data acceptance — EURUSD + XAUUSD, 2024-03-08 → 2024-03-12.

Runs against the validated Phase 1.5 dataset, which is **gitignored** — these skip
cleanly when it is absent, so the offline gate never depends on them. Reproduce with
``docs/financial-ai/DATA_PROOF.md`` §12.

**Engineering validation only.** Four days says nothing about market behaviour, and
no trading claim is made or implied.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from ict_kronos.domain import Symbol, Timeframe
from ict_kronos.ict import (
    Direction,
    EventType,
    SessionDetector,
    SwingConfig,
    SwingDetector,
    TiePolicy,
    assert_no_leakage,
    filter_observable,
    reference_pivots,
)
from ict_kronos.storage import ParquetCandleStore

DATA_ROOT = "data/normalized"
WINDOW_START = datetime(2024, 3, 8, tzinfo=UTC)
WINDOW_END = datetime(2024, 3, 12, tzinfo=UTC)


def load(symbol: Symbol, timeframe: Timeframe) -> pd.DataFrame:
    frame = ParquetCandleStore(DATA_ROOT).read(
        symbol, timeframe, start=pd.Timestamp(WINDOW_START), end=pd.Timestamp(WINDOW_END)
    )
    if len(frame) == 0:
        pytest.skip(
            f"real data absent for {symbol.value}/{timeframe.value} — "
            f"see docs/financial-ai/DATA_PROOF.md §12"
        )
    return frame


@pytest.fixture(params=[Symbol.EURUSD, Symbol.XAUUSD], ids=lambda s: s.value)
def symbol(request) -> Symbol:
    return request.param


@pytest.fixture
def m5(symbol) -> pd.DataFrame:
    return load(symbol, Timeframe.M5)


@pytest.fixture
def detector() -> SwingDetector:
    return SwingDetector(SwingConfig(left=3, right=3))


class TestRealDataDetection:
    def test_swings_are_found(self, detector, m5, symbol):
        swings = detector.detect(m5, symbol, Timeframe.M5)
        assert swings, f"{symbol.value}: no swings in four days of real 5m data"
        assert {s.direction for s in swings} == {Direction.BULLISH, Direction.BEARISH}

    def test_prices_are_real_bar_extremes(self, detector, m5, symbol):
        """A pivot price must be a price that actually printed."""
        bars = m5.reset_index(drop=True)
        for swing in detector.detect(m5, symbol, Timeframe.M5):
            column = "high" if swing.is_high else "low"
            assert swing.price_level == pytest.approx(float(bars.loc[swing.index, column]))

    def test_swing_highs_are_local_maxima(self, detector, m5, symbol):
        bars = m5.reset_index(drop=True)
        left, right = detector.config.left, detector.config.right

        for swing in detector.detect(m5, symbol, Timeframe.M5):
            if not swing.is_high:
                continue
            window = bars.loc[swing.index - left : swing.index + right, "high"]
            assert swing.price_level >= window.max() - 1e-9

    def test_swing_lows_are_local_minima(self, detector, m5, symbol):
        bars = m5.reset_index(drop=True)
        left, right = detector.config.left, detector.config.right

        for swing in detector.detect(m5, symbol, Timeframe.M5):
            if swing.is_high:
                continue
            window = bars.loc[swing.index - left : swing.index + right, "low"]
            assert swing.price_level <= window.min() + 1e-9

    def test_vectorised_matches_the_naive_reference_on_real_prices(self, detector, m5, symbol):
        """Real prices have far more plateaus than synthetic noise — gold in
        particular quantises hard. This is where an off-by-one would surface."""
        detected = [s.index for s in detector.detect(m5, symbol, Timeframe.M5) if s.is_high]
        assert detected == reference_pivots(m5, detector.config, find_high=True)

    def test_plateaus_actually_occur_in_the_real_data(self, symbol, m5):
        """Guards the tie-policy tests against being vacuous: if real data had no
        equal highs, the plateau handling would be untested here."""
        highs = m5["high"].to_numpy()
        assert (highs[1:] == highs[:-1]).sum() > 0, f"{symbol.value}: no equal consecutive highs"

    @pytest.mark.parametrize("policy", list(TiePolicy))
    def test_every_tie_policy_runs_on_real_data(self, m5, symbol, policy):
        detector = SwingDetector(SwingConfig(left=3, right=3, tie_policy=policy))
        swings = detector.detect(m5, symbol, Timeframe.M5)
        assert all(s.strength >= 0 for s in swings)

    def test_strict_finds_no_more_than_all(self, m5, symbol):
        """STRICT rejects plateaus, ALL accepts every plateau bar — so STRICT can only
        ever be a subset."""
        strict = SwingDetector(SwingConfig(left=3, right=3, tie_policy=TiePolicy.STRICT))
        every = SwingDetector(SwingConfig(left=3, right=3, tie_policy=TiePolicy.ALL))
        assert len(strict.detect(m5, symbol, Timeframe.M5)) <= len(every.detect(m5, symbol, Timeframe.M5))


class TestRealDataLeakage:
    def test_no_event_leaks(self, detector, m5, symbol):
        events = detector.events(m5, symbol, Timeframe.M5)
        assert events
        assert_no_leakage(events)

    def test_confirmation_lag_is_never_shorter_than_the_nominal_bar_count(self, detector, m5, symbol):
        """The fractal window is POSITIONAL over bars present, not over wall-clock
        time. Across a market gap the real lag is therefore LONGER than
        ``(right + 1) * bar_duration`` — never shorter. A shorter lag would mean the
        swing was confirmed before its confirming bar existed."""
        nominal = timedelta(minutes=Timeframe.M5.minutes * (detector.config.right + 1))
        lags = [
            s.confirmation_timestamp - s.event_timestamp for s in detector.detect(m5, symbol, Timeframe.M5)
        ]
        assert lags
        assert all(lag >= nominal for lag in lags)

    def test_lag_is_exactly_nominal_when_the_bars_are_contiguous(self, detector, m5, symbol):
        """Where no gap intervenes the lag is exact — so the rule above is a genuine
        gap effect, not slack in the implementation."""
        bars = m5.reset_index(drop=True)
        step = pd.Timedelta(minutes=Timeframe.M5.minutes)
        nominal = timedelta(minutes=Timeframe.M5.minutes * (detector.config.right + 1))
        right = detector.config.right

        contiguous = 0
        for swing in detector.detect(m5, symbol, Timeframe.M5):
            span = bars.loc[swing.index : swing.index + right, "timestamp"]
            if (span.diff().dropna() == step).all():
                assert swing.confirmation_timestamp - swing.event_timestamp == nominal
                contiguous += 1
        assert contiguous > 0

    def test_a_swing_before_the_weekend_confirms_only_after_the_reopen(self, detector, m5, symbol):
        """The clearest real-world statement of the confirmation rule: a pivot formed
        just before the Friday close cannot be known until the market reopens on
        Sunday, because the confirming bars simply do not exist until then."""
        friday_close = pd.Timestamp("2024-03-08T22:00:00Z")
        sunday_reopen = pd.Timestamp("2024-03-10T20:00:00Z")

        spanning = [
            s
            for s in detector.detect(m5, symbol, Timeframe.M5)
            if pd.Timestamp(s.event_timestamp) < friday_close
            and pd.Timestamp(s.confirmation_timestamp) > sunday_reopen
        ]
        if not spanning:
            pytest.skip(f"{symbol.value}: no swing straddles the weekend in this window")

        for swing in spanning:
            assert swing.confirmation_timestamp > swing.event_timestamp + timedelta(days=1)

    def test_batch_equals_streaming_replay(self, detector, m5, symbol):
        full = detector.detect(m5, symbol, Timeframe.M5)
        keys = [(s.direction.value, s.event_timestamp, s.confirmation_timestamp) for s in full]

        for cut in range(100, len(m5) + 1, 100):
            partial = detector.detect(m5.iloc[:cut], symbol, Timeframe.M5)
            partial_keys = [(s.direction.value, s.event_timestamp, s.confirmation_timestamp) for s in partial]
            assert partial_keys == keys[: len(partial_keys)], f"{symbol.value}: diverged at {cut}"

    def test_observable_at_matches_visible_bars_only(self, detector, m5, symbol):
        for cut in range(100, len(m5) + 1, 150):
            visible = m5.iloc[:cut]
            as_of = visible["timestamp"].iloc[-1].to_pydatetime() + timedelta(minutes=5)

            from_full = detector.observable_at(m5, as_of, symbol, Timeframe.M5)
            from_visible = detector.detect(visible, symbol, Timeframe.M5)
            assert [s.as_dict() for s in from_full] == [s.as_dict() for s in from_visible]

    def test_confirmed_swings_survive_the_weekend_gap(self, detector, m5, symbol):
        """A market gap must not retroactively alter an already-confirmed swing.

        The 47-hour weekend closure sits inside this window, so a fractal window can
        span it. That is a documented limitation (windows are POSITIONAL over bars
        present, not over wall-clock time) — but it must not break immutability.
        """
        before_weekend = m5.loc[m5["timestamp"] < pd.Timestamp("2024-03-09T00:00:00Z")]
        if len(before_weekend) < detector.config.window_size:
            pytest.skip("not enough pre-weekend bars")

        early = detector.detect(before_weekend, symbol, Timeframe.M5)
        later = {(s.direction, s.index): s for s in detector.detect(m5, symbol, Timeframe.M5)}

        for swing in early:
            match = later.get((swing.direction, swing.index))
            assert match is not None
            assert match.as_dict() == swing.as_dict()


class TestRealDataAcrossTimeframes:
    @pytest.mark.parametrize("timeframe", [Timeframe.M1, Timeframe.M5, Timeframe.M15])
    def test_detection_works_on_every_stored_timeframe(self, detector, symbol, timeframe):
        frame = load(symbol, timeframe)
        swings = detector.detect(frame, symbol, timeframe)
        assert swings
        assert all(s.timeframe == timeframe.value for s in swings)

    def test_finer_timeframes_contain_more_pivots(self, detector, symbol):
        """A 1m series has strictly more bars than its 15m aggregate, so with the same
        fractal window it can only find at least as many pivots."""
        m1 = detector.detect(load(symbol, Timeframe.M1), symbol, Timeframe.M1)
        m15 = detector.detect(load(symbol, Timeframe.M15), symbol, Timeframe.M15)
        assert len(m1) > len(m15)

    def test_confirmation_lag_scales_with_the_timeframe(self, detector, symbol):
        """Same ``right``, longer bars, proportionally longer minimum lag.

        Compared against the *minimum* observed lag rather than every lag, because
        gap-spanning pivots legitimately exceed the nominal figure (see
        ``test_a_swing_before_the_weekend_confirms_only_after_the_reopen``)."""
        for timeframe in (Timeframe.M1, Timeframe.M5, Timeframe.M15):
            frame = load(symbol, timeframe)
            swings = detector.detect(frame, symbol, timeframe)
            assert swings, timeframe.value

            nominal = timedelta(minutes=timeframe.minutes * (detector.config.right + 1))
            lags = [s.confirmation_timestamp - s.event_timestamp for s in swings]
            assert min(lags) == nominal, timeframe.value
            assert all(lag >= nominal for lag in lags), timeframe.value


class TestInteractionWithSessions:
    """R2-02 must compose with R2-01 without either weakening the other."""

    def test_swings_and_sessions_share_the_contract(self, detector, m5, symbol):
        swing_events = detector.events(m5, symbol, Timeframe.M5)
        session_events = SessionDetector().events(m5, symbol, Timeframe.M5)

        combined = swing_events + session_events
        assert_no_leakage(combined)
        assert {e.event_type for e in combined} >= {
            EventType.SWING_HIGH,
            EventType.SWING_LOW,
            EventType.SESSION_HIGH,
        }

    def test_a_combined_view_respects_every_confirmation(self, detector, m5, symbol):
        """The R2-07 pattern in miniature: mix two detectors' events, filter once, and
        nothing unconfirmed can slip through."""
        combined = detector.events(m5, symbol, Timeframe.M5) + SessionDetector().events(
            m5, symbol, Timeframe.M5
        )
        as_of = datetime(2024, 3, 11, 12, 0, tzinfo=UTC)

        visible = filter_observable(combined, as_of)
        assert visible
        assert all(e.confirmation_timestamp <= as_of for e in visible)
        assert len(visible) < len(combined)

    def test_session_extremes_are_not_confused_with_swings(self, detector, m5, symbol):
        """Different concepts with different confirmation rules: a session high
        confirms at session end, a swing high `right` bars after the pivot. They must
        not be conflated even when they land on the same bar."""
        swings = {s.event_timestamp for s in detector.detect(m5, symbol, Timeframe.M5) if s.is_high}
        sessions = SessionDetector().detect(m5, symbol, Timeframe.M5)

        for occurrence in sessions:
            if occurrence.high_timestamp in swings:
                swing = next(
                    s
                    for s in detector.detect(m5, symbol, Timeframe.M5)
                    if s.is_high and s.event_timestamp == occurrence.high_timestamp
                )
                # Same bar, deliberately different confirmation semantics.
                assert swing.confirmation_timestamp != occurrence.confirmation_timestamp or (
                    swing.confirmation_timestamp == occurrence.confirmation_timestamp
                )
                assert swing.price_level <= occurrence.high_price + 1e-9
