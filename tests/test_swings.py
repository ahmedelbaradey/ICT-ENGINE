"""R2-02 SwingDetector — fractal pivots, tie policies, configuration, boundaries.

Leakage, immutability and streaming replay live in ``test_swings_leakage.py``;
real-data acceptance in ``test_swings_real_data.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame
from ict_kronos.ict import (
    Direction,
    EventType,
    SwingConfig,
    SwingDetector,
    TiePolicy,
    reference_pivots,
)

START = datetime(2024, 3, 8, 9, 0, tzinfo=UTC)


def frame_from_highs(
    highs: list[float],
    lows: list[float] | None = None,
    *,
    timeframe: Timeframe = Timeframe.M5,
    symbol: Symbol = Symbol.EURUSD,
) -> pd.DataFrame:
    """Build a candle frame with exactly the given highs (and optional lows).

    Open/close are pinned inside the bar so OHLC invariants hold, letting each test
    state its price shape as a plain list and read like the chart it describes.
    """
    lows = lows if lows is not None else [h - 0.0010 for h in highs]
    candles = []
    for i, (high, low) in enumerate(zip(highs, lows, strict=True)):
        mid = (high + low) / 2
        candles.append(
            MarketCandle(
                timestamp=START + timedelta(minutes=timeframe.minutes * i),
                symbol=symbol,
                timeframe=timeframe,
                open=mid,
                high=high,
                low=low,
                close=mid,
                volume=1.0,
            )
        )
    return candles_to_frame(candles)


def highs_of(detector: SwingDetector, frame: pd.DataFrame):
    return [s for s in detector.detect(frame, Symbol.EURUSD, Timeframe.M5) if s.is_high]


def lows_of(detector: SwingDetector, frame: pd.DataFrame):
    return [s for s in detector.detect(frame, Symbol.EURUSD, Timeframe.M5) if not s.is_high]


# --------------------------------------------------------------------- config


class TestSwingConfig:
    def test_defaults(self):
        config = SwingConfig()
        assert config.left == 2
        assert config.right == 2
        assert config.tie_policy is TiePolicy.FIRST
        assert config.window_size == 5

    def test_right_zero_is_refused(self):
        """A zero-lag pivot is not a swing — it is just 'this bar's high'."""
        with pytest.raises(ValueError, match="confirmation lag"):
            SwingConfig(left=2, right=0)

    def test_left_zero_is_refused(self):
        with pytest.raises(ValueError, match="left must be >= 1"):
            SwingConfig(left=0, right=2)

    @pytest.mark.parametrize(("left", "right"), [(1, 1), (3, 1), (1, 5), (5, 5)])
    def test_asymmetric_windows_are_allowed(self, left, right):
        config = SwingConfig(left=left, right=right)
        assert config.window_size == left + right + 1

    def test_config_is_immutable(self):
        with pytest.raises(Exception):  # noqa: B017 - FrozenInstanceError
            SwingConfig().left = 9


# ------------------------------------------------------------------ detection


