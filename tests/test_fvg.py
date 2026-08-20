"""R2-05 FVGDetector — three-candle imbalance, boundaries, fill, mitigation.

Leakage and replay live in ``test_fvg_leakage.py``; real data in
``test_fvg_real_data.py``.

Scenarios are hand-built so every expected zone can be read off the table rather than
re-derived by running the detector inside the assertion.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame
from ict_kronos.ict import (
    Direction,
    EventType,
    FvgConfig,
    FvgDetector,
    FvgStatus,
    GapMeasure,
    reference_zones,
)

START = datetime(2024, 3, 8, 9, 0, tzinfo=UTC)
M5 = Timeframe.M5


def bars(spec, *, symbol=Symbol.EURUSD, timeframe=M5, start=START):
    """Frame from ``(high, low[, open, close])`` rows.

    Open/close default to the midpoint so OHLC invariants always hold and the wick
    measure is the one under test unless a row says otherwise.
    """
    candles = []
    for i, row in enumerate(spec):
        high, low = row[0], row[1]
        open_ = row[2] if len(row) > 2 else (high + low) / 2
        close = row[3] if len(row) > 3 else (high + low) / 2
        candles.append(
            MarketCandle(
                timestamp=start + timedelta(minutes=timeframe.minutes * i),
                symbol=symbol,
                timeframe=timeframe,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=1.0,
            )
        )
    return candles_to_frame(candles)


def detect(spec, config=None, symbol=Symbol.EURUSD, timeframe=M5):
    return FvgDetector(config or FvgConfig()).detect(
        bars(spec, symbol=symbol, timeframe=timeframe), symbol, timeframe
    )


def analyse(spec, config=None, symbol=Symbol.EURUSD, timeframe=M5):
    return FvgDetector(config or FvgConfig()).analyse(
        bars(spec, symbol=symbol, timeframe=timeframe), symbol, timeframe
    )


#: C1 high 1.0100, C2 the displacement candle, C3 low 1.0200 -> bullish gap
#: [1.0100, 1.0200]. Nothing after, so it stays untouched.
BULLISH = [
    (1.0100, 1.0000),  # 0: C1
    (1.0300, 1.0050),  # 1: C2
    (1.0400, 1.0200),  # 2: C3   low 1.0200 > high 1.0100
]

#: Mirror: C1 low 1.0300, C3 high 1.0200 -> bearish gap [1.0200, 1.0300].
BEARISH = [
    (1.0400, 1.0300),  # 0: C1
    (1.0350, 1.0100),  # 1: C2
    (1.0200, 1.0000),  # 2: C3   high 1.0200 < low 1.0300
]


# ---------------------------------------------------------------------- config


class TestFvgConfig:
    def test_defaults(self):
        config = FvgConfig()
        assert config.min_gap_points == 0.0
        assert config.measure is GapMeasure.WICK
        assert config.require_contiguous_bars is True
        assert config.partial_fill_threshold == 0.0
        assert config.full_fill_threshold == 1.0
        assert config.require_displacement is False

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"min_gap_points": -1}, "min_gap_points"),
            ({"partial_fill_threshold": 1.5}, "partial_fill_threshold"),
            ({"full_fill_threshold": 0.0}, "full_fill_threshold"),
            ({"full_fill_threshold": 1.5}, "full_fill_threshold"),
            ({"partial_fill_threshold": 0.9, "full_fill_threshold": 0.5}, "must be below"),
            ({"displacement_lookback": 0}, "displacement_lookback"),
            ({"displacement_factor": 0}, "displacement_factor"),
        ],
    )
    def test_invalid_config_is_refused(self, kwargs, match):
        with pytest.raises(ValueError, match=match):
            FvgConfig(**kwargs)

    def test_config_is_immutable(self):
        with pytest.raises(Exception):  # noqa: B017 - FrozenInstanceError
            FvgConfig().min_gap_points = 5

    def test_settings_expose_fvg_config(self, monkeypatch):
        from ict_kronos.app.config import Settings

        monkeypatch.setenv("ICT_FVG_MIN_GAP_POINTS", "3")
        monkeypatch.setenv("ICT_FVG_MEASURE", "body")
        monkeypatch.setenv("ICT_FVG_REQUIRE_CONTIGUOUS_BARS", "0")

        fvg = Settings.from_env().fvg
        assert fvg.min_gap_points == 3.0
        assert fvg.measure == "body"
        assert fvg.require_contiguous_bars is False


# ------------------------------------------------------------------- detection


class TestBullishAndBearish:
    def test_bullish_fvg(self):
        zones = detect(BULLISH)
        assert len(zones) == 1
        zone = zones[0]
        assert zone.direction is Direction.BULLISH
        assert zone.bottom == pytest.approx(1.0100)  # high(C1)
        assert zone.top == pytest.approx(1.0200)  # low(C3)

    def test_bearish_fvg(self):
        zones = detect(BEARISH)
        assert len(zones) == 1
        zone = zones[0]
        assert zone.direction is Direction.BEARISH
        assert zone.bottom == pytest.approx(1.0200)  # high(C3)
        assert zone.top == pytest.approx(1.0300)  # low(C1)

    def test_no_gap_yields_nothing(self):
        overlapping = [(1.0300, 1.0000), (1.0350, 1.0050), (1.0400, 1.0100)]
        assert detect(overlapping) == []

    def test_size_is_reported_in_price_and_points(self):
        zone = detect(BULLISH)[0]
        assert zone.size == pytest.approx(0.0100)
        assert zone.size_points == pytest.approx(0.0100 / Symbol.EURUSD.spec.point_value, rel=1e-6)

    def test_midpoint_is_consequent_encroachment(self):
        zone = detect(BULLISH)[0]
        assert zone.midpoint == pytest.approx(1.0150)

    def test_entry_and_far_edges(self):
        bullish = detect(BULLISH)[0]
        assert bullish.entry_edge == pytest.approx(1.0200)  # price enters from above
        assert bullish.far_edge == pytest.approx(1.0100)

        bearish = detect(BEARISH)[0]
        assert bearish.entry_edge == pytest.approx(1.0200)  # price enters from below
        assert bearish.far_edge == pytest.approx(1.0300)

    def test_all_three_candle_timestamps_are_recorded(self):
        """So a consumer preferring the C2 convention can read it off directly."""
        zone = detect(BULLISH)[0]
        assert zone.candle1_timestamp == START
        assert zone.candle2_timestamp == START + timedelta(minutes=5)
        assert zone.candle3_timestamp == START + timedelta(minutes=10)


class TestTimestamps:
    def test_formation_is_candle3_open(self):
        zone = detect(BULLISH)[0]
        assert zone.formation_timestamp == START + timedelta(minutes=10)

    def test_confirmation_is_candle3_close(self):
        """THE R2-05 semantic. The condition reads low(C3), which is not final until
        C3 closes."""
        zone = detect(BULLISH)[0]
        assert zone.confirmation_timestamp == START + timedelta(minutes=15)

    def test_confirmation_is_exactly_one_bar_after_formation(self):
        """The legacy off-by-one made structurally impossible: confirmation is derived
        from close_time, which is always formation + one bar duration."""
        for spec in (BULLISH, BEARISH):
            zone = detect(spec)[0]
            assert zone.confirmation_timestamp - zone.formation_timestamp == timedelta(minutes=5)

    @pytest.mark.parametrize("timeframe", [Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.H1])
    def test_the_lag_scales_with_the_timeframe(self, timeframe):
        zone = detect(BULLISH, timeframe=timeframe)[0]
        expected = timedelta(minutes=timeframe.minutes)
        assert zone.confirmation_timestamp - zone.formation_timestamp == expected


class TestBoundaryEquality:
    def test_exact_equality_is_not_a_gap(self):
        """low(C3) == high(C1) leaves no imbalance."""
        touching = [(1.0100, 1.0000), (1.0300, 1.0050), (1.0400, 1.0100)]
        assert detect(touching) == []

    def test_one_point_above_equality_is_a_gap(self):
        spec = [(1.01000, 1.0000), (1.0300, 1.0050), (1.0400, 1.01001)]
        assert len(detect(spec)) == 1

    def test_bearish_exact_equality_is_not_a_gap(self):
        touching = [(1.0400, 1.0300), (1.0350, 1.0100), (1.0300, 1.0000)]
        assert detect(touching) == []


class TestMinimumGapSize:
    SMALL = [(1.01000, 1.0000), (1.0300, 1.0050), (1.0400, 1.01002)]  # 2-point gap

    def test_zero_threshold_accepts_any_gap(self):
        assert len(detect(self.SMALL, FvgConfig(min_gap_points=0))) == 1

    def test_threshold_rejects_a_smaller_gap(self):
        assert detect(self.SMALL, FvgConfig(min_gap_points=5)) == []

    def test_threshold_is_strict(self):
        """A gap exactly equal to the threshold is rejected."""
        assert detect(self.SMALL, FvgConfig(min_gap_points=2)) == []


class TestGapMeasure:
    #: Wick gap exists (low(C3)=1.0200 > high(C1)=1.0100) but the BODY of C1 reaches
    #: 1.0250, so a body-measured gap does not.
    WICK_ONLY = [
        (1.0100, 1.0000, 1.0000, 1.0090),
        (1.0300, 1.0050, 1.0100, 1.0250),
        (1.0400, 1.0200, 1.0210, 1.0390),
    ]

    def test_wick_measure_is_the_default(self):
        assert FvgDetector().config.measure is GapMeasure.WICK

    def test_body_measure_can_widen_a_gap(self):
        """A body-measured gap is always at least as wide as the wick one, so BODY can
        only ever find more."""
        wick = detect(BULLISH, FvgConfig(measure=GapMeasure.WICK))
        body = detect(BULLISH, FvgConfig(measure=GapMeasure.BODY))
        assert len(body) >= len(wick)
        assert body[0].size >= wick[0].size

    def test_measures_can_disagree(self):
        wick = detect(self.WICK_ONLY, FvgConfig(measure=GapMeasure.WICK))
        body = detect(self.WICK_ONLY, FvgConfig(measure=GapMeasure.BODY))
        assert len(wick) == 1
        assert body[0].size != wick[0].size


class TestContiguity:
    def test_a_time_gap_between_candles_yields_no_fvg(self):
        """A weekend or data gap makes the price jump meaningless as an imbalance —
        nothing traded in between. Admitting it manufactures a phantom FVG."""
        import pandas as pd

        frame = bars(BULLISH)
        gapped = frame.copy(deep=True)
        gapped.loc[2, "timestamp"] = gapped.loc[2, "timestamp"] + pd.Timedelta(days=2)

        detector = FvgDetector(FvgConfig())
        assert detector.detect(gapped, Symbol.EURUSD, M5) == []

    def test_a_gap_between_c1_and_c2_also_blocks_it(self):
        import pandas as pd

        frame = bars(BULLISH)
        gapped = frame.copy(deep=True)
        for i in (1, 2):
            gapped.loc[i, "timestamp"] = gapped.loc[i, "timestamp"] + pd.Timedelta(days=2)

        detector = FvgDetector(FvgConfig())
        assert detector.detect(gapped, Symbol.EURUSD, M5) == []

    def test_disabling_contiguity_restores_the_naive_behaviour(self):
        """Available for comparison, documented as leaky, off by default."""
        import pandas as pd

        frame = bars(BULLISH)
        gapped = frame.copy(deep=True)
        gapped.loc[2, "timestamp"] = gapped.loc[2, "timestamp"] + pd.Timedelta(days=2)

        detector = FvgDetector(FvgConfig(require_contiguous_bars=False))
        assert len(detector.detect(gapped, Symbol.EURUSD, M5)) == 1

    def test_a_missing_bar_blocks_the_window(self):
        spec = [(1.0100, 1.0000), (1.0300, 1.0050), (1.0400, 1.0200), (1.0450, 1.0250)]
        frame = bars(spec)
        holed = frame.drop(index=1).reset_index(drop=True)

        detector = FvgDetector(FvgConfig())
        assert detector.detect(holed, Symbol.EURUSD, M5) == []


class TestDisplacementFilter:
    def test_off_by_default(self):
        assert len(detect(BULLISH)) == 1

    def test_enabled_filter_rejects_a_small_c2(self):
        """With a short lookback the mean range is small, but C2 here is not
        exceptional relative to its neighbours."""
        spec = [(1.02, 1.00)] * 5 + [(1.0100, 1.0000), (1.0110, 1.0050), (1.0400, 1.0200)]
        loose = detect(
            spec, FvgConfig(displacement_lookback=3, displacement_factor=0.1, require_displacement=True)
        )
        strict = detect(
            spec, FvgConfig(displacement_lookback=3, displacement_factor=5.0, require_displacement=True)
        )
        assert loose
        assert strict == []

    def test_displacement_ratio_is_recorded_when_enabled(self):
        spec = [(1.02, 1.00)] * 5 + [(1.0100, 1.0000), (1.0300, 1.0050), (1.0400, 1.0200)]
        zones = detect(
            spec, FvgConfig(require_displacement=True, displacement_lookback=3, displacement_factor=0.1)
        )
        assert zones
        assert zones[0].displacement_ratio is not None


# ---------------------------------------------------------- multiple / overlap


class TestMultipleZones:
    def test_consecutive_fvgs(self):
        """A rising staircase: each bar's low clears the high two bars back."""
        spec = [
            (1.0100, 1.0000),
            (1.0200, 1.0110),
            (1.0300, 1.0210),
            (1.0400, 1.0310),
            (1.0500, 1.0410),
        ]
        zones = detect(spec)
        assert len(zones) == 3
        assert [z.index for z in zones] == [2, 3, 4]

    def test_one_bar_can_be_c3_of_one_zone_and_c1_of_another(self):
        spec = [
            (1.0100, 1.0000),
            (1.0200, 1.0110),
            (1.0300, 1.0210),
            (1.0400, 1.0310),
            (1.0500, 1.0410),
        ]
        zones = detect(spec)
        assert zones[0].candle3_timestamp == zones[2].candle1_timestamp

    def test_overlapping_zones_keep_separate_identities(self):
        spec = [
            (1.0100, 1.0000),
            (1.0500, 1.0110),
            (1.0300, 1.0150),  # bullish zone A: [1.0100, 1.0150]
            (1.0600, 1.0200),
            (1.0700, 1.0160),  # bullish zone B: [1.0300, 1.0160]? overlaps A's range
        ]
        zones = detect(spec)
        assert len({z.zone_id for z in zones}) == len(zones)

    def test_bullish_and_bearish_can_both_occur(self):
        spec = [*BULLISH, (1.0500, 1.0450), (1.0460, 1.0300), (1.0250, 1.0100)]
        zones = detect(spec)
        assert {z.direction for z in zones} == {Direction.BULLISH, Direction.BEARISH}


