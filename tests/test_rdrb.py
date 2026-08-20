"""R2-05.2 RdrbDetector — the FOUR-candle redelivered rebalanced price range.

The definition of record for this engine:

    C1 -> C2 -> C3 -> C4,  C2 holds the protected wick

    bullish   valid iff  C4.low  >  C2.low
    bearish   valid iff  C4.high <  C2.high

    confirmation = C4's close

Equality is INVALID: reaching the protected extreme is violating it. The comparison is
wick to wick — never body, never close. Every one of those claims is a test below.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame
from ict_kronos.ict import (
    RDRB_CANDLES,
    Direction,
    EventType,
    RdrbConfig,
    RdrbDetector,
)

START = datetime(2024, 3, 8, 9, 0, tzinfo=UTC)
M5 = Timeframe.M5
SYM = Symbol.EURUSD


def bars(spec, *, start=START, timeframe=M5, gap_after=None):
    """Frame from explicit ``(open, high, low, close)`` rows.

    Every price is stated, because the whole point of these tests is that a specific
    wick — not a body, not a close — decides validity.
    """
    candles = []
    offset = 0
    for i, (o, h, low, c) in enumerate(spec):
        if gap_after is not None and i == gap_after + 1:
            offset += 10  # a hole in the series
        candles.append(
            MarketCandle(
                timestamp=start + timedelta(minutes=timeframe.minutes * (i + offset)),
                symbol=SYM,
                timeframe=timeframe,
                open=o,
                high=h,
                low=low,
                close=c,
                volume=1.0,
            )
        )
    return candles_to_frame(candles)


#: A valid bullish sequence. C2's low (1.0005) is protected; C4's low (1.0040) clears it.
BULLISH = [
    (1.0000, 1.0020, 0.9990, 1.0015),  # C1 up-close
    (1.0015, 1.0040, 1.0005, 1.0035),  # C2 up-close, protected low 1.0005
    (1.0035, 1.0050, 1.0030, 1.0045),  # C3 intervening
    (1.0045, 1.0070, 1.0040, 1.0065),  # C4 up-close, low 1.0040 > 1.0005
]

#: The bearish mirror. C2's high (1.0055) is protected; C4's high (1.0020) stays below.
BEARISH = [
    (1.0060, 1.0070, 1.0040, 1.0045),  # C1 down-close
    (1.0045, 1.0055, 1.0020, 1.0025),  # C2 down-close, protected high 1.0055
    (1.0025, 1.0030, 1.0010, 1.0015),  # C3 intervening
    (1.0015, 1.0020, 0.9990, 0.9995),  # C4 down-close, high 1.0020 < 1.0055
]


def detect(spec, config=None, **kwargs):
    return RdrbDetector(config or RdrbConfig()).detect(bars(spec, **kwargs), SYM, M5)


def with_c4(spec, *, low=None, high=None):
    """The same sequence with C4's protected-side wick moved."""
    o, h, lo, c = spec[3]
    return [*spec[:3], (o, high if high is not None else h, low if low is not None else lo, c)]


class TestTheFourCandleShape:
    def test_the_pattern_is_exactly_four_candles(self):
        assert RDRB_CANDLES == 4

    def test_fewer_than_four_bars_yields_nothing(self):
        for n in range(4):
            assert detect(BULLISH[:n]) == []

    def test_c1_c2_c3_alone_cannot_emit(self):
        """Three candles carry no statement about C4, so they carry no RDRB."""
        assert detect(BULLISH[:3]) == []

    def test_four_candles_emit_one(self):
        zones = detect(BULLISH)
        assert len(zones) == 1
        assert zones[0].direction is Direction.BULLISH

    def test_all_four_source_timestamps_are_recorded(self):
        frame = bars(BULLISH)
        zone = detect(BULLISH)[0]
        expected = tuple(t.to_pydatetime() for t in frame["timestamp"])

        assert zone.source_candle_timestamps == expected
        assert (zone.c1_timestamp, zone.c2_timestamp, zone.c3_timestamp, zone.c4_timestamp) == expected