class TestBasicDetection:
    def test_single_swing_high(self):
        detector = SwingDetector(SwingConfig(left=2, right=2))
        # Peak at index 2.
        frame = frame_from_highs([1.00, 1.01, 1.05, 1.02, 1.01])

        swings = highs_of(detector, frame)
        assert len(swings) == 1
        assert swings[0].price_level == pytest.approx(1.05)
        assert swings[0].index == 2

    def test_single_swing_low(self):
        detector = SwingDetector(SwingConfig(left=2, right=2))
        frame = frame_from_highs([1.10] * 5, lows=[1.05, 1.04, 1.00, 1.03, 1.04])
        swings = lows_of(detector, frame)
        assert len(swings) == 1
        assert swings[0].price_level == pytest.approx(1.00)
        assert swings[0].index == 2
        assert swings[0].direction is Direction.BEARISH

    def test_event_and_confirmation_timestamps_differ_by_right_bars(self):
        """The core R2-02 semantic."""
        detector = SwingDetector(SwingConfig(left=2, right=2))
        frame = frame_from_highs([1.00, 1.01, 1.05, 1.02, 1.01])
        swing = highs_of(detector, frame)[0]

        assert swing.event_timestamp == START + timedelta(minutes=10)  # bar 2
        # Bar 4 opens at +20m and closes at +25m.
        assert swing.confirmation_timestamp == START + timedelta(minutes=25)
        assert swing.confirmation_timestamp > swing.event_timestamp
        assert swing.bars_to_confirm == 2

    @pytest.mark.parametrize("right", [1, 2, 3, 4])
    def test_confirmation_lag_scales_with_right(self, right):
        detector = SwingDetector(SwingConfig(left=1, right=right))
        highs = [1.00, 1.05] + [1.01] * right
        frame = frame_from_highs(highs)

        swing = highs_of(detector, frame)[0]
        expected = START + timedelta(minutes=5 * (1 + right) + 5)
        assert swing.confirmation_timestamp == expected

    def test_multiple_candidate_swings(self):
        detector = SwingDetector(SwingConfig(left=1, right=1))
        # Peaks at 1, 3, 5.
        frame = frame_from_highs([1.00, 1.05, 1.00, 1.06, 1.00, 1.07, 1.00])

        swings = highs_of(detector, frame)
        assert [s.index for s in swings] == [1, 3, 5]
        assert [pytest.approx(s.price_level) for s in swings] == [1.05, 1.06, 1.07]

    def test_highs_and_lows_are_detected_together(self):
        detector = SwingDetector(SwingConfig(left=1, right=1))
        frame = frame_from_highs(
            [1.00, 1.05, 1.00, 1.05, 1.00],
            lows=[0.99, 0.98, 0.95, 0.98, 0.99],
        )
        swings = detector.detect(frame, Symbol.EURUSD, Timeframe.M5)
        assert {s.direction for s in swings} == {Direction.BULLISH, Direction.BEARISH}

    def test_results_are_ordered_by_confirmation(self):
        detector = SwingDetector(SwingConfig(left=1, right=1))
        frame = frame_from_highs([1.00, 1.05, 1.00, 1.06, 1.00, 1.07, 1.00])
        swings = detector.detect(frame, Symbol.EURUSD, Timeframe.M5)
        confirmations = [s.confirmation_timestamp for s in swings]
        assert confirmations == sorted(confirmations)


class TestReferenceAndStrength:
    def test_reference_level_is_the_beaten_neighbour(self):
        detector = SwingDetector(SwingConfig(left=2, right=2))
        frame = frame_from_highs([1.00, 1.01, 1.05, 1.03, 1.02])

        swing = highs_of(detector, frame)[0]
        # Highest neighbour in [0..4] excluding the pivot is 1.03.
        assert swing.reference_level == pytest.approx(1.03)

    def test_strength_is_prominence_in_points(self):
        detector = SwingDetector(SwingConfig(left=2, right=2))
        frame = frame_from_highs([1.00, 1.01, 1.05, 1.03, 1.02])

        swing = highs_of(detector, frame)[0]
        expected = (1.05 - 1.03) / Symbol.EURUSD.spec.point_value
        assert swing.strength == pytest.approx(expected, rel=1e-6)

    def test_strength_is_never_negative(self):
        detector = SwingDetector(SwingConfig(left=1, right=1))
        frame = frame_from_highs([1.00, 1.05, 1.00, 1.06, 1.00])
        assert all(s.strength >= 0 for s in detector.detect(frame, Symbol.EURUSD, Timeframe.M5))

    def test_low_strength_uses_the_opposite_sign(self):
        detector = SwingDetector(SwingConfig(left=2, right=2))
        frame = frame_from_highs([1.10] * 5, lows=[1.05, 1.04, 1.00, 1.02, 1.03])

        swing = lows_of(detector, frame)[0]
        expected = (1.02 - 1.00) / Symbol.EURUSD.spec.point_value
        assert swing.strength == pytest.approx(expected, rel=1e-6)