# ------------------------------------------------------------------- fill


class TestFill:
    #: Bullish zone [1.0100, 1.0200], then bars retracing into it.
    def _with_retrace(self, low_after: float):
        return [*BULLISH, (1.0400, 1.0300), (1.0350, low_after)]

    def test_untouched_zone_has_zero_fill(self):
        analysis = analyse([*BULLISH, (1.0400, 1.0300), (1.0380, 1.0250)])
        zone = analysis.zones[0]
        assert analysis.status[zone.zone_id] is FvgStatus.ACTIVE
        assert analysis.fills == []

    def test_touching_the_entry_edge_is_not_a_fill(self):
        """Price reaching exactly 1.0200 gives 0% — a touch is not a fill."""
        analysis = analyse(self._with_retrace(1.0200))
        assert analysis.fills == []
        assert analysis.status[analysis.zones[0].zone_id] is FvgStatus.ACTIVE

    def test_partial_fill(self):
        analysis = analyse(self._with_retrace(1.0150))  # halfway
        zone = analysis.zones[0]
        assert analysis.status[zone.zone_id] is FvgStatus.PARTIALLY_FILLED
        assert analysis.fills[-1].fill_percentage == pytest.approx(0.5)

    def test_a_partially_filled_zone_stays_usable(self):
        """Documented §6: only FULL fill removes a zone."""
        analysis = analyse(self._with_retrace(1.0150))
        zone = analysis.zones[0]
        later = analysis.fills[-1].confirmation_timestamp
        assert zone.zone_id in {z.zone_id for z in analysis.active_at(later)}

    def test_full_fill_mitigates(self):
        analysis = analyse(self._with_retrace(1.0100))  # reaches the far edge
        zone = analysis.zones[0]
        assert analysis.status[zone.zone_id] is FvgStatus.MITIGATED
        assert zone.zone_id in analysis.mitigated_at

    def test_trading_beyond_the_far_edge_still_caps_at_100_percent(self):
        analysis = analyse(self._with_retrace(1.0000))
        assert analysis.fills[-1].fill_percentage == pytest.approx(1.0)

    def test_a_mitigated_zone_leaves_the_active_set(self):
        analysis = analyse(self._with_retrace(1.0100))
        zone = analysis.zones[0]
        moment = analysis.mitigated_at[zone.zone_id]
        assert zone.zone_id not in {z.zone_id for z in analysis.active_at(moment)}

    def test_fill_only_deepens(self):
        """Updates are monotonic — a shallower retrace emits nothing."""
        spec = [*BULLISH, (1.0400, 1.0300), (1.0350, 1.0150), (1.0340, 1.0190)]
        analysis = analyse(spec)
        percentages = [u.fill_percentage for u in analysis.fills]
        assert percentages == sorted(percentages)
        assert len(analysis.fills) == 1

    def test_bearish_fill_direction(self):
        """Price enters a bearish zone from BELOW, going up."""
        spec = [*BEARISH, (1.0100, 1.0000), (1.0250, 1.0050)]
        analysis = analyse(spec)
        zone = analysis.zones[0]
        assert analysis.status[zone.zone_id] is FvgStatus.PARTIALLY_FILLED
        assert analysis.fills[-1].fill_percentage == pytest.approx(0.5)

    def test_candle3_cannot_fill_its_own_gap(self):
        """C3 defines the zone; it cannot also be the bar that fills it."""
        analysis = analyse(BULLISH)
        assert analysis.fills == []

    def test_thresholds_are_configurable(self):
        half = analyse(self._with_retrace(1.0150), FvgConfig(full_fill_threshold=0.5))
        zone = half.zones[0]
        assert half.status[zone.zone_id] is FvgStatus.MITIGATED

    def test_partial_threshold_suppresses_shallow_touches(self):
        shallow = analyse(self._with_retrace(1.0190), FvgConfig(partial_fill_threshold=0.5))
        assert shallow.fills == []


