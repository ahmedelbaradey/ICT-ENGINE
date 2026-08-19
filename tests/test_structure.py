"""R2-03 StructureDetector — HH/HL/LH/LL, BOS, MSS/CHoCH, state machine.

Leakage and replay live in ``test_structure_leakage.py``; real data in
``test_structure_real_data.py``.

Scenarios are hand-built bar-by-bar so every expected label and break can be derived
by reading the table, rather than by re-running the detector inside the assertion.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame
from ict_kronos.ict import (
    BreakMode,
    ChochPolicy,
    Direction,
    EventType,
    StructureConfig,
    StructureDetector,
    StructureState,
    SwingConfig,
)

START = datetime(2024, 3, 8, 9, 0, tzinfo=UTC)
SWING_1_1 = SwingConfig(left=1, right=1)


def bars(spec: list[tuple], *, symbol: Symbol = Symbol.EURUSD, timeframe: Timeframe = Timeframe.M5):
    """Build a frame from ``(high, low[, close])`` rows; close defaults to the midpoint."""
    candles = []
    for i, row in enumerate(spec):
        high, low = row[0], row[1]
        close = row[2] if len(row) > 2 else (high + low) / 2
        candles.append(
            MarketCandle(
                timestamp=START + timedelta(minutes=timeframe.minutes * i),
                symbol=symbol,
                timeframe=timeframe,
                open=close,
                high=high,
                low=low,
                close=close,
                volume=1.0,
            )
        )
    return candles_to_frame(candles)


#: Swing highs at bars 1 (1.05) and 5 (1.07)  -> HH
#: Swing lows  at bars 3 (0.95) and 7 (0.97)  -> HL
#: No break: every close stays inside the references.
BULLISH_SEQUENCE = [
    (1.02, 0.99),
    (1.05, 1.00),  # 1: swing high 1.05
    (1.03, 0.98),
    (1.02, 0.95),  # 3: swing low 0.95
    (1.04, 0.99),
    (1.07, 1.01),  # 5: swing high 1.07  -> HH
    (1.05, 1.00),
    (1.04, 0.97),  # 7: swing low 0.97   -> HL
    (1.06, 1.00),
]

#: Mirror: swing highs 1.05 then 1.03 (LH); swing lows 0.95 then 0.93 (LL).
BEARISH_SEQUENCE = [
    (1.02, 0.99),
    (1.05, 1.00),  # 1: swing high 1.05
    (1.03, 0.98),
    (1.02, 0.95),  # 3: swing low 0.95
    (1.01, 0.965),
    (1.03, 0.97),  # 5: swing high 1.03  -> LH
    (1.00, 0.96),
    (0.99, 0.93),  # 7: swing low 0.93   -> LL
    (1.00, 0.96),
]


def analyse(spec, config=None, swing=SWING_1_1, symbol=Symbol.EURUSD):
    detector = StructureDetector(config or StructureConfig(), swing)
    return detector.analyse(bars(spec), symbol, Timeframe.M5)


def labels_of(analysis):
    return [(x.label, x.price_level) for x in analysis.labels]


# ---------------------------------------------------------------------- config


class TestStructureConfig:
    def test_defaults_are_the_documented_ones(self):
        config = StructureConfig()
        assert config.break_mode is BreakMode.CLOSE
        assert config.choch_policy is ChochPolicy.SYNONYM
        assert config.break_tolerance_points == 0.0
        assert config.min_swing_strength_points == 0.0

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"displacement_lookback": 0}, "displacement_lookback"),
            ({"displacement_factor": 0}, "displacement_factor"),
            ({"break_tolerance_points": -1}, "break_tolerance_points"),
            ({"min_swing_strength_points": -0.5}, "min_swing_strength_points"),
        ],
    )
    def test_invalid_config_is_refused(self, kwargs, match):
        with pytest.raises(ValueError, match=match):
            StructureConfig(**kwargs)

    def test_config_is_immutable(self):
        with pytest.raises(Exception):  # noqa: B017 - FrozenInstanceError
            StructureConfig().break_mode = BreakMode.WICK

    def test_settings_expose_structure_config(self, monkeypatch):
        from ict_kronos.app.config import Settings

        monkeypatch.setenv("ICT_STRUCTURE_BREAK_MODE", "wick")
        monkeypatch.setenv("ICT_STRUCTURE_CHOCH_POLICY", "distinct_by_displacement")
        monkeypatch.setenv("ICT_STRUCTURE_MIN_SWING_STRENGTH", "5")

        structure = Settings.from_env().structure
        assert structure.break_mode == "wick"
        assert structure.choch_policy == "distinct_by_displacement"
        assert structure.min_swing_strength_points == 5.0


# ----------------------------------------------------------- HH / HL / LH / LL


class TestSwingClassification:
    def test_higher_high_and_higher_low(self):
        analysis = analyse(BULLISH_SEQUENCE)
        assert labels_of(analysis) == [
            (EventType.HIGHER_HIGH, pytest.approx(1.07)),
            (EventType.HIGHER_LOW, pytest.approx(0.97)),
        ]

    def test_lower_high_and_lower_low(self):
        analysis = analyse(BEARISH_SEQUENCE)
        assert labels_of(analysis) == [
            (EventType.LOWER_HIGH, pytest.approx(1.03)),
            (EventType.LOWER_LOW, pytest.approx(0.93)),
        ]

    def test_direction_follows_the_label(self):
        for spec in (BULLISH_SEQUENCE, BEARISH_SEQUENCE):
            for label in analyse(spec).labels:
                expected = (
                    Direction.BULLISH
                    if label.label in (EventType.HIGHER_HIGH, EventType.HIGHER_LOW)
                    else Direction.BEARISH
                )
                assert label.direction is expected

    def test_the_first_swing_of_each_type_is_unlabelled(self):
        """No predecessor to compare against — silence, not an error."""
        analysis = analyse(BULLISH_SEQUENCE)
        labelled = {x.event_timestamp for x in analysis.labels}
        assert START + timedelta(minutes=5) not in labelled  # first swing high, bar 1
        assert START + timedelta(minutes=15) not in labelled  # first swing low, bar 3

    def test_reference_level_is_the_previous_same_type_swing(self):
        analysis = analyse(BULLISH_SEQUENCE)
        higher_high = next(x for x in analysis.labels if x.label is EventType.HIGHER_HIGH)
        assert higher_high.reference_level == pytest.approx(1.05)
        assert higher_high.previous_swing_timestamp == START + timedelta(minutes=5)

    def test_strength_is_the_distance_in_points(self):
        analysis = analyse(BULLISH_SEQUENCE)
        higher_high = next(x for x in analysis.labels if x.label is EventType.HIGHER_HIGH)
        expected = (1.07 - 1.05) / Symbol.EURUSD.spec.point_value
        assert higher_high.strength == pytest.approx(expected, rel=1e-6)

    def test_label_confirms_when_the_swing_confirms(self):
        analysis = analyse(BULLISH_SEQUENCE)
        higher_high = next(x for x in analysis.labels if x.label is EventType.HIGHER_HIGH)
        # Pivot at bar 5 (09:25); right=1 so it confirms at the close of bar 6 (09:35).
        assert higher_high.event_timestamp == START + timedelta(minutes=25)
        assert higher_high.confirmation_timestamp == START + timedelta(minutes=35)


class TestEqualAndRepeatedLevels:
    #: Swing highs at bars 1 and 5, both exactly 1.05.
    EQUAL_HIGHS = [
        (1.02, 0.99),
        (1.05, 1.00),
        (1.03, 0.98),
        (1.02, 0.95),
        (1.04, 0.99),
        (1.05, 1.01),
        (1.03, 1.00),
    ]

    def test_equal_highs_produce_no_label(self):
        """Neither higher nor lower. Equal highs are LIQUIDITY (R2-04), not structure —
        inventing an EQUAL_HIGH label here would pre-empt that story."""
        analysis = analyse(self.EQUAL_HIGHS)
        assert [x for x in analysis.labels if x.label in (EventType.HIGHER_HIGH, EventType.LOWER_HIGH)] == []

    def test_an_equal_high_still_becomes_the_active_reference(self):
        """Unlabelled does not mean ignored — the level is still protected."""
        spec = [*self.EQUAL_HIGHS, (1.10, 1.04, 1.09)]
        analysis = analyse(spec)
        assert len(analysis.breaks) == 1
        assert analysis.breaks[0].reference_level == pytest.approx(1.05)

    def test_tolerance_makes_near_equal_levels_equal(self):
        near = [
            (1.02, 0.99),
            (1.05000, 1.00),
            (1.03, 0.98),
            (1.02, 0.95),
            (1.04, 0.99),
            (1.05002, 1.01),
            (1.03, 1.00),
        ]
        strict = analyse(near)
        assert any(x.label is EventType.HIGHER_HIGH for x in strict.labels)

        # 5 points of tolerance on a 0.00001 point value = 0.00005 > 0.00002 difference.
        tolerant = analyse(near, StructureConfig(equal_level_tolerance_points=5))
        assert not any(x.label is EventType.HIGHER_HIGH for x in tolerant.labels)


# -------------------------------------------------------------------- breaks


class TestBos:
    #: BULLISH_SEQUENCE then a bar closing above the 1.07 reference.
    BULLISH_BREAK = [*BULLISH_SEQUENCE, (1.10, 1.05, 1.09)]
    #: BEARISH_SEQUENCE then a bar closing below the 0.93 reference.
    BEARISH_BREAK = [*BEARISH_SEQUENCE, (0.97, 0.90, 0.91)]

    def test_bos_bullish(self):
        analysis = analyse(self.BULLISH_BREAK)
        assert len(analysis.breaks) == 1
        event = analysis.breaks[0]
        assert event.event_type is EventType.BOS
        assert event.direction is Direction.BULLISH
        assert event.reference_level == pytest.approx(1.07)
        assert analysis.final_state is StructureState.BULLISH

    def test_bos_bearish(self):
        analysis = analyse(self.BEARISH_BREAK)
        assert len(analysis.breaks) == 1
        event = analysis.breaks[0]
        assert event.event_type is EventType.BOS
        assert event.direction is Direction.BEARISH
        assert event.reference_level == pytest.approx(0.93)
        assert analysis.final_state is StructureState.BEARISH

    def test_first_break_from_undefined_is_a_bos(self):
        """There is no prior character to change, so it is a break of structure that
        establishes the trend."""
        event = analyse(self.BULLISH_BREAK).breaks[0]
        assert event.previous_state is StructureState.UNDEFINED
        assert event.resulting_state is StructureState.BULLISH
        assert event.event_type is EventType.BOS
        assert not event.is_reversal

    def test_break_timestamps(self):
        """event = breaking bar's OPEN, confirmation = its CLOSE."""
        event = analyse(self.BULLISH_BREAK).breaks[0]
        assert event.event_timestamp == START + timedelta(minutes=45)  # bar 9 open
        assert event.confirmation_timestamp == START + timedelta(minutes=50)  # bar 9 close

    def test_the_referenced_swing_is_recorded(self):
        """R2-07 must be able to see which swing was broken and when it was knowable."""
        event = analyse(self.BULLISH_BREAK).breaks[0]
        assert event.reference_swing_timestamp == START + timedelta(minutes=25)  # bar 5
        assert event.reference_swing_confirmation == START + timedelta(minutes=35)
        assert event.reference_swing_confirmation < event.confirmation_timestamp

    def test_break_distance_is_reported_in_points(self):
        event = analyse(self.BULLISH_BREAK).breaks[0]
        expected = (1.09 - 1.07) / Symbol.EURUSD.spec.point_value
        assert event.break_distance_points == pytest.approx(expected, rel=1e-6)
        assert event.strength == event.break_distance_points

    def test_equality_is_not_a_break(self):
        """Closing exactly at the level does not break it."""
        spec = [*BULLISH_SEQUENCE, (1.08, 1.05, 1.07)]
        assert analyse(spec).breaks == []

    def test_tolerance_requires_a_larger_move(self):
        spec = [*BULLISH_SEQUENCE, (1.10, 1.05, 1.07001)]
        assert len(analyse(spec).breaks) == 1
        # 5 points = 0.00005 > the 0.00001 excess, so the break is suppressed.
        assert analyse(spec, StructureConfig(break_tolerance_points=5)).breaks == []

    def test_a_level_cannot_be_broken_twice(self):
        """The reference is consumed on break; a second push needs a NEW swing."""
        spec = [*BULLISH_SEQUENCE, (1.10, 1.05, 1.09), (1.12, 1.08, 1.11)]
        assert len(analyse(spec).breaks) == 1