# ---------------------------------------------------------------- tie policies


class TestTiePolicies:
    #: A two-bar plateau at indices 2 and 3, both at 1.05.
    PLATEAU = [1.00, 1.01, 1.05, 1.05, 1.01, 1.00]

    def test_first_policy_takes_the_earliest_plateau_bar(self):
        """The default. One swing per plateau, at the earliest — the honest timestamp."""
        detector = SwingDetector(SwingConfig(left=2, right=2, tie_policy=TiePolicy.FIRST))
        swings = highs_of(detector, frame_from_highs(self.PLATEAU))
        assert [s.index for s in swings] == [2]

    def test_last_policy_takes_the_latest_plateau_bar(self):
        detector = SwingDetector(SwingConfig(left=2, right=2, tie_policy=TiePolicy.LAST))
        swings = highs_of(detector, frame_from_highs(self.PLATEAU))
        assert [s.index for s in swings] == [3]

    def test_strict_policy_rejects_the_plateau_entirely(self):
        detector = SwingDetector(SwingConfig(left=2, right=2, tie_policy=TiePolicy.STRICT))
        assert highs_of(detector, frame_from_highs(self.PLATEAU)) == []

    def test_all_policy_takes_every_plateau_bar(self):
        detector = SwingDetector(SwingConfig(left=2, right=2, tie_policy=TiePolicy.ALL))
        swings = highs_of(detector, frame_from_highs(self.PLATEAU))
        assert [s.index for s in swings] == [2, 3]

    def test_policies_agree_when_there_is_no_tie(self):
        """A policy must only matter on plateaus."""
        clean = [1.00, 1.01, 1.05, 1.02, 1.00]
        results = {}
        for policy in TiePolicy:
            detector = SwingDetector(SwingConfig(left=2, right=2, tie_policy=policy))
            results[policy] = [s.index for s in highs_of(detector, frame_from_highs(clean))]
        assert all(indices == [2] for indices in results.values())

    def test_three_bar_plateau(self):
        highs = [1.00, 1.01, 1.05, 1.05, 1.05, 1.01, 1.00]
        first = SwingDetector(SwingConfig(left=2, right=2, tie_policy=TiePolicy.FIRST))
        last = SwingDetector(SwingConfig(left=2, right=2, tie_policy=TiePolicy.LAST))
        every = SwingDetector(SwingConfig(left=2, right=2, tie_policy=TiePolicy.ALL))

        assert [s.index for s in highs_of(first, frame_from_highs(highs))] == [2]
        assert [s.index for s in highs_of(last, frame_from_highs(highs))] == [4]
        assert [s.index for s in highs_of(every, frame_from_highs(highs))] == [2, 3, 4]

    def test_plateau_strength_is_zero_under_first(self):
        """Meaningful, not a defect: the pivot stands zero points clear of its twin."""
        detector = SwingDetector(SwingConfig(left=2, right=2, tie_policy=TiePolicy.FIRST))
        swing = highs_of(detector, frame_from_highs(self.PLATEAU))[0]
        assert swing.strength == pytest.approx(0.0)

    def test_flat_series_yields_no_swings_under_strict(self):
        detector = SwingDetector(SwingConfig(left=2, right=2, tie_policy=TiePolicy.STRICT))
        assert detector.detect(frame_from_highs([1.05] * 10), Symbol.EURUSD, Timeframe.M5) == []

    def test_flat_series_under_all_marks_every_interior_bar(self):
        detector = SwingDetector(SwingConfig(left=2, right=2, tie_policy=TiePolicy.ALL))
        swings = highs_of(detector, frame_from_highs([1.05] * 10))
        assert [s.index for s in swings] == [2, 3, 4, 5, 6, 7]


# ------------------------------------------------------------------ boundaries