class TestFillPointInTime:
    def test_fill_at_returns_the_value_known_then(self):
        spec = [*BULLISH, (1.0400, 1.0300), (1.0350, 1.0150), (1.0340, 1.0100)]
        analysis = analyse(spec)
        zone = analysis.zones[0]

        first, second = analysis.fills[0], analysis.fills[1]
        assert analysis.fill_at(zone.zone_id, first.confirmation_timestamp) == pytest.approx(0.5)
        assert analysis.fill_at(zone.zone_id, second.confirmation_timestamp) == pytest.approx(1.0)

    def test_fill_before_any_update_is_zero(self):
        analysis = analyse([*BULLISH, (1.0400, 1.0300), (1.0350, 1.0150)])
        zone = analysis.zones[0]
        assert analysis.fill_at(zone.zone_id, zone.confirmation_timestamp) == 0.0

    def test_status_at_is_none_before_the_zone_confirms(self):
        analysis = analyse([*BULLISH, (1.0400, 1.0300), (1.0350, 1.0150)])
        zone = analysis.zones[0]
        early = zone.confirmation_timestamp - timedelta(seconds=1)
        assert analysis.status_at(zone.zone_id, early) is None

    def test_status_at_tracks_the_progression(self):
        spec = [*BULLISH, (1.0400, 1.0300), (1.0350, 1.0150), (1.0340, 1.0100)]
        analysis = analyse(spec)
        zone = analysis.zones[0]

        assert analysis.status_at(zone.zone_id, zone.confirmation_timestamp) is FvgStatus.ACTIVE
        assert (
            analysis.status_at(zone.zone_id, analysis.fills[0].confirmation_timestamp)
            is FvgStatus.PARTIALLY_FILLED
        )
        assert (
            analysis.status_at(zone.zone_id, analysis.fills[1].confirmation_timestamp) is FvgStatus.MITIGATED
        )


