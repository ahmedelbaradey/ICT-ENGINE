"""R2-05.2 OrderBlockDetector — the opposing candle/group, closed through.

    bullish OB   last down-close candle/group, then a later bar CLOSES ABOVE its high
    bearish OB   last up-close candle/group,   then a later bar CLOSES BELOW its low

Two claims carry the story and each has a test class: an Order Block does **not**
require an FVG, and it is **not** observable when its candidate closes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame
from ict_kronos.ict import (
    Direction,
    EventType,
    ObGrouping,
    ObStatus,
    ObZoneGeometry,
    OrderBlockConfig,
    OrderBlockDetector,
)

START = datetime(2024, 3, 8, 9, 0, tzinfo=UTC)
M5 = Timeframe.M5
SYM = Symbol.EURUSD


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


def detect(spec, config=None):
    return OrderBlockDetector(config or OrderBlockConfig()).detect(bars(spec), SYM, M5)


#: One down-close candle (the candidate), then a bar closing above its high.
SINGLE_BULLISH = [
    (1.0050, 1.0055, 1.0045, 1.0050),  # doji — belongs to no run, forms no block
    (1.0052, 1.0060, 1.0030, 1.0035),  # THE candidate: down-close, high 1.0060
    (1.0035, 1.0075, 1.0034, 1.0070),  # closes 1.0070 > 1.0060  -> confirms
    (1.0070, 1.0080, 1.0055, 1.0078),  # low kept BELOW 1.0060 so no FVG prints here
]

#: One up-close candidate, then a bar closing below its low.
SINGLE_BEARISH = [
    (1.0050, 1.0055, 1.0045, 1.0050),  # doji — belongs to no run, forms no block
    (1.0048, 1.0070, 1.0040, 1.0065),  # THE candidate: up-close, low 1.0040
    (1.0065, 1.0066, 1.0030, 1.0035),  # closes 1.0035 < 1.0040  -> confirms
    (1.0035, 1.0040, 1.0025, 1.0028),
]

#: Three consecutive down-close candles, then a close above the group's high.
GROUP_BULLISH = [
    (1.0080, 1.0085, 1.0075, 1.0080),  # doji — belongs to no run, forms no block
    (1.0082, 1.0090, 1.0070, 1.0072),  # group member 1 — group high 1.0090
    (1.0072, 1.0075, 1.0055, 1.0058),  # group member 2
    (1.0058, 1.0060, 1.0040, 1.0045),  # group member 3 — group low 1.0040
    (1.0045, 1.0095, 1.0044, 1.0092),  # closes 1.0092 > 1.0090 -> confirms
]


class TestSingleCandle:
    def test_a_bullish_order_block_is_the_down_close_candle(self):
        blocks = detect(SINGLE_BULLISH)
        assert len(blocks) == 1
        block = blocks[0]

        assert block.direction is Direction.BULLISH
        assert block.candle_count == 1
        assert block.zone_top == pytest.approx(1.0060)
        assert block.zone_bottom == pytest.approx(1.0030)

    def test_a_bearish_order_block_is_the_up_close_candle(self):
        blocks = detect(SINGLE_BEARISH)
        assert len(blocks) == 1
        block = blocks[0]

        assert block.direction is Direction.BEARISH
        assert block.zone_top == pytest.approx(1.0070)
        assert block.zone_bottom == pytest.approx(1.0040)

    def test_the_source_candle_is_recorded(self):
        frame = bars(SINGLE_BULLISH)
        block = detect(SINGLE_BULLISH)[0]
        assert block.source_candle_timestamps == (frame["timestamp"].iloc[1].to_pydatetime(),)

    def test_the_breaking_close_is_recorded(self):
        block = detect(SINGLE_BULLISH)[0]
        assert block.break_close == pytest.approx(1.0070)


class TestTheCloseThroughRequirement:
    def test_a_candidate_never_closed_through_is_not_an_order_block(self):
        """Not a pending one, not a weak one — it does not exist."""
        never = [
            (1.0050, 1.0055, 1.0045, 1.0050),
            (1.0052, 1.0060, 1.0030, 1.0035),  # candidate, high 1.0060
            (1.0035, 1.0058, 1.0034, 1.0055),  # never closes above 1.0060
            (1.0055, 1.0059, 1.0050, 1.0057),
        ]
        assert detect(never) == []

    def test_a_wick_through_the_high_confirms_nothing(self):
        """The bar trades above 1.0060 but closes below it. No Order Block."""
        wick_only = [
            (1.0050, 1.0055, 1.0045, 1.0050),
            (1.0052, 1.0060, 1.0030, 1.0035),  # candidate, high 1.0060
            (1.0035, 1.0075, 1.0034, 1.0058),  # HIGH 1.0075 but CLOSE 1.0058
            (1.0058, 1.0059, 1.0050, 1.0055),
        ]
        assert detect(wick_only) == []

    def test_a_close_exactly_at_the_high_is_not_a_break(self):
        equal = [
            (1.0050, 1.0055, 1.0045, 1.0050),
            (1.0052, 1.0060, 1.0030, 1.0035),
            (1.0035, 1.0062, 1.0034, 1.0060),  # closes exactly 1.0060
            (1.0060, 1.0061, 1.0055, 1.0058),
        ]
        assert detect(equal) == []

    def test_the_confirmation_window_is_bounded(self):
        drift = [
            (1.0050, 1.0055, 1.0045, 1.0050),
            (1.0052, 1.0060, 1.0030, 1.0035),
            *[(1.0035, 1.0040, 1.0030, 1.0036) for _ in range(10)],
            (1.0036, 1.0075, 1.0035, 1.0070),  # the break, 11 bars later
        ]
        assert len(detect(drift)) == 1
        assert detect(drift, OrderBlockConfig(max_bars_to_confirm=5)) == []


class TestMultiCandleGroup:
    def test_the_group_is_the_maximal_contiguous_run(self):
        blocks = detect(GROUP_BULLISH)
        assert len(blocks) == 1
        block = blocks[0]

        assert block.candle_count == 3
        assert block.zone_top == pytest.approx(1.0090)  # max high across the group
        assert block.zone_bottom == pytest.approx(1.0040)  # min low across the group

    def test_single_candle_mode_uses_only_the_last_of_the_run(self):
        blocks = detect(GROUP_BULLISH, OrderBlockConfig(grouping=ObGrouping.SINGLE_CANDLE))
        assert len(blocks) == 1
        block = blocks[0]

        assert block.candle_count == 1
        assert block.zone_top == pytest.approx(1.0060)  # the LAST member's high only
        assert block.zone_bottom == pytest.approx(1.0040)

    def test_the_group_demands_a_larger_move_to_confirm(self):
        """The group's high is above the last member's, so grouping is stricter."""
        modest = [*GROUP_BULLISH[:4], (1.0045, 1.0070, 1.0044, 1.0065)]

        assert detect(modest) == []  # 1.0065 < group high 1.0090
        assert len(detect(modest, OrderBlockConfig(grouping=ObGrouping.SINGLE_CANDLE))) == 1

    def test_every_group_candle_is_recorded_in_order(self):
        frame = bars(GROUP_BULLISH)
        block = detect(GROUP_BULLISH)[0]
        expected = tuple(t.to_pydatetime() for t in frame["timestamp"].iloc[1:4])

        assert block.source_candle_timestamps == expected

    def test_a_doji_terminates_the_run(self):
        """close == open is neither up- nor down-close, so it belongs to no group."""
        with_doji = [
            (1.0080, 1.0085, 1.0075, 1.0080),
            (1.0082, 1.0090, 1.0070, 1.0072),  # down-close
            (1.0072, 1.0074, 1.0068, 1.0072),  # DOJI — terminates the run
            (1.0072, 1.0075, 1.0055, 1.0058),  # down-close: a NEW run starts here
            (1.0058, 1.0095, 1.0057, 1.0092),
        ]
        blocks = detect(with_doji)

        # Two separate single-candle runs, never one merged three-candle group.
        assert blocks
        assert all(b.candle_count == 1 for b in blocks)
        tops = {round(b.zone_top, 5) for b in blocks}
        assert {1.0090, 1.0075} <= tops


class TestNoFvgRequirement:
    """OB formation and FVG formation are different events."""

    def test_an_order_block_is_confirmed_without_any_fvg(self):
        from ict_kronos.ict import FvgDetector

        frame = bars(SINGLE_BULLISH)
        assert FvgDetector().detect(frame, SYM, M5) == []  # genuinely no gap here
        assert len(detect(SINGLE_BULLISH)) == 1  # and yet an Order Block exists

    def test_related_fvg_id_is_none_when_no_gap_printed(self):
        assert detect(SINGLE_BULLISH)[0].related_fvg_id is None

    def test_requiring_an_fvg_is_opt_in_and_suppresses_it(self):
        strict = OrderBlockConfig(require_fvg=True)
        assert detect(SINGLE_BULLISH, strict) == []

    def test_the_default_config_does_not_require_an_fvg(self):
        assert OrderBlockConfig().require_fvg is False


class TestConfirmationTiming:
    def test_confirmation_is_the_breaking_bars_close_time(self):
        frame = bars(SINGLE_BULLISH)
        block = detect(SINGLE_BULLISH)[0]
        break_open = frame["timestamp"].iloc[2].to_pydatetime()

        assert block.break_bar_timestamp == break_open
        assert block.confirmation_timestamp == break_open + M5.duration

    def test_event_timestamp_is_the_candidates_own_open(self):
        frame = bars(SINGLE_BULLISH)
        block = detect(SINGLE_BULLISH)[0]
        assert block.event_timestamp == frame["timestamp"].iloc[1].to_pydatetime()

    def test_confirmation_lags_the_candidate_by_more_than_one_bar(self):
        """The property that makes this detector non-trivial."""
        block = detect(SINGLE_BULLISH)[0]
        assert block.confirmation_timestamp - block.event_timestamp > M5.duration
        assert block.bars_to_confirm >= 1

    def test_nothing_is_observable_before_the_confirming_close(self):
        block = detect(SINGLE_BULLISH)[0]
        assert not block.is_observable_at(block.confirmation_timestamp - timedelta(seconds=1))
        assert block.is_observable_at(block.confirmation_timestamp)

    def test_the_candidate_closing_does_not_make_it_observable(self):
        frame = bars(SINGLE_BULLISH)
        block = detect(SINGLE_BULLISH)[0]
        candidate_close = frame["timestamp"].iloc[1].to_pydatetime() + M5.duration

        assert not block.is_observable_at(candidate_close)


class TestZoneGeometry:
    def test_full_range_is_the_default(self):
        assert OrderBlockConfig().geometry is ObZoneGeometry.FULL_RANGE

    def test_body_geometry_narrows_the_zone(self):
        body = detect(SINGLE_BULLISH, OrderBlockConfig(geometry=ObZoneGeometry.BODY))
        assert len(body) == 1
        assert body[0].zone_top == pytest.approx(1.0052)  # open, not high
        assert body[0].zone_bottom == pytest.approx(1.0035)  # close, not low

    def test_the_mean_threshold_is_the_midpoint(self):
        block = detect(SINGLE_BULLISH)[0]
        assert block.mean_threshold == pytest.approx((1.0060 + 1.0030) / 2)
        assert block.mean_threshold == block.midpoint

    def test_the_far_edge_is_the_invalidation_side(self):
        assert detect(SINGLE_BULLISH)[0].far_edge == pytest.approx(1.0030)
        assert detect(SINGLE_BEARISH)[0].far_edge == pytest.approx(1.0070)


class TestLifecycle:
    def test_mitigation_and_invalidation_are_different_events(self):
        """A wick to the far edge mitigates; only a CLOSE beyond it invalidates."""
        touched = [
            *SINGLE_BULLISH,
            (1.0078, 1.0079, 1.0030, 1.0070),  # wicks to 1.0030 — full fill, no close beyond
        ]
        analysis = OrderBlockDetector().analyse(bars(touched), SYM, M5)
        block_id = analysis.blocks[0].order_block_id

        assert analysis.status[block_id] is ObStatus.MITIGATED
        assert block_id not in analysis.invalidated_at

    def test_a_close_beyond_the_far_edge_invalidates(self):
        failed = [
            *SINGLE_BULLISH,
            (1.0078, 1.0079, 1.0020, 1.0025),  # closes 1.0025 < 1.0030
        ]
        analysis = OrderBlockDetector().analyse(bars(failed), SYM, M5)
        block_id = analysis.blocks[0].order_block_id

        assert analysis.status[block_id] is ObStatus.INVALIDATED
        assert block_id in analysis.invalidated_at

    def test_an_untouched_block_stays_active(self):
        """Truncated before the bar that dips back into the zone."""
        analysis = OrderBlockDetector().analyse(bars(SINGLE_BULLISH[:3]), SYM, M5)
        assert analysis.status[analysis.blocks[0].order_block_id] is ObStatus.ACTIVE

    def test_price_re_entering_the_zone_partially_fills_it(self):
        """The full fixture's trailing bar dips to 1.0055, inside [1.0030, 1.0060]."""
        analysis = OrderBlockDetector().analyse(bars(SINGLE_BULLISH), SYM, M5)
        assert analysis.status[analysis.blocks[0].order_block_id] is ObStatus.PARTIALLY_FILLED