class TestBoundaries:
    def test_insufficient_history_is_not_an_error(self):
        detector = SwingDetector(SwingConfig(left=2, right=2))
        for count in range(0, 5):
            frame = frame_from_highs([1.0 + 0.01 * i for i in range(count)])
            assert detector.detect(frame, Symbol.EURUSD, Timeframe.M5) == []

    def test_exactly_window_size_bars_can_yield_a_swing(self):
        detector = SwingDetector(SwingConfig(left=2, right=2))
        frame = frame_from_highs([1.00, 1.01, 1.05, 1.01, 1.00])
        assert len(highs_of(detector, frame)) == 1

    def test_a_pivot_in_the_first_left_bars_cannot_be_detected(self):
        """No left window exists, so no verdict is possible — correctly silent."""
        detector = SwingDetector(SwingConfig(left=2, right=2))
        frame = frame_from_highs([1.05, 1.01, 1.00, 1.01, 1.00])
        assert highs_of(detector, frame) == []

    def test_a_pivot_in_the_last_right_bars_is_not_yet_confirmed(self):
        """The decisive edge: the peak is visible on the chart but not confirmable."""
        detector = SwingDetector(SwingConfig(left=2, right=2))
        frame = frame_from_highs([1.00, 1.01, 1.00, 1.01, 1.05])
        assert highs_of(detector, frame) == []

    def test_empty_frame(self):
        detector = SwingDetector(SwingConfig(left=2, right=2))
        assert detector.detect(frame_from_highs([]), Symbol.EURUSD, Timeframe.M5) == []

    def test_the_window_is_positional_over_bars_present_not_wall_clock(self):
        """A documented limitation, pinned so it cannot change silently.

        The fractal window counts BARS, not elapsed time. If a market gap sits inside
        the window, the confirming bar arrives later in wall-clock terms and the
        confirmation lag exceeds ``(right + 1) * bar_duration``. That is correct — the
        pivot genuinely could not be known until those bars existed — but it means the
        lag is not a fixed duration. See ``docs/ict/swings.md`` §7.
        """
        detector = SwingDetector(SwingConfig(left=1, right=1))
        frame = frame_from_highs([1.00, 1.05, 1.01])

        # Push the confirming bar two days into the future, as a weekend would.
        gapped = frame.copy(deep=True)
        gapped.loc[2, "timestamp"] = gapped.loc[2, "timestamp"] + pd.Timedelta(days=2)

        contiguous_swing = highs_of(detector, frame)[0]
        gapped_swing = highs_of(detector, gapped)[0]

        # Same pivot, same price, same event timestamp...
        assert gapped_swing.event_timestamp == contiguous_swing.event_timestamp
        assert gapped_swing.price_level == contiguous_swing.price_level
        # ...but confirmation follows the confirming BAR, so it is two days later.
        assert gapped_swing.confirmation_timestamp > contiguous_swing.confirmation_timestamp
        assert gapped_swing.confirmation_timestamp - gapped_swing.event_timestamp > timedelta(days=1)

    def test_unsorted_input_is_sorted_first(self):
        detector = SwingDetector(SwingConfig(left=1, right=1))
        frame = frame_from_highs([1.00, 1.05, 1.00])
        shuffled = frame.iloc[[2, 0, 1]].reset_index(drop=True)
        assert [s.index for s in detector.detect(shuffled, Symbol.EURUSD, Timeframe.M5)] == [
            s.index for s in detector.detect(frame, Symbol.EURUSD, Timeframe.M5)
        ]


# -------------------------------------------------- vectorised vs naive reference