class TestStateOf:
    def test_state_of_answers_the_lifecycle_questions(self):
        spec = [*BULLISH, (1.0400, 1.0300), (1.0350, 1.0100)]
        analysis = analyse(spec)
        state = analysis.state_of(analysis.zones[0].zone_id)

        for key in (
            "top",
            "bottom",
            "midpoint",
            "formation_timestamp",
            "confirmation_timestamp",
            "candle1_timestamp",
            "status",
            "is_active",
            "fill_percentage",
            "first_touch_timestamp",
            "mitigation_timestamp",
            "invalidation_timestamp",
        ):
            assert key in state, key
        assert state["is_active"] is False
        assert state["mitigation_timestamp"] == state["invalidation_timestamp"]

    def test_state_of_unknown_zone_is_none(self):
        assert analyse(BULLISH).state_of("nope") is None


# ------------------------------------------------------------------ boundaries


class TestBoundaries:
    def test_empty_frame(self):
        assert FvgDetector().detect(bars([]), Symbol.EURUSD, M5) == []

    @pytest.mark.parametrize("count", [0, 1, 2])
    def test_insufficient_history_is_not_an_error(self, count):
        assert detect(BULLISH[:count]) == []

    def test_exactly_three_bars_can_yield_a_zone(self):
        assert len(detect(BULLISH)) == 1

    def test_zones_are_frozen(self):
        with pytest.raises(Exception):  # noqa: B017 - FrozenInstanceError
            detect(BULLISH)[0].top = 9.99

    def test_fill_updates_are_frozen(self):
        analysis = analyse([*BULLISH, (1.0400, 1.0300), (1.0350, 1.0150)])
        with pytest.raises(Exception):  # noqa: B017
            analysis.fills[0].fill_percentage = 9.99

    def test_unsorted_input_is_sorted_first(self):
        frame = bars(BULLISH)
        shuffled = frame.iloc[[2, 0, 1]].reset_index(drop=True)
        detector = FvgDetector()
        assert [z.as_dict() for z in detector.detect(shuffled, Symbol.EURUSD, M5)] == [
            z.as_dict() for z in detector.detect(frame, Symbol.EURUSD, M5)
        ]


