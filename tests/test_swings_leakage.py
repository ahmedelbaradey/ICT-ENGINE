"""R2-02 leakage, immutability and streaming replay.

Four guarantees, each with a distinct failure mode:

1. **Confirmation lag is real.** No swing is observable before bar ``i + right`` closes.
2. **Immutability.** A confirmed swing is never revised by a later candle.
3. **Batch == streaming replay.** Detecting over ``history[:k]`` equals replaying to *k*.
4. **Downstream cannot bypass it.** ``filter_observable`` is the one gate feature
   assembly goes through, and it holds for swings.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from ict_kronos.domain import Symbol, Timeframe
from ict_kronos.ict import (
    ContractViolation,
    SwingConfig,
    SwingDetector,
    TiePolicy,
    assert_no_leakage,
    assert_observable,
    filter_observable,
)

from .test_swings import frame_from_highs, highs_of

pytestmark = pytest.mark.leakage


@pytest.fixture
def noisy_frame():
    """400 bars of rounded noise — plenty of pivots and plenty of plateaus."""
    rng = np.random.default_rng(20240302)
    highs = np.round(1.08 + rng.normal(0, 0.0025, 400), 4).tolist()
    lows = [h - round(abs(rng.normal(0, 0.0005)), 4) - 0.0002 for h in highs]
    return frame_from_highs(highs, lows)


@pytest.fixture
def detector():
    return SwingDetector(SwingConfig(left=3, right=3))


# ------------------------------------------------------- 1. confirmation lag


class TestConfirmationLagIsReal:
    def test_no_swing_confirms_at_its_own_bar(self, detector, noisy_frame):
        swings = detector.detect(noisy_frame, Symbol.EURUSD, Timeframe.M5)
        assert swings
        assert all(s.confirmation_timestamp > s.event_timestamp for s in swings)

    def test_lag_equals_right_bars_plus_the_confirming_bar(self, detector, noisy_frame):
        """confirmation = close of bar i+right = event_open + (right+1) * duration."""
        expected = timedelta(minutes=Timeframe.M5.minutes * (detector.config.right + 1))
        for swing in detector.detect(noisy_frame, Symbol.EURUSD, Timeframe.M5):
            assert swing.confirmation_timestamp - swing.event_timestamp == expected

    def test_contract_invariant_holds(self, detector, noisy_frame):
        assert_no_leakage(detector.events(noisy_frame, Symbol.EURUSD, Timeframe.M5))

    def test_not_observable_one_second_early(self, detector, noisy_frame):
        for event in detector.events(noisy_frame, Symbol.EURUSD, Timeframe.M5):
            assert not event.is_observable_at(event.confirmation_timestamp - timedelta(seconds=1))
            assert event.is_observable_at(event.confirmation_timestamp)

    def test_the_peak_is_invisible_until_the_window_completes(self):
        """Bar-by-bar: the peak exists on the chart from bar 2, but is undetectable
        until bar 4 closes."""
        detector = SwingDetector(SwingConfig(left=2, right=2))
        highs = [1.00, 1.01, 1.05, 1.02, 1.01, 1.00]
        full = frame_from_highs(highs)

        for visible in range(1, 5):
            assert highs_of(detector, full.iloc[:visible]) == [], f"leaked at {visible} bars"
        assert len(highs_of(detector, full.iloc[:5])) == 1


# ---------------------------------------------------------- 2. immutability


class TestImmutability:
    def test_a_confirmed_swing_is_never_revised(self, detector, noisy_frame):
        """The decisive property. Detect over a prefix, then over the full series; the
        prefix's swings must appear byte-identical in the longer run."""
        prefix = noisy_frame.iloc[:200]
        early = detector.detect(prefix, Symbol.EURUSD, Timeframe.M5)
        later = detector.detect(noisy_frame, Symbol.EURUSD, Timeframe.M5)

        assert early
        later_by_key = {(s.direction, s.index): s for s in later}
        for swing in early:
            match = later_by_key.get((swing.direction, swing.index))
            assert match is not None, f"swing at index {swing.index} vanished"
            assert match.as_dict() == swing.as_dict(), "a confirmed swing was revised"

    def test_a_later_higher_high_does_not_invalidate_an_earlier_swing(self):
        """Swings are LOCAL pivots, not running extremes. A new high later is a new
        swing, not a retraction of the old one — documented in docs/ict/swings.md."""
        detector = SwingDetector(SwingConfig(left=2, right=2))
        frame = frame_from_highs([1.00, 1.01, 1.05, 1.02, 1.01, 1.02, 1.09, 1.02, 1.01])

        swings = highs_of(detector, frame)
        indices = [s.index for s in swings]
        assert 2 in indices, "the earlier, lower swing high was dropped"
        assert 6 in indices, "the later, higher swing high was missed"

    def test_appending_bars_only_ever_adds_swings(self, detector, noisy_frame):
        previous: list = []
        for cut in range(60, len(noisy_frame) + 1, 60):
            current = detector.detect(noisy_frame.iloc[:cut], Symbol.EURUSD, Timeframe.M5)
            keys = [(s.direction, s.index, s.price_level) for s in current]
            previous_keys = [(s.direction, s.index, s.price_level) for s in previous]
            assert keys[: len(previous_keys)] == previous_keys, f"history changed at cut={cut}"
            previous = current

    def test_swing_points_are_frozen(self, detector, noisy_frame):
        swing = detector.detect(noisy_frame, Symbol.EURUSD, Timeframe.M5)[0]
        with pytest.raises(Exception):  # noqa: B017 - FrozenInstanceError
            swing.price_level = 9.99