class TestBullishValidity:
    def test_c4_low_above_c2_low_is_valid(self):
        assert len(detect(BULLISH)) == 1

    def test_c4_low_equal_to_c2_low_is_invalid(self):
        """Reaching the protected extreme IS violating it."""
        assert detect(with_c4(BULLISH, low=1.0005)) == []

    def test_c4_low_below_c2_low_is_invalid(self):
        assert detect(with_c4(BULLISH, low=1.0000)) == []

    def test_the_protected_and_validation_wicks_are_recorded(self):
        zone = detect(BULLISH)[0]
        assert zone.protected_wick == pytest.approx(1.0005)  # C2 low
        assert zone.validation_wick == pytest.approx(1.0040)  # C4 low

    def test_the_zone_spans_the_two_wicks(self):
        zone = detect(BULLISH)[0]
        assert zone.zone_bottom == pytest.approx(1.0005)
        assert zone.zone_top == pytest.approx(1.0040)


class TestBearishValidity:
    def test_c4_high_below_c2_high_is_valid(self):
        zones = detect(BEARISH)
        assert len(zones) == 1
        assert zones[0].direction is Direction.BEARISH

    def test_c4_high_equal_to_c2_high_is_invalid(self):
        assert detect(with_c4(BEARISH, high=1.0055)) == []

    def test_c4_high_above_c2_high_is_invalid(self):
        assert detect(with_c4(BEARISH, high=1.0060)) == []

    def test_the_protected_and_validation_wicks_are_recorded(self):
        zone = detect(BEARISH)[0]
        assert zone.protected_wick == pytest.approx(1.0055)  # C2 high
        assert zone.validation_wick == pytest.approx(1.0020)  # C4 high

    def test_the_zone_spans_the_two_wicks(self):
        zone = detect(BEARISH)[0]
        assert zone.zone_top == pytest.approx(1.0055)
        assert zone.zone_bottom == pytest.approx(1.0020)


class TestWickToWickOnly:
    """The comparison is wick to wick. Bodies and closes do not decide."""

    def test_a_body_that_clears_cannot_rescue_a_wick_that_violates(self):
        """C4's body sits well above C2's low; its WICK dips below. Invalid."""
        pierced = [*BULLISH[:3], (1.0045, 1.0070, 1.0000, 1.0065)]
        c4 = pierced[3]

        assert min(c4[0], c4[3]) > 1.0005  # the body is clear...
        assert c4[2] < 1.0005  # ...the wick is not
        assert detect(pierced) == []

    def test_a_close_that_clears_cannot_rescue_a_wick_that_violates(self):
        pierced = [*BULLISH[:3], (1.0045, 1.0070, 1.0000, 1.0065)]
        assert pierced[3][3] > 1.0005  # the close is clear
        assert detect(pierced) == []

    def test_the_bearish_mirror(self):
        pierced = [*BEARISH[:3], (1.0015, 1.0060, 0.9990, 0.9995)]
        c4 = pierced[3]

        assert max(c4[0], c4[3]) < 1.0055  # body clear
        assert c4[1] > 1.0055  # wick violates
        assert detect(pierced) == []

    def test_c2s_body_is_irrelevant_only_its_wick_matters(self):
        """Move C2's body without moving its low — the RDRB must be unchanged."""
        base = detect(BULLISH)[0]
        shifted = [BULLISH[0], (1.0010, 1.0040, 1.0005, 1.0038), *BULLISH[2:]]
        moved = detect(shifted)[0]

        assert moved.protected_wick == base.protected_wick
        assert (moved.zone_top, moved.zone_bottom) == (base.zone_top, base.zone_bottom)


class TestConfirmationTiming:
    def test_confirmation_is_c4s_close_time(self):
        frame = bars(BULLISH)
        zone = detect(BULLISH)[0]
        c4_open = frame["timestamp"].iloc[3].to_pydatetime()

        assert zone.confirmation_timestamp == c4_open + M5.duration

    def test_event_timestamp_is_c1s_open(self):
        frame = bars(BULLISH)
        zone = detect(BULLISH)[0]
        assert zone.event_timestamp == frame["timestamp"].iloc[0].to_pydatetime()

    def test_confirmation_is_three_bar_durations_after_the_event(self):
        zone = detect(BULLISH)[0]
        assert zone.confirmation_timestamp - zone.event_timestamp == M5.duration * RDRB_CANDLES

    def test_nothing_is_observable_before_c4_closes(self):
        zone = detect(BULLISH)[0]
        assert not zone.is_observable_at(zone.confirmation_timestamp - timedelta(seconds=1))
        assert zone.is_observable_at(zone.confirmation_timestamp)


