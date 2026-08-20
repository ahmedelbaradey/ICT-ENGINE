"""R2-05.2 CisdDetector — change in the state of delivery.

    bullish CISD   a close ABOVE the opening price of the preceding bearish leg
    bearish CISD   a close BELOW the opening price of the preceding bullish leg

Bodies only. A wick through the trigger level is not a CISD. And CISD is deliberately
independent of R2-03: it reads opens and closes, structure reads swing levels.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame
from ict_kronos.ict import (
    CisdAnchor,
    CisdConfig,
    CisdDetector,
    DeliveryState,
    Direction,
    EventType,
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
    return CisdDetector(config or CisdConfig()).detect(bars(spec), SYM, M5)


#: A three-candle bearish delivery leg opening at 1.0100, then a close back above it.
BEARISH_LEG_THEN_BULLISH_CISD = [
    (1.0100, 1.0105, 1.0080, 1.0085),  # leg candle 1 — the SERIES OPEN is 1.0100
    (1.0085, 1.0090, 1.0060, 1.0065),  # leg candle 2
    (1.0065, 1.0070, 1.0040, 1.0045),  # leg candle 3
    (1.0045, 1.0120, 1.0044, 1.0115),  # closes 1.0115 > 1.0100  -> bullish CISD
]


class TestTheRule:
    def test_a_close_above_the_legs_open_is_a_bullish_cisd(self):
        transitions = detect(BEARISH_LEG_THEN_BULLISH_CISD)
        assert len(transitions) == 1
        cisd = transitions[0]

        assert cisd.direction is Direction.BULLISH
        assert cisd.trigger_level == pytest.approx(1.0100)
        assert cisd.trigger_close == pytest.approx(1.0115)

    def test_the_bearish_mirror(self):
        bullish_leg = [
            (1.0040, 1.0060, 1.0035, 1.0055),  # leg opens at 1.0040
            (1.0055, 1.0080, 1.0050, 1.0075),
            (1.0075, 1.0090, 1.0070, 1.0085),
            (1.0085, 1.0086, 1.0020, 1.0025),  # closes 1.0025 < 1.0040 -> bearish CISD
        ]
        transitions = detect(bullish_leg)
        assert len(transitions) == 1
        assert transitions[0].direction is Direction.BEARISH
        assert transitions[0].trigger_level == pytest.approx(1.0040)

    def test_the_delivery_leg_is_recorded(self):
        frame = bars(BEARISH_LEG_THEN_BULLISH_CISD)
        cisd = detect(BEARISH_LEG_THEN_BULLISH_CISD)[0]

        assert cisd.leg_length == 3
        assert cisd.leg_start_timestamp == frame["timestamp"].iloc[0].to_pydatetime()
        assert cisd.leg_end_timestamp == frame["timestamp"].iloc[2].to_pydatetime()

    def test_the_state_transition_is_recorded(self):
        cisd = detect(BEARISH_LEG_THEN_BULLISH_CISD)[0]
        assert cisd.previous_state is DeliveryState.UNDEFINED
        assert cisd.resulting_state is DeliveryState.BULLISH


class TestBodiesOnly:
    def test_a_wick_above_the_trigger_level_is_not_a_cisd(self):
        wick_only = [
            *BEARISH_LEG_THEN_BULLISH_CISD[:3],
            (1.0045, 1.0120, 1.0044, 1.0095),  # HIGH 1.0120 but CLOSE 1.0095 < 1.0100
        ]
        assert detect(wick_only) == []

    def test_a_close_exactly_at_the_trigger_level_is_not_a_transition(self):
        equal = [
            *BEARISH_LEG_THEN_BULLISH_CISD[:3],
            (1.0045, 1.0120, 1.0044, 1.0100),  # closes exactly 1.0100
        ]
        assert detect(equal) == []

    def test_the_legs_lows_are_irrelevant_only_its_open_matters(self):
        """Move the leg's wicks without moving its opening price."""
        base = detect(BEARISH_LEG_THEN_BULLISH_CISD)[0]
        deeper = [
            (1.0100, 1.0105, 1.0010, 1.0085),  # far lower low, same open
            *BEARISH_LEG_THEN_BULLISH_CISD[1:],
        ]
        moved = detect(deeper)[0]

        assert moved.trigger_level == base.trigger_level
        assert moved.confirmation_timestamp == base.confirmation_timestamp


class TestTheAnchor:
    def test_series_open_is_the_default(self):
        assert CisdConfig().anchor is CisdAnchor.SERIES_OPEN

    def test_series_open_uses_the_legs_first_candle(self):
        cisd = detect(BEARISH_LEG_THEN_BULLISH_CISD)[0]
        assert cisd.trigger_level == pytest.approx(1.0100)

    def test_extreme_open_can_differ_from_series_open(self):
        """A leg whose highest open is not its first — the readings then disagree."""
        rising_opens = [
            (1.0090, 1.0095, 1.0085, 1.0088),  # opens 1.0090
            (1.0110, 1.0115, 1.0080, 1.0085),  # opens 1.0110 — the extreme
            (1.0085, 1.0090, 1.0060, 1.0065),
            (1.0065, 1.0130, 1.0064, 1.0125),  # closes above BOTH
        ]
        series = detect(rising_opens)
        extreme = detect(rising_opens, CisdConfig(anchor=CisdAnchor.EXTREME_OPEN))

        assert series[0].trigger_level == pytest.approx(1.0090)
        assert extreme[0].trigger_level == pytest.approx(1.0110)

    def test_the_extreme_anchor_is_stricter(self):
        """A close between the two anchors satisfies SERIES_OPEN only."""
        rising_opens = [
            (1.0090, 1.0095, 1.0085, 1.0088),
            (1.0110, 1.0115, 1.0080, 1.0085),
            (1.0085, 1.0090, 1.0060, 1.0065),
            (1.0065, 1.0105, 1.0064, 1.0100),  # 1.0100 is above 1.0090, below 1.0110
        ]
        assert detect(rising_opens)
        assert detect(rising_opens, CisdConfig(anchor=CisdAnchor.EXTREME_OPEN)) == []


