"""R2-05.2 IfvgDetector — inversion as a state transition of a confirmed FVG.

The claim this file exists to defend:

    a wick that fills a gap 100%   -> MITIGATED, and NOT an IFVG
    a close beyond the far edge    -> INVERTED, and an IFVG exists

Mitigation and inversion are different questions about the same zone, and the whole
concept is worthless if they collapse into each other.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame
from ict_kronos.ict import (
    Direction,
    EventType,
    FvgDetector,
    FvgStatus,
    IfvgConfig,
    IfvgDetector,
    InversionTrigger,
    ZoneStatus,
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


#: A bullish FVG over bars 0-2: low[2] 1.0100 > high[0] 1.0050, so the gap is
#: [1.0050, 1.0100]. Bar 3 onwards decides what happens to it.
BULLISH_GAP = [
    (1.0020, 1.0050, 1.0010, 1.0045),  # C1 — high 1.0050
    (1.0045, 1.0110, 1.0040, 1.0105),  # C2 — the displacement candle
    (1.0105, 1.0130, 1.0100, 1.0125),  # C3 — low 1.0100  => gap [1.0050, 1.0100]
]


def gap_zone():
    zones = FvgDetector().detect(bars(BULLISH_GAP), SYM, M5)
    assert len(zones) == 1
    return zones[0]


def detect(extra, config=None):
    return IfvgDetector(config or IfvgConfig()).detect(bars([*BULLISH_GAP, *extra]), SYM, M5)


class TestTheSourceGap:
    def test_the_fixture_really_contains_one_bullish_gap(self):
        zone = gap_zone()
        assert zone.direction is Direction.BULLISH
        assert zone.top == pytest.approx(1.0100)
        assert zone.bottom == pytest.approx(1.0050)


class TestMitigationIsNotInversion:
    """The central distinction."""

    def test_a_wick_that_fully_fills_the_gap_does_not_invert_it(self):
        # Trades all the way down to 1.0045 (below the gap) but CLOSES at 1.0120.
        wick_fill = [(1.0125, 1.0130, 1.0045, 1.0120)]

        fvg = FvgDetector().analyse(bars([*BULLISH_GAP, *wick_fill]), SYM, M5)
        assert fvg.status[gap_zone().zone_id] is FvgStatus.MITIGATED  # fully filled...
        assert detect(wick_fill) == []  # ...and yet NOT inverted

    def test_a_close_beyond_the_far_edge_does_invert_it(self):
        closed_through = [(1.0125, 1.0130, 1.0040, 1.0045)]
        zones = detect(closed_through)

        assert len(zones) == 1
        assert zones[0].close_through_price == pytest.approx(1.0045)

    def test_a_close_inside_the_zone_is_not_enough_by_default(self):
        """Closing at 1.0075 is inside [1.0050, 1.0100] but not beyond its far edge."""
        inside = [(1.0125, 1.0130, 1.0070, 1.0075)]
        assert detect(inside) == []

    def test_the_looser_trigger_accepts_a_close_inside(self):
        inside = [(1.0125, 1.0130, 1.0070, 1.0075)]
        loose = IfvgConfig(trigger=InversionTrigger.CLOSE_INSIDE_ZONE)
        assert len(detect(inside, loose)) == 1

    def test_the_analysis_records_gaps_mitigated_without_inverting(self):
        wick_fill = [(1.0125, 1.0130, 1.0045, 1.0120)]
        analysis = IfvgDetector().analyse(bars([*BULLISH_GAP, *wick_fill]), SYM, M5)

        assert analysis.zones == []
        assert gap_zone().zone_id in analysis.mitigated_without_inversion


class TestPolarityFlip:
    def test_a_bullish_gap_produces_a_bearish_ifvg(self):
        zone = detect([(1.0125, 1.0130, 1.0040, 1.0045)])[0]
        assert zone.original_direction is Direction.BULLISH
        assert zone.direction is Direction.BEARISH

    def test_the_geometry_is_inherited_unchanged(self):
        source = gap_zone()
        zone = detect([(1.0125, 1.0130, 1.0040, 1.0045)])[0]

        assert zone.zone_top == pytest.approx(source.top)
        assert zone.zone_bottom == pytest.approx(source.bottom)

    def test_provenance_points_at_the_source_gap(self):
        zone = detect([(1.0125, 1.0130, 1.0040, 1.0045)])[0]
        assert zone.source_fvg_id == gap_zone().zone_id
        assert zone.source_fvg_confirmation == gap_zone().confirmation_timestamp

    def test_inversion_is_terminal_the_zone_does_not_flip_back(self):
        both_ways = [
            (1.0125, 1.0130, 1.0040, 1.0045),  # inverts
            (1.0045, 1.0150, 1.0040, 1.0145),  # closes back above the zone
        ]
        zones = detect(both_ways)
        assert len(zones) == 1  # one inversion per gap, ever


class TestConfirmationTiming:
    def test_confirmation_is_the_inverting_bars_close_time(self):
        frame = bars([*BULLISH_GAP, (1.0125, 1.0130, 1.0040, 1.0045)])
        zone = detect([(1.0125, 1.0130, 1.0040, 1.0045)])[0]
        inverting_open = frame["timestamp"].iloc[3].to_pydatetime()

        assert zone.event_timestamp == inverting_open
        assert zone.confirmation_timestamp == inverting_open + M5.duration

    def test_confirmation_is_strictly_after_the_source_gaps(self):
        zone = detect([(1.0125, 1.0130, 1.0040, 1.0045)])[0]
        assert zone.confirmation_timestamp > zone.source_fvg_confirmation

    def test_nothing_is_observable_before_the_inverting_close(self):
        zone = detect([(1.0125, 1.0130, 1.0040, 1.0045)])[0]
        assert not zone.is_observable_at(zone.confirmation_timestamp - timedelta(seconds=1))
        assert zone.is_observable_at(zone.confirmation_timestamp)

    def test_c3_cannot_invert_the_gap_it_defines(self):
        """The search starts only after C3 has closed."""
        zone = detect([(1.0125, 1.0130, 1.0040, 1.0045)])[0]
        assert zone.event_timestamp > gap_zone().formation_timestamp

    def test_bars_to_invert_is_recorded(self):
        delayed = [
            (1.0125, 1.0130, 1.0120, 1.0128),
            (1.0128, 1.0130, 1.0040, 1.0045),
        ]
        assert detect(delayed)[0].bars_to_invert == 2

    def test_the_inversion_window_is_bounded(self):
        """The drift also prints a second, smaller gap; both invert on the final bar."""
        drift = [(1.0125, 1.0130, 1.0120, 1.0128) for _ in range(5)]
        drift.append((1.0128, 1.0130, 1.0040, 1.0045))

        unbounded = detect(drift)
        assert unbounded
        assert all(z.bars_to_invert > 3 for z in unbounded)
        # Every inversion took longer than the bound, so bounding suppresses them all.
        assert detect(drift, IfvgConfig(max_bars_to_invert=3)) == []


class TestTheNaiveWickTrigger:
    def test_the_naive_trigger_fires_where_the_causal_one_does_not(self):
        """The divergence proof: a wick-based inverter reports a flip that never closed."""
        wick_fill = [(1.0125, 1.0130, 1.0045, 1.0120)]
        naive = IfvgConfig(trigger=InversionTrigger.WICK_THROUGH)

        assert detect(wick_fill) == []
        assert len(detect(wick_fill, naive)) == 1

    def test_the_naive_trigger_also_fires_earlier(self):
        sequence = [
            (1.0125, 1.0130, 1.0045, 1.0120),  # wick through, close above
            (1.0120, 1.0125, 1.0040, 1.0045),  # the genuine close through
        ]
        causal = detect(sequence)[0]
        naive = detect(sequence, IfvgConfig(trigger=InversionTrigger.WICK_THROUGH))[0]

        assert naive.confirmation_timestamp < causal.confirmation_timestamp


class TestLifecycle:
    def test_an_inverted_zone_starts_active(self):
        analysis = IfvgDetector().analyse(bars([*BULLISH_GAP, (1.0125, 1.0130, 1.0040, 1.0045)]), SYM, M5)
        zone = analysis.zones[0]
        assert analysis.status[zone.ifvg_id] is ZoneStatus.ACTIVE

    def test_price_returning_into_the_inverted_zone_fills_it(self):
        sequence = [
            (1.0125, 1.0130, 1.0040, 1.0045),  # inverts; now a BEARISH zone
            (1.0045, 1.0105, 1.0044, 1.0100),  # trades back up through it
        ]
        analysis = IfvgDetector().analyse(bars([*BULLISH_GAP, *sequence]), SYM, M5)
        zone = analysis.zones[0]

        assert analysis.status[zone.ifvg_id] is ZoneStatus.MITIGATED
        assert analysis.fills


class TestEvents:
    def test_the_event_type_reflects_the_INVERTED_direction(self):
        events = IfvgDetector().events(bars([*BULLISH_GAP, (1.0125, 1.0130, 1.0040, 1.0045)]), SYM, M5)
        assert events[0].event_type is EventType.IFVG_BEARISH
        assert events[0].metadata["original_direction"] == "bullish"

    def test_the_event_carries_source_provenance(self):
        events = IfvgDetector().events(bars([*BULLISH_GAP, (1.0125, 1.0130, 1.0040, 1.0045)]), SYM, M5)
        assert events[0].metadata["source_fvg_id"] == gap_zone().zone_id

    def test_the_event_never_leaks(self):
        events = IfvgDetector().events(bars([*BULLISH_GAP, (1.0125, 1.0130, 1.0040, 1.0045)]), SYM, M5)
        assert events[0].confirmation_timestamp > events[0].event_timestamp


class TestConfigValidation:
    @pytest.mark.parametrize(
        "kwargs, match",
        [
            ({"partial_fill_threshold": 1.5}, "partial_fill_threshold"),
            ({"full_fill_threshold": 0.0}, "full_fill_threshold"),
            ({"max_bars_to_invert": 0}, "max_bars_to_invert"),
        ],
    )
    def test_invalid_configuration_is_rejected(self, kwargs, match):
        with pytest.raises(ValueError, match=match):
            IfvgConfig(**kwargs)

    def test_no_gaps_means_no_inversions(self):
        flat = [(1.0000, 1.0010, 0.9990, 1.0005) for _ in range(6)]
        assert IfvgDetector().detect(bars(flat), SYM, M5) == []