class TestBreakMode:
    #: The breaking bar wicks to 1.10 but closes at 1.06 — below the 1.07 reference.
    WICK_ONLY = [*BULLISH_SEQUENCE, (1.10, 1.05, 1.06)]

    def test_close_mode_ignores_a_wick_break(self):
        assert analyse(self.WICK_ONLY, StructureConfig(break_mode=BreakMode.CLOSE)).breaks == []

    def test_wick_mode_accepts_it(self):
        breaks = analyse(self.WICK_ONLY, StructureConfig(break_mode=BreakMode.WICK)).breaks
        assert breaks
        final = breaks[-1]
        assert final.reference_level == pytest.approx(1.07)
        assert final.price_level == pytest.approx(1.10)

    def test_wick_mode_also_breaks_on_the_bar_that_forms_a_higher_swing_high(self):
        """A consequence of WICK mode worth stating: the bar that PRINTS a higher swing
        high necessarily exceeds the previous swing high, so it registers a break.

        Here bar 5 (high 1.07) breaks the 1.05 reference that was still active, even
        though bar 5 is itself the next swing-high pivot. CLOSE mode does not, because
        bar 5 closes at 1.04. This is the main reason CLOSE is the default: in WICK
        mode almost every HH formation also emits a BOS, which conflates the pivot with
        the break. See docs/ict/structure.md §4.
        """
        wick = analyse(self.WICK_ONLY, StructureConfig(break_mode=BreakMode.WICK)).breaks
        close = analyse(self.WICK_ONLY, StructureConfig(break_mode=BreakMode.CLOSE)).breaks

        assert [b.bar_index for b in wick] == [5, 9]
        assert close == []

    def test_wick_mode_still_confirms_at_the_bar_close(self):
        """Intrabar sequencing is unknowable from bar data, so wick mode loosens the
        trigger without shortening the confirmation lag."""
        event = analyse(self.WICK_ONLY, StructureConfig(break_mode=BreakMode.WICK)).breaks[0]
        assert event.confirmation_timestamp == event.event_timestamp + timedelta(minutes=5)

    def test_close_mode_reports_the_close_as_the_price(self):
        spec = [*BULLISH_SEQUENCE, (1.10, 1.05, 1.09)]
        assert analyse(spec).breaks[0].price_level == pytest.approx(1.09)