# ------------------------------------------------- 3. batch == streaming replay


class TestBatchEqualsStreamingReplay:
    @pytest.mark.parametrize("policy", list(TiePolicy))
    def test_prefix_detection_matches_the_full_prefix(self, noisy_frame, policy):
        detector = SwingDetector(SwingConfig(left=2, right=2, tie_policy=policy))
        full = detector.detect(noisy_frame, Symbol.EURUSD, Timeframe.M5)
        full_keys = [
            (s.direction.value, s.event_timestamp, s.confirmation_timestamp, s.price_level) for s in full
        ]

        for cut in range(50, len(noisy_frame) + 1, 50):
            partial = detector.detect(noisy_frame.iloc[:cut], Symbol.EURUSD, Timeframe.M5)
            partial_keys = [
                (s.direction.value, s.event_timestamp, s.confirmation_timestamp, s.price_level)
                for s in partial
            ]
            assert partial_keys == full_keys[: len(partial_keys)], f"divergence at cut={cut}"

    def test_bar_by_bar_replay_reproduces_the_batch_result(self):
        """The strictest form: feed one bar at a time and accumulate."""
        detector = SwingDetector(SwingConfig(left=2, right=2))
        rng = np.random.default_rng(11)
        highs = np.round(1.08 + rng.normal(0, 0.002, 120), 4).tolist()
        frame = frame_from_highs(highs)

        seen: list[tuple] = []
        for n in range(1, len(frame) + 1):
            for swing in detector.detect(frame.iloc[:n], Symbol.EURUSD, Timeframe.M5):
                key = (swing.direction.value, swing.index, swing.price_level)
                if key not in seen:
                    seen.append(key)

        batch = [
            (s.direction.value, s.index, s.price_level)
            for s in detector.detect(frame, Symbol.EURUSD, Timeframe.M5)
        ]
        assert seen == batch

    def test_events_replay_identically(self, detector, noisy_frame):
        full = detector.events(noisy_frame, Symbol.EURUSD, Timeframe.M5)
        keys = [(e.event_type.value, e.event_timestamp, e.confirmation_timestamp) for e in full]

        for cut in (100, 200, 300, len(noisy_frame)):
            partial = detector.events(noisy_frame.iloc[:cut], Symbol.EURUSD, Timeframe.M5)
            partial_keys = [
                (e.event_type.value, e.event_timestamp, e.confirmation_timestamp) for e in partial
            ]
            assert partial_keys == keys[: len(partial_keys)]


# --------------------------------------------- 4. downstream cannot bypass it