# -------------------------------------------- vectorised vs naive reference


class TestAgainstReferenceImplementation:
    @pytest.mark.parametrize("measure", list(GapMeasure))
    @pytest.mark.parametrize("contiguous", [True, False])
    def test_matches_the_reference_on_random_bars(self, measure, contiguous):
        import numpy as np

        rng = np.random.default_rng(20240505)
        spec = []
        price = 1.0800
        for _ in range(300):
            price += rng.normal(0, 0.0008)
            high = round(price + abs(rng.normal(0, 0.0005)), 5)
            low = round(price - abs(rng.normal(0, 0.0005)), 5)
            spec.append((high, low))

        config = FvgConfig(measure=measure, require_contiguous_bars=contiguous)
        frame = bars(spec)
        detector = FvgDetector(config)

        detected = [(z.index, z.direction.value) for z in detector.detect(frame, Symbol.EURUSD, M5)]
        expected = reference_zones(frame, config, M5, Symbol.EURUSD.spec.point_value)
        assert detected == expected

    def test_reference_agrees_on_the_hand_built_cases(self):
        for spec, direction in ((BULLISH, "bullish"), (BEARISH, "bearish")):
            frame = bars(spec)
            assert reference_zones(frame, FvgConfig(), M5, Symbol.EURUSD.spec.point_value) == [(2, direction)]