class TestMss:
    #: BOS bullish at bar 9, then a bar closing below the still-active 0.97 low.
    BULLISH_THEN_MSS = [
        *BULLISH_SEQUENCE,
        (1.10, 1.05, 1.09),  # 9: BOS bullish, state -> BULLISH
        (1.09, 1.06, 1.07),  # 10
        (1.08, 0.96, 0.965),  # 11: closes below 0.97 -> MSS bearish
    ]

    def test_mss_bearish_after_a_bullish_state(self):
        analysis = analyse(self.BULLISH_THEN_MSS)
        assert [b.event_type for b in analysis.breaks] == [EventType.BOS, EventType.MSS]

        mss = analysis.breaks[1]
        assert mss.direction is Direction.BEARISH
        assert mss.previous_state is StructureState.BULLISH
        assert mss.resulting_state is StructureState.BEARISH
        assert mss.is_reversal
        assert analysis.final_state is StructureState.BEARISH

    def test_mss_bullish_after_a_bearish_state(self):
        spec = [
            *BEARISH_SEQUENCE,
            (0.97, 0.90, 0.91),  # 9: BOS bearish, state -> BEARISH
            (0.99, 0.92, 0.95),  # 10
            (1.06, 0.99, 1.05),  # 11: closes above the 1.03 high -> MSS bullish
        ]
        analysis = analyse(spec)
        assert [b.event_type for b in analysis.breaks] == [EventType.BOS, EventType.MSS]

        mss = analysis.breaks[1]
        assert mss.direction is Direction.BULLISH
        assert mss.previous_state is StructureState.BEARISH
        assert mss.resulting_state is StructureState.BULLISH

    def test_bos_and_mss_are_the_same_break_with_a_different_prior_state(self):
        """The documented core of §5: identical detection, label set by prior state."""
        analysis = analyse(self.BULLISH_THEN_MSS)
        bos, mss = analysis.breaks
        assert bos.previous_state is StructureState.UNDEFINED
        assert mss.previous_state is StructureState.BULLISH
        # Both are ordinary confirmed breaks of a confirmed reference swing.
        for event in (bos, mss):
            assert event.reference_swing_confirmation <= event.confirmation_timestamp