class TestDownstreamObservability:
    def test_observable_at_matches_detecting_over_visible_bars_only(self, detector, noisy_frame):
        """THE guarantee the story asks for: a feature builder asking 'what did I know
        at time t?' gets exactly what a live system would have had."""
        for cut in range(60, len(noisy_frame) + 1, 60):
            visible = noisy_frame.iloc[:cut]
            as_of = visible["timestamp"].iloc[-1].to_pydatetime() + timedelta(minutes=Timeframe.M5.minutes)

            from_full = detector.observable_at(noisy_frame, as_of, Symbol.EURUSD, Timeframe.M5)
            from_visible = detector.detect(visible, Symbol.EURUSD, Timeframe.M5)

            assert [s.as_dict() for s in from_full] == [s.as_dict() for s in from_visible]

    def test_filter_observable_drops_unconfirmed_events(self, detector, noisy_frame):
        events = detector.events(noisy_frame, Symbol.EURUSD, Timeframe.M5)
        midpoint = events[len(events) // 2].confirmation_timestamp

        visible = filter_observable(events, midpoint)
        assert visible
        assert len(visible) < len(events)
        assert all(e.confirmation_timestamp <= midpoint for e in visible)

    def test_assert_observable_catches_a_leak(self, detector, noisy_frame):
        events = detector.events(noisy_frame, Symbol.EURUSD, Timeframe.M5)
        too_early = events[0].confirmation_timestamp - timedelta(seconds=1)

        with pytest.raises(ContractViolation, match="not observable"):
            assert_observable(events, too_early)

    def test_assert_observable_passes_after_the_last_confirmation(self, detector, noisy_frame):
        events = detector.events(noisy_frame, Symbol.EURUSD, Timeframe.M5)
        assert_observable(events, max(e.confirmation_timestamp for e in events))

    def test_naive_as_of_is_rejected_everywhere(self, detector, noisy_frame):
        naive = datetime(2024, 3, 8, 12, 0)  # noqa: DTZ001
        with pytest.raises(ValueError, match="timezone-aware"):
            detector.observable_at(noisy_frame, naive, Symbol.EURUSD, Timeframe.M5)
        with pytest.raises(ContractViolation, match="timezone-aware"):
            filter_observable(detector.events(noisy_frame, Symbol.EURUSD, Timeframe.M5), naive)

    def test_no_swing_is_observable_at_its_own_event_time(self, detector, noisy_frame):
        """A feature vector built at the pivot bar must not contain that pivot."""
        for swing in detector.detect(noisy_frame, Symbol.EURUSD, Timeframe.M5):
            observable = detector.observable_at(
                noisy_frame, swing.event_timestamp, Symbol.EURUSD, Timeframe.M5
            )
            assert swing not in observable


class TestNoFutureContaminationOfPrices:
    def test_reported_price_exists_in_the_confirming_window(self, detector, noisy_frame):
        """The pivot price must come from the pivot bar, not from anywhere later."""
        bars = noisy_frame.reset_index(drop=True)
        for swing in detector.detect(noisy_frame, Symbol.EURUSD, Timeframe.M5):
            column = "high" if swing.is_high else "low"
            assert swing.price_level == pytest.approx(float(bars.loc[swing.index, column]))
            assert bars.loc[swing.index, "timestamp"].to_pydatetime() == swing.event_timestamp

    def test_reference_level_comes_only_from_the_window(self, detector, noisy_frame):
        bars = noisy_frame.reset_index(drop=True)
        left, right = detector.config.left, detector.config.right

        for swing in detector.detect(noisy_frame, Symbol.EURUSD, Timeframe.M5):
            column = "high" if swing.is_high else "low"
            window = bars.loc[swing.index - left : swing.index + right, column]
            assert window.min() - 1e-9 <= swing.reference_level <= window.max() + 1e-9

    def test_confirmation_never_precedes_the_confirming_bars_close(self, detector, noisy_frame):
        bars = pd.concat([noisy_frame.reset_index(drop=True)["timestamp"]], axis=1).reset_index(drop=True)
        duration = timedelta(minutes=Timeframe.M5.minutes)

        for swing in detector.detect(noisy_frame, Symbol.EURUSD, Timeframe.M5):
            confirming_bar_open = bars.loc[swing.index + detector.config.right, "timestamp"]
            assert swing.confirmation_timestamp == confirming_bar_open.to_pydatetime() + duration