class TestDirectionalPrerequisites:
    def test_c1_must_close_in_the_delivery_direction_by_default(self):
        broken = [(1.0020, 1.0025, 0.9990, 1.0000), *BULLISH[1:]]  # C1 down-close
        assert detect(broken) == []

    def test_c2_must_close_in_the_delivery_direction_by_default(self):
        broken = [BULLISH[0], (1.0038, 1.0040, 1.0005, 1.0010), *BULLISH[2:]]
        assert detect(broken) == []

    def test_c3_is_unconstrained_by_default(self):
        """C3 is described only as the intervening candle."""
        down_c3 = [*BULLISH[:2], (1.0048, 1.0050, 1.0030, 1.0036), BULLISH[3]]
        assert len(detect(down_c3)) == 1

    def test_c3_can_be_constrained_by_configuration(self):
        down_c3 = [*BULLISH[:2], (1.0048, 1.0050, 1.0030, 1.0036), BULLISH[3]]
        strict = RdrbConfig(require_directional_c3=True)
        assert detect(down_c3, strict) == []

    def test_c4_must_close_in_the_delivery_direction_by_default(self):
        broken = [*BULLISH[:3], (1.0065, 1.0070, 1.0040, 1.0050)]  # C4 down-close
        assert detect(broken) == []


class TestConfiguration:
    def test_tolerance_requires_clearing_by_more_than_the_margin(self):
        """C4's low clears C2's low by 0.0035 — 350 EURUSD points, whose point is 1e-5."""
        zone = detect(BULLISH)[0]
        assert zone.clearance_points == pytest.approx(350.0)

        assert detect(BULLISH, RdrbConfig(wick_tolerance_points=300.0))
        assert detect(BULLISH, RdrbConfig(wick_tolerance_points=400.0)) == []

    def test_a_negative_tolerance_is_rejected(self):
        with pytest.raises(ValueError, match="wick_tolerance_points"):
            RdrbConfig(wick_tolerance_points=-1.0)

    def test_a_gap_inside_the_sequence_suppresses_it(self):
        """Across a hole the four candles are not one delivery sequence."""
        assert detect(BULLISH, gap_after=1) == []

    def test_the_contiguity_guard_can_be_disabled(self):
        loose = RdrbConfig(require_contiguous_bars=False)
        assert len(detect(BULLISH, loose, gap_after=1)) == 1

    def test_with_config_returns_a_new_detector(self):
        base = RdrbDetector()
        other = base.with_config(RdrbConfig(require_directional_c3=True))
        assert base.config.require_directional_c3 is False
        assert other.config.require_directional_c3 is True


class TestEvents:
    def test_the_event_type_matches_the_direction(self):
        detector = RdrbDetector()
        assert detector.events(bars(BULLISH), SYM, M5)[0].event_type is EventType.RDRB_BULLISH
        assert detector.events(bars(BEARISH), SYM, M5)[0].event_type is EventType.RDRB_BEARISH

    def test_the_event_carries_the_protected_wick_and_sources(self):
        event = RdrbDetector().events(bars(BULLISH), SYM, M5)[0]
        assert event.reference_level == pytest.approx(1.0005)
        assert len(event.metadata["source_candle_timestamps"]) == RDRB_CANDLES

    def test_the_event_never_leaks(self):
        event = RdrbDetector().events(bars(BULLISH), SYM, M5)[0]
        assert event.confirmation_timestamp > event.event_timestamp

    def test_as_dict_round_trips(self):
        payload = detect(BULLISH)[0].as_dict()
        assert payload["direction"] == "bullish"
        assert len(payload["source_candle_timestamps"]) == 4


class TestImmutability:
    def test_the_record_is_frozen(self):
        from dataclasses import FrozenInstanceError

        zone = detect(BULLISH)[0]
        with pytest.raises(FrozenInstanceError):
            zone.zone_top = 0.0  # type: ignore[misc]