class TestChochPolicy:
    SPEC = TestMss.BULLISH_THEN_MSS

    def test_synonym_policy_never_emits_choch(self):
        """Stated plainly rather than faked into two code paths: CHoCH IS MSS here."""
        analysis = analyse(self.SPEC, StructureConfig(choch_policy=ChochPolicy.SYNONYM))
        assert EventType.CHOCH not in {b.event_type for b in analysis.breaks}
        assert EventType.MSS in {b.event_type for b in analysis.breaks}

    def test_distinct_policy_emits_choch_without_displacement(self):
        """With too little history the displacement ratio cannot be computed, so the
        break is labelled CHoCH — the conservative choice, documented."""
        analysis = analyse(
            self.SPEC,
            StructureConfig(choch_policy=ChochPolicy.DISTINCT_BY_DISPLACEMENT, displacement_lookback=20),
        )
        counter = [b for b in analysis.breaks if b.is_reversal]
        assert counter
        assert all(b.event_type is EventType.CHOCH for b in counter)

    def test_distinct_policy_emits_mss_on_a_displaced_bar(self):
        analysis = analyse(
            self.SPEC,
            StructureConfig(
                choch_policy=ChochPolicy.DISTINCT_BY_DISPLACEMENT,
                displacement_lookback=3,
                displacement_factor=1.5,
            ),
        )
        counter = [b for b in analysis.breaks if b.is_reversal]
        assert counter
        # Bar 11 has range 0.12 against a much smaller recent mean.
        assert all(b.event_type is EventType.MSS for b in counter)
        assert counter[0].displacement_ratio > 1.5

    def test_displacement_ratio_is_recorded_regardless_of_policy(self):
        """Available as a feature without changing the labelling."""
        analysis = analyse(self.SPEC, StructureConfig(displacement_lookback=3))
        assert any(b.displacement_ratio is not None for b in analysis.breaks)

    def test_bos_is_never_relabelled_by_the_policy(self):
        """Displacement only ever discriminates counter-trend breaks."""
        analysis = analyse(
            self.SPEC,
            StructureConfig(choch_policy=ChochPolicy.DISTINCT_BY_DISPLACEMENT, displacement_lookback=3),
        )
        assert analysis.breaks[0].event_type is EventType.BOS