class TestConfirmationTiming:
    def test_confirmation_is_the_crossing_bars_close_time(self):
        frame = bars(BEARISH_LEG_THEN_BULLISH_CISD)
        cisd = detect(BEARISH_LEG_THEN_BULLISH_CISD)[0]
        crossing_open = frame["timestamp"].iloc[3].to_pydatetime()

        assert cisd.event_timestamp == crossing_open
        assert cisd.confirmation_timestamp == crossing_open + M5.duration

    def test_the_lag_is_exactly_one_bar(self):
        cisd = detect(BEARISH_LEG_THEN_BULLISH_CISD)[0]
        assert cisd.confirmation_timestamp - cisd.event_timestamp == M5.duration

    def test_nothing_is_observable_before_the_crossing_close(self):
        cisd = detect(BEARISH_LEG_THEN_BULLISH_CISD)[0]
        assert not cisd.is_observable_at(cisd.confirmation_timestamp - timedelta(seconds=1))
        assert cisd.is_observable_at(cisd.confirmation_timestamp)

    def test_a_leg_never_closed_through_yields_nothing(self):
        assert detect(BEARISH_LEG_THEN_BULLISH_CISD[:3]) == []


class TestDeliveryState:
    def test_state_is_undefined_before_the_first_transition(self):
        analysis = CisdDetector().analyse(bars(BEARISH_LEG_THEN_BULLISH_CISD), SYM, M5)
        assert analysis.state_at(START) is DeliveryState.UNDEFINED

    def test_state_flips_at_the_transitions_confirmation(self):
        analysis = CisdDetector().analyse(bars(BEARISH_LEG_THEN_BULLISH_CISD), SYM, M5)
        cisd = analysis.transitions[0]

        assert analysis.state_at(cisd.confirmation_timestamp - timedelta(seconds=1)) is (
            DeliveryState.UNDEFINED
        )
        assert analysis.state_at(cisd.confirmation_timestamp) is DeliveryState.BULLISH

    def test_a_repeat_in_the_same_direction_is_not_a_change_of_state(self):
        repeated = [
            *BEARISH_LEG_THEN_BULLISH_CISD,
            (1.0115, 1.0118, 1.0110, 1.0112),  # a small bearish leg
            (1.0112, 1.0200, 1.0111, 1.0195),  # closes above it — already bullish
        ]
        transitions = detect(repeated)
        assert len({t.resulting_state for t in transitions}) == len(transitions)


class TestIndependenceFromStructure:
    def test_the_module_does_not_import_structure(self):
        from pathlib import Path

        source = Path("ict_kronos/ict/cisd.py").read_text(encoding="utf-8")
        assert "from .structure import" not in source
        assert "from .swings import" not in source

    def test_cisd_can_fire_where_structure_reports_nothing(self):
        """Four bars carry no confirmed swing, so R2-03 has nothing to say."""
        from ict_kronos.ict import StructureDetector

        frame = bars(BEARISH_LEG_THEN_BULLISH_CISD)
        assert StructureDetector().analyse(frame, SYM, M5).breaks == []
        assert detect(BEARISH_LEG_THEN_BULLISH_CISD)


class TestConfiguration:
    def test_a_single_candle_leg_is_legal_by_default(self):
        short = [
            (1.0100, 1.0105, 1.0080, 1.0085),  # a one-candle bearish leg
            (1.0085, 1.0120, 1.0084, 1.0115),  # closes above 1.0100
        ]
        assert len(detect(short)) == 1

    def test_min_leg_length_suppresses_short_legs(self):
        short = [
            (1.0100, 1.0105, 1.0080, 1.0085),
            (1.0085, 1.0120, 1.0084, 1.0115),
        ]
        assert detect(short, CisdConfig(min_leg_length=2)) == []

    @pytest.mark.parametrize(
        "kwargs, match",
        [
            ({"min_leg_length": 0}, "min_leg_length"),
            ({"trigger_tolerance_points": -1.0}, "trigger_tolerance_points"),
            ({"max_bars_to_trigger": 0}, "max_bars_to_trigger"),
        ],
    )
    def test_invalid_configuration_is_rejected(self, kwargs, match):
        with pytest.raises(ValueError, match=match):
            CisdConfig(**kwargs)


class TestEvents:
    def test_the_event_type_matches_the_direction(self):
        events = CisdDetector().events(bars(BEARISH_LEG_THEN_BULLISH_CISD), SYM, M5)
        assert events[0].event_type is EventType.CISD_BULLISH

    def test_the_event_carries_the_leg(self):
        event = CisdDetector().events(bars(BEARISH_LEG_THEN_BULLISH_CISD), SYM, M5)[0]
        assert event.metadata["leg_length"] == 3
        assert event.metadata["resulting_state"] == "bullish"

    def test_the_event_never_leaks(self):
        event = CisdDetector().events(bars(BEARISH_LEG_THEN_BULLISH_CISD), SYM, M5)[0]
        assert event.confirmation_timestamp > event.event_timestamp
