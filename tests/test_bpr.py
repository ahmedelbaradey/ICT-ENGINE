"""R2-05.2 BprDetector — the intersection of two opposing Fair Value Gaps.

    bullish FVG = [A, B]     bearish FVG = [C, D]
    BPR = [max(A, C), min(B, D)]   iff   max(A, C) < min(B, D)

Touching is not overlapping, same-polarity pairs are not BPRs, and the range is not
knowable until BOTH gaps are.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame
from ict_kronos.ict import (
    BprConfig,
    BprDetector,
    BprPolarity,
    Direction,
    EventType,
    FvgDetector,
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


#: Bars 0-2 leave a bullish gap [1.0050, 1.0100]; bars 3-5 come back down and leave a
#: bearish gap [1.0070, 1.0120]. The intersection is [1.0070, 1.0100].
OVERLAPPING = [
    (1.0020, 1.0050, 1.0010, 1.0045),  # C1 up   — high 1.0050
    (1.0045, 1.0110, 1.0040, 1.0105),  # C2 up
    (1.0105, 1.0130, 1.0100, 1.0125),  # C3 up   — low 1.0100  => bull gap [1.0050, 1.0100]
    (1.0125, 1.0140, 1.0105, 1.0135),  # C1 down — low 1.0105 (kept under 1.0110 so no extra gap)
    (1.0135, 1.0138, 1.0060, 1.0065),  # C2 down
    (1.0065, 1.0070, 1.0030, 1.0035),  # C3 down — high 1.0070 => bear gap [1.0070, 1.0105]
]


def detect(spec=OVERLAPPING, config=None):
    return BprDetector(config or BprConfig()).detect(bars(spec), SYM, M5)


class TestTheSourceGaps:
    def test_the_fixture_contains_one_gap_of_each_polarity(self):
        zones = FvgDetector().detect(bars(OVERLAPPING), SYM, M5)
        directions = [z.direction for z in zones]

        assert Direction.BULLISH in directions
        assert Direction.BEARISH in directions


class TestIntersectionGeometry:
    def test_the_zone_is_the_intersection(self):
        ranges = detect()
        assert len(ranges) == 1
        item = ranges[0]

        assert item.zone_bottom == pytest.approx(1.0070)  # max of the two bottoms
        assert item.zone_top == pytest.approx(1.0100)  # min of the two tops

    def test_the_zone_is_not_the_union(self):
        item = detect()[0]
        assert item.zone_bottom > 1.0050  # the bullish gap's bottom
        assert item.zone_top < 1.0105  # the bearish gap's top

    def test_provenance_names_both_gaps(self):
        item = detect()[0]
        zones = {z.zone_id: z for z in FvgDetector().detect(bars(OVERLAPPING), SYM, M5)}

        assert item.bullish_fvg_id in zones
        assert item.bearish_fvg_id in zones
        assert set(item.source_fvg_ids) == {item.bullish_fvg_id, item.bearish_fvg_id}
        assert zones[item.bullish_fvg_id].direction is Direction.BULLISH
        assert zones[item.bearish_fvg_id].direction is Direction.BEARISH

    def test_the_overlap_width_is_recorded(self):
        item = detect()[0]
        assert item.overlap_points == pytest.approx(300.0)  # 0.0030 at a 1e-5 point


class TestNoOverlapNoBpr:
    def test_gaps_that_do_not_overlap_produce_nothing(self):
        apart = [
            (1.0020, 1.0050, 1.0010, 1.0045),
            (1.0045, 1.0110, 1.0040, 1.0105),
            (1.0105, 1.0130, 1.0100, 1.0125),  # bull gap [1.0050, 1.0100]
            (1.0125, 1.0400, 1.0120, 1.0395),  # jump far away
            (1.0395, 1.0398, 1.0300, 1.0305),
            (1.0305, 1.0310, 1.0270, 1.0275),  # bear gap far above the bull gap
        ]
        assert detect(apart) == []

    def test_touching_at_a_single_price_is_not_an_overlap(self):
        """Strictly positive overlap is required, mirroring R2-05's rule on gaps."""
        item = detect()[0]
        exact = BprConfig(min_overlap_points=item.overlap_points)

        assert detect(OVERLAPPING, exact) == []  # equality is refused
        assert detect(OVERLAPPING, BprConfig(min_overlap_points=item.overlap_points - 1))

    def test_same_polarity_gaps_never_pair(self):
        same = [
            (1.0020, 1.0050, 1.0010, 1.0045),
            (1.0045, 1.0110, 1.0040, 1.0105),
            (1.0105, 1.0130, 1.0100, 1.0125),  # bullish gap
            (1.0125, 1.0135, 1.0120, 1.0130),
            (1.0130, 1.0190, 1.0128, 1.0185),
            (1.0185, 1.0200, 1.0140, 1.0195),  # another bullish gap
        ]
        zones = FvgDetector().detect(bars(same), SYM, M5)
        assert all(z.direction is Direction.BULLISH for z in zones)
        assert detect(same) == []