# --------------------------------------------------------------- state machine


class TestStateMachine:
    def test_initial_state_is_undefined(self):
        assert analyse(BULLISH_SEQUENCE).final_state is StructureState.UNDEFINED

    def test_state_at_uses_only_confirmed_breaks(self):
        analysis = analyse(TestMss.BULLISH_THEN_MSS)
        bos, mss = analysis.breaks

        assert analysis.state_at(bos.confirmation_timestamp - timedelta(seconds=1)) is (
            StructureState.UNDEFINED
        )
        assert analysis.state_at(bos.confirmation_timestamp) is StructureState.BULLISH
        assert analysis.state_at(mss.confirmation_timestamp - timedelta(seconds=1)) is (
            StructureState.BULLISH
        )
        assert analysis.state_at(mss.confirmation_timestamp) is StructureState.BEARISH

    def test_state_at_rejects_naive_timestamps(self):
        analysis = analyse(TestMss.BULLISH_THEN_MSS)
        with pytest.raises(ValueError, match="timezone-aware"):
            analysis.state_at(datetime(2024, 3, 8, 10, 0))  # noqa: DTZ001

    def test_every_break_records_its_transition(self):
        for event in analyse(TestMss.BULLISH_THEN_MSS).breaks:
            assert event.previous_state is not None
            assert event.resulting_state in (StructureState.BULLISH, StructureState.BEARISH)


# ------------------------------------------------------------------ boundaries


