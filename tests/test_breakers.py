"""R2-05.2 BreakerDetector — a failed Order Block that flips polarity.

Two rules carry this module: a **wick** through the block is not a failure, and **not
every broken Order Block is a Breaker** — the structure condition is required by
default, and a block that fails without one is recorded rather than promoted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame
from ict_kronos.ict import (
    BreakerBreakMode,
    BreakerConfig,
    BreakerDetector,
    Direction,
    EventType,
    OrderBlockDetector,
)

START = datetime(2024, 3, 8, 9, 0, tzinfo=UTC)
M5 = Timeframe.M5
SYM = Symbol.EURUSD
#: Structure needs confirmed swings; the tests below drive the detector without it and
#: assert the gate separately, which keeps the fixtures readable.
NO_STRUCTURE = BreakerConfig(require_structure_break=False)


def bars(spec, *, start=START, timeframe=M5):
    candles = [
        MarketCandle(
            timestamp=start + timedelta(minutes=timeframe.minutes * i),
            symbol=SYM,
            timeframe=timeframe,
            open=o,
            high=h,
            low=low,
            close=c,
            volume=1.0,
        )
        for i, (o, h, low, c) in enumerate(spec)
    ]
    return candles_to_frame(candles)


#: A bullish Order Block (zone 1.0030-1.0060) that is later closed through downward.
BULLISH_OB_THEN_FAILURE = [
    (1.0050, 1.0055, 1.0045, 1.0050),  # doji
    (1.0052, 1.0060, 1.0030, 1.0035),  # the OB candidate
    (1.0035, 1.0075, 1.0034, 1.0070),  # confirms the OB (close > 1.0060)
    (1.0070, 1.0072, 1.0055, 1.0058),  # drifts back down
    (1.0058, 1.0059, 1.0020, 1.0025),  # CLOSES 1.0025 < 1.0030 -> the OB fails
]


def detect(spec, config=NO_STRUCTURE):
    return BreakerDetector(config).detect(bars(spec), SYM, M5)


class TestFailureFlipsPolarity:
    def test_a_failed_bullish_order_block_becomes_a_bearish_breaker(self):
        breakers = detect(BULLISH_OB_THEN_FAILURE)
        assert len(breakers) == 1
        breaker = breakers[0]

        assert breaker.original_direction is Direction.BULLISH
        assert breaker.direction is Direction.BEARISH

    def test_the_zone_is_inherited_from_the_source_block(self):
        block = OrderBlockDetector().detect(bars(BULLISH_OB_THEN_FAILURE), SYM, M5)[0]
        breaker = detect(BULLISH_OB_THEN_FAILURE)[0]

        assert breaker.zone_top == pytest.approx(block.zone_top)
        assert breaker.zone_bottom == pytest.approx(block.zone_bottom)

    def test_provenance_points_at_the_source_block(self):
        block = OrderBlockDetector().detect(bars(BULLISH_OB_THEN_FAILURE), SYM, M5)[0]
        breaker = detect(BULLISH_OB_THEN_FAILURE)[0]

        assert breaker.source_order_block_id == block.order_block_id
        assert breaker.source_order_block_confirmation == block.confirmation_timestamp

    def test_the_source_block_is_not_mutated(self):
        frame = bars(BULLISH_OB_THEN_FAILURE)
        before = OrderBlockDetector().detect(frame, SYM, M5)
        BreakerDetector(NO_STRUCTURE).detect(frame, SYM, M5)
        after = OrderBlockDetector().detect(frame, SYM, M5)

        assert before == after


class TestCloseNotWick:
    def test_a_wick_below_the_far_edge_is_not_a_failure(self):
        wick_only = [
            *BULLISH_OB_THEN_FAILURE[:4],
            (1.0058, 1.0059, 1.0020, 1.0055),  # LOW 1.0020 but CLOSE 1.0055
        ]
        assert detect(wick_only) == []

    def test_the_naive_wick_mode_fires_where_close_mode_does_not(self):
        """The divergence proof."""
        wick_only = [
            *BULLISH_OB_THEN_FAILURE[:4],
            (1.0058, 1.0059, 1.0020, 1.0055),
        ]
        naive = BreakerConfig(require_structure_break=False, break_mode=BreakerBreakMode.WICK)

        assert detect(wick_only) == []
        assert len(detect(wick_only, naive)) == 1

    def test_the_naive_wick_mode_also_fires_earlier(self):
        sequence = [
            *BULLISH_OB_THEN_FAILURE[:4],
            (1.0058, 1.0059, 1.0020, 1.0055),  # wick through, close above
            (1.0055, 1.0056, 1.0018, 1.0022),  # the genuine close through
        ]
        naive = BreakerConfig(require_structure_break=False, break_mode=BreakerBreakMode.WICK)

        causal = detect(sequence)[0]
        early = detect(sequence, naive)[0]
        assert early.confirmation_timestamp < causal.confirmation_timestamp

    def test_close_mode_is_the_default(self):
        assert BreakerConfig().break_mode is BreakerBreakMode.CLOSE


class TestTheStructureGate:
    def test_the_structure_condition_is_required_by_default(self):
        assert BreakerConfig().require_structure_break is True

    def test_a_failure_without_structure_is_not_promoted(self):
        """The fixture has no confirmed structure break, so the default gate blocks it."""
        with_gate = BreakerDetector().detect(bars(BULLISH_OB_THEN_FAILURE), SYM, M5)
        without_gate = detect(BULLISH_OB_THEN_FAILURE)

        assert without_gate  # the block did fail...
        assert with_gate == []  # ...but it is not a Breaker

    def test_blocked_failures_are_recorded_not_silently_dropped(self):
        analysis = BreakerDetector().analyse(bars(BULLISH_OB_THEN_FAILURE), SYM, M5)
        assert analysis.breakers == []
        assert analysis.failed_without_structure


class TestConfirmationTiming:
    def test_confirmation_is_the_failing_bars_close_time(self):
        frame = bars(BULLISH_OB_THEN_FAILURE)
        breaker = detect(BULLISH_OB_THEN_FAILURE)[0]
        failing_open = frame["timestamp"].iloc[4].to_pydatetime()

        assert breaker.failure_timestamp == failing_open
        assert breaker.confirmation_timestamp == failing_open + M5.duration

    def test_confirmation_is_after_the_source_blocks(self):
        breaker = detect(BULLISH_OB_THEN_FAILURE)[0]
        assert breaker.confirmation_timestamp > breaker.source_order_block_confirmation

    def test_nothing_is_observable_before_the_failing_close(self):
        breaker = detect(BULLISH_OB_THEN_FAILURE)[0]
        assert not breaker.is_observable_at(breaker.confirmation_timestamp - timedelta(seconds=1))
        assert breaker.is_observable_at(breaker.confirmation_timestamp)

    def test_a_block_that_never_fails_yields_no_breaker(self):
        assert detect(BULLISH_OB_THEN_FAILURE[:4]) == []


class TestEvents:
    def test_the_event_type_reflects_the_flipped_direction(self):
        events = BreakerDetector(NO_STRUCTURE).events(bars(BULLISH_OB_THEN_FAILURE), SYM, M5)
        assert events[0].event_type is EventType.BREAKER_BEARISH
        assert events[0].metadata["original_direction"] == "bullish"

    def test_the_event_carries_provenance(self):
        events = BreakerDetector(NO_STRUCTURE).events(bars(BULLISH_OB_THEN_FAILURE), SYM, M5)
        assert events[0].metadata["source_order_block_id"]

    def test_the_event_never_leaks(self):
        events = BreakerDetector(NO_STRUCTURE).events(bars(BULLISH_OB_THEN_FAILURE), SYM, M5)
        assert events[0].confirmation_timestamp > events[0].event_timestamp


class TestConfigValidation:
    @pytest.mark.parametrize(
        "kwargs, match",
        [
            ({"structure_window_bars": 0}, "structure_window_bars"),
            ({"full_fill_threshold": 0.0}, "full_fill_threshold"),
        ],
    )
    def test_invalid_configuration_is_rejected(self, kwargs, match):
        with pytest.raises(ValueError, match=match):
            BreakerConfig(**kwargs)

    def test_no_order_blocks_means_no_breakers(self):
        flat = [(1.0000, 1.0010, 0.9990, 1.0005) for _ in range(6)]
        assert detect(flat) == []