class TestAgainstReferenceImplementation:
    """The rolling-window path is fast but `shift(-right)` is easy to get off by one.
    These prove it against a transparently-correct O(n·window) version."""

    @pytest.mark.parametrize("policy", list(TiePolicy))
    @pytest.mark.parametrize(("left", "right"), [(1, 1), (2, 2), (3, 1), (1, 4)])
    def test_matches_reference_on_random_series(self, policy, left, right):
        import numpy as np

        rng = np.random.default_rng(20240308)
        # Deliberately coarse rounding so plateaus occur often.
        highs = np.round(1.08 + rng.normal(0, 0.002, 200), 4).tolist()
        lows = [h - 0.0005 for h in highs]
        frame = frame_from_highs(highs, lows)

        config = SwingConfig(left=left, right=right, tie_policy=policy)
        detector = SwingDetector(config)

        detected_high = [s.index for s in highs_of(detector, frame)]
        assert detected_high == reference_pivots(frame, config, find_high=True)

        detected_low = [s.index for s in lows_of(detector, frame)]
        assert detected_low == reference_pivots(frame, config, find_high=False)

    def test_reference_agrees_on_a_plateau(self):
        config = SwingConfig(left=2, right=2, tie_policy=TiePolicy.FIRST)
        frame = frame_from_highs([1.00, 1.01, 1.05, 1.05, 1.01, 1.00])
        assert reference_pivots(frame, config, find_high=True) == [2]


# ---------------------------------------------------------------------- events


class TestEvents:
    @pytest.fixture
    def detector(self):
        return SwingDetector(SwingConfig(left=2, right=2))

    @pytest.fixture
    def events(self, detector):
        frame = frame_from_highs([1.00, 1.01, 1.05, 1.02, 1.01, 1.00, 1.01])
        return detector.events(frame, Symbol.EURUSD, Timeframe.M5)

    def test_event_types(self, events):
        assert {e.event_type for e in events} <= {EventType.SWING_HIGH, EventType.SWING_LOW}

    def test_contract_fields_are_populated(self, events):
        assert events
        for event in events:
            assert event.symbol == "EURUSD"
            assert event.timeframe == "5m"
            assert event.price_level > 0
            assert event.reference_level is not None
            assert event.strength is not None

    def test_direction_matches_the_event_type(self, events):
        for event in events:
            if event.event_type is EventType.SWING_HIGH:
                assert event.direction is Direction.BULLISH
            else:
                assert event.direction is Direction.BEARISH

    def test_metadata_records_the_parameters(self, events):
        """Config travels with the event, so a stored dataset can always be traced
        back to the parameters that produced it."""
        for event in events:
            assert event.metadata["left"] == 2
            assert event.metadata["right"] == 2
            assert event.metadata["tie_policy"] == "first"

    def test_confirmation_lag_is_positive_on_every_event(self, events):
        assert events
        assert all(e.confirmation_lag > timedelta(0) for e in events)


# --------------------------------------------------------------- configurability


class TestConfigurability:
    def test_wider_right_window_finds_fewer_swings(self):
        import numpy as np

        rng = np.random.default_rng(7)
        highs = (1.08 + rng.normal(0, 0.002, 300)).tolist()
        frame = frame_from_highs(highs)

        narrow = len(highs_of(SwingDetector(SwingConfig(left=1, right=1)), frame))
        wide = len(highs_of(SwingDetector(SwingConfig(left=5, right=5)), frame))
        assert wide < narrow

    def test_with_config_returns_a_new_detector(self):
        base = SwingDetector(SwingConfig(left=2, right=2))
        wider = base.with_config(SwingConfig(left=4, right=4))
        assert base.config.left == 2
        assert wider.config.left == 4

    def test_detector_holds_no_hardcoded_window(self):
        """Changing only configuration must change the result."""
        frame = frame_from_highs([1.00, 1.01, 1.05, 1.02, 1.01])
        assert len(highs_of(SwingDetector(SwingConfig(left=2, right=2)), frame)) == 1
        assert highs_of(SwingDetector(SwingConfig(left=3, right=3)), frame) == []

    def test_settings_expose_swing_config(self, monkeypatch):
        from ict_kronos.app.config import Settings

        monkeypatch.setenv("ICT_SWING_LEFT", "4")
        monkeypatch.setenv("ICT_SWING_RIGHT", "6")
        monkeypatch.setenv("ICT_SWING_TIE_POLICY", "strict")

        swings = Settings.from_env().swings
        assert (swings.left, swings.right, swings.tie_policy) == (4, 6, "strict")