class TestBoundaries:
    def test_empty_frame(self):
        analysis = StructureDetector(StructureConfig(), SWING_1_1).analyse(
            bars([]), Symbol.EURUSD, Timeframe.M5
        )
        assert analysis.labels == [] and analysis.breaks == []
        assert analysis.final_state is StructureState.UNDEFINED

    def test_insufficient_history_for_any_swing(self):
        analysis = analyse([(1.02, 0.99), (1.05, 1.00)])
        assert analysis.labels == [] and analysis.breaks == []

    def test_one_swing_of_each_type_gives_no_labels(self):
        analysis = analyse(BULLISH_SEQUENCE[:5])
        assert analysis.labels == []

    def test_pending_references_are_exposed(self):
        """A caller can see which levels a future bar could break."""
        analysis = analyse(BULLISH_SEQUENCE)
        assert analysis.pending_high is not None
        assert analysis.pending_high.price_level == pytest.approx(1.07)
        assert analysis.pending_low.price_level == pytest.approx(0.97)

    def test_a_consumed_reference_is_cleared(self):
        analysis = analyse([*BULLISH_SEQUENCE, (1.10, 1.05, 1.09)])
        assert analysis.pending_high is None
        assert analysis.pending_low is not None

    def test_a_newer_swing_supersedes_the_reference(self):
        """docs §3: the most recent swing high is the protected level, even if lower."""
        spec = [
            (1.02, 0.99),
            (1.08, 1.00),  # 1: swing high 1.08
            (1.03, 0.98),
            (1.02, 0.95),  # 3: swing low
            (1.04, 0.99),
            (1.05, 1.01),  # 5: swing high 1.05 (LOWER than 1.08)
            (1.03, 1.00),
            (1.07, 1.02, 1.06),  # 7: closes above 1.05 -> breaks the NEWER, lower high
        ]
        breaks = analyse(spec).breaks
        assert len(breaks) == 1
        assert breaks[0].reference_level == pytest.approx(1.05)


class TestSignificanceFilter:
    def test_zero_threshold_keeps_every_swing(self):
        analysis = analyse(BULLISH_SEQUENCE, StructureConfig(min_swing_strength_points=0))
        assert analysis.swings_filtered_out == 0
        assert analysis.swings_used > 0

    def test_a_high_threshold_removes_swings(self):
        analysis = analyse(BULLISH_SEQUENCE, StructureConfig(min_swing_strength_points=1_000_000))
        assert analysis.swings_used == 0
        assert analysis.swings_filtered_out > 0
        assert analysis.labels == [] and analysis.breaks == []

    def test_a_filtered_swing_cannot_be_broken(self):
        """Excluded from structure entirely — not merely unlabelled."""
        spec = [*BULLISH_SEQUENCE, (1.10, 1.05, 1.09)]
        assert analyse(spec).breaks
        assert analyse(spec, StructureConfig(min_swing_strength_points=1_000_000)).breaks == []


# ---------------------------------------------------------------------- events


class TestEvents:
    def test_events_cover_labels_and_breaks(self):
        detector = StructureDetector(StructureConfig(), SWING_1_1)
        events = detector.events(bars(TestMss.BULLISH_THEN_MSS), Symbol.EURUSD, Timeframe.M5)

        kinds = {e.event_type for e in events}
        assert EventType.BOS in kinds
        assert EventType.MSS in kinds
        assert kinds & {EventType.HIGHER_HIGH, EventType.HIGHER_LOW}

    def test_events_are_ordered_by_confirmation(self):
        detector = StructureDetector(StructureConfig(), SWING_1_1)
        events = detector.events(bars(TestMss.BULLISH_THEN_MSS), Symbol.EURUSD, Timeframe.M5)
        confirmations = [e.confirmation_timestamp for e in events]
        assert confirmations == sorted(confirmations)

    def test_break_metadata_exposes_the_full_transition(self):
        """R2-03 must not be an opaque pattern recogniser."""
        detector = StructureDetector(StructureConfig(), SWING_1_1)
        events = detector.events(bars(TestMss.BULLISH_THEN_MSS), Symbol.EURUSD, Timeframe.M5)
        mss = next(e for e in events if e.event_type is EventType.MSS)

        for key in (
            "previous_state",
            "resulting_state",
            "reference_swing_timestamp",
            "reference_swing_confirmation",
            "displacement_ratio",
            "is_reversal",
            "break_mode",
        ):
            assert key in mss.metadata, key

    def test_contract_fields_are_populated(self):
        detector = StructureDetector(StructureConfig(), SWING_1_1)
        for event in detector.events(bars(TestMss.BULLISH_THEN_MSS), Symbol.EURUSD, Timeframe.M5):
            assert event.symbol == "EURUSD"
            assert event.timeframe == "5m"
            assert event.reference_level is not None
            assert event.strength is not None