class TestConfirmationOrdering:
    def test_confirmation_is_the_LATER_gaps(self):
        item = detect()[0]
        zones = {z.zone_id: z for z in FvgDetector().detect(bars(OVERLAPPING), SYM, M5)}
        both = [zones[i].confirmation_timestamp for i in item.source_fvg_ids]

        assert item.confirmation_timestamp == max(both)
        assert item.confirmation_timestamp > min(both)  # NOT the earlier one

    def test_nothing_is_observable_before_both_gaps_are(self):
        item = detect()[0]
        zones = {z.zone_id: z for z in FvgDetector().detect(bars(OVERLAPPING), SYM, M5)}

        for source_id in item.source_fvg_ids:
            assert zones[source_id].confirmation_timestamp <= item.confirmation_timestamp
        assert not item.is_observable_at(item.confirmation_timestamp - timedelta(seconds=1))

    def test_the_event_timestamp_is_the_later_gaps_formation(self):
        item = detect()[0]
        zones = {z.zone_id: z for z in FvgDetector().detect(bars(OVERLAPPING), SYM, M5)}
        formations = [zones[i].formation_timestamp for i in item.source_fvg_ids]
        assert item.event_timestamp == max(formations)


class TestPolarity:
    def test_direction_defaults_to_the_later_gaps_polarity(self):
        item = detect()[0]
        zones = {z.zone_id: z for z in FvgDetector().detect(bars(OVERLAPPING), SYM, M5)}
        later = max((zones[i] for i in item.source_fvg_ids), key=lambda z: z.confirmation_timestamp)
        assert item.direction is later.direction

    def test_neutral_polarity_is_configurable(self):
        item = detect(OVERLAPPING, BprConfig(polarity=BprPolarity.NEUTRAL))[0]
        assert item.direction is Direction.NEUTRAL

    def test_later_fvg_is_the_default(self):
        assert BprConfig().polarity is BprPolarity.LATER_FVG


class TestAdjacency:
    def test_pairing_is_bounded_in_time(self):
        assert detect(OVERLAPPING, BprConfig(max_bars_between=1)) == []
        assert detect(OVERLAPPING, BprConfig(max_bars_between=100))

    def test_bars_between_is_recorded(self):
        assert detect()[0].bars_between >= 1


class TestEvents:
    def test_the_event_type_is_balanced_price_range(self):
        events = BprDetector().events(bars(OVERLAPPING), SYM, M5)
        assert events[0].event_type is EventType.BALANCED_PRICE_RANGE

    def test_the_event_carries_both_source_ids(self):
        event = BprDetector().events(bars(OVERLAPPING), SYM, M5)[0]
        assert len(event.metadata["source_fvg_ids"]) == 2

    def test_the_event_never_leaks(self):
        event = BprDetector().events(bars(OVERLAPPING), SYM, M5)[0]
        assert event.confirmation_timestamp >= event.event_timestamp


class TestConfigValidation:
    @pytest.mark.parametrize(
        "kwargs, match",
        [
            ({"max_bars_between": 0}, "max_bars_between"),
            ({"min_overlap_points": -1.0}, "min_overlap_points"),
            ({"full_fill_threshold": 0.0}, "full_fill_threshold"),
        ],
    )
    def test_invalid_configuration_is_rejected(self, kwargs, match):
        with pytest.raises(ValueError, match=match):
            BprConfig(**kwargs)

    def test_fewer_than_two_gaps_yields_nothing(self):
        flat = [(1.0000, 1.0010, 0.9990, 1.0005) for _ in range(6)]
        assert detect(flat) == []