# ---------------------------------------------------------------------- events


class TestEvents:
    def test_event_types(self):
        detector = FvgDetector()
        bull = detector.events(bars(BULLISH), Symbol.EURUSD, M5)
        bear = detector.events(bars(BEARISH), Symbol.EURUSD, M5)
        assert bull[0].event_type is EventType.FVG_BULLISH
        assert bear[0].event_type is EventType.FVG_BEARISH

    def test_contract_fields_are_populated(self):
        event = FvgDetector().events(bars(BULLISH), Symbol.EURUSD, M5)[0]
        assert event.symbol == "EURUSD"
        assert event.timeframe == "5m"
        assert event.price_level == pytest.approx(1.0150)  # midpoint
        assert event.reference_level == pytest.approx(1.0200)  # entry edge
        assert event.strength is not None

    def test_metadata_carries_the_zone(self):
        event = FvgDetector().events(bars(BULLISH), Symbol.EURUSD, M5)[0]
        for key in (
            "zone_id",
            "top",
            "bottom",
            "midpoint",
            "candle1_timestamp",
            "lifecycle_status",
            "fill_percentage",
        ):
            assert key in event.metadata, key

    def test_invalidation_timestamp_is_set_on_mitigation(self):
        spec = [*BULLISH, (1.0400, 1.0300), (1.0350, 1.0100)]
        event = FvgDetector().events(bars(spec), Symbol.EURUSD, M5)[0]
        assert event.invalidation_timestamp is not None

    def test_events_are_ordered_by_confirmation(self):
        spec = [
            (1.0100, 1.0000),
            (1.0200, 1.0110),
            (1.0300, 1.0210),
            (1.0400, 1.0310),
            (1.0500, 1.0410),
        ]
        events = FvgDetector().events(bars(spec), Symbol.EURUSD, M5)
        assert [e.confirmation_timestamp for e in events] == sorted(e.confirmation_timestamp for e in events)