class TestEvents:
    def test_the_event_type_matches_the_direction(self):
        detector = OrderBlockDetector()
        assert detector.events(bars(SINGLE_BULLISH), SYM, M5)[0].event_type is EventType.ORDER_BLOCK_BULLISH
        assert detector.events(bars(SINGLE_BEARISH), SYM, M5)[0].event_type is EventType.ORDER_BLOCK_BEARISH

    def test_the_event_carries_the_source_candles(self):
        event = OrderBlockDetector().events(bars(GROUP_BULLISH), SYM, M5)[0]
        assert event.metadata["candle_count"] == 3
        assert len(event.metadata["source_candle_timestamps"]) == 3

    def test_the_event_never_leaks(self):
        event = OrderBlockDetector().events(bars(SINGLE_BULLISH), SYM, M5)[0]
        assert event.confirmation_timestamp > event.event_timestamp


class TestConfigValidation:
    @pytest.mark.parametrize(
        "kwargs, match",
        [
            ({"break_tolerance_points": -1.0}, "break_tolerance_points"),
            ({"max_bars_to_confirm": 0}, "max_bars_to_confirm"),
            ({"displacement_lookback": 0}, "displacement_lookback"),
            ({"displacement_factor": 0.0}, "displacement_factor"),
            ({"full_fill_threshold": 0.0}, "full_fill_threshold"),
        ],
    )
    def test_invalid_configuration_is_rejected(self, kwargs, match):
        with pytest.raises(ValueError, match=match):
            OrderBlockConfig(**kwargs)

    def test_insufficient_history_is_not_an_error(self):
        assert detect([]) == []
        assert detect(SINGLE_BULLISH[:1]) == []
