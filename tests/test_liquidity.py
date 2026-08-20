"""R2-04 LiquidityDetector — levels, sweeps, lifecycle, day/week calendar.

Leakage and replay live in ``test_liquidity_leakage.py``; real data in
``test_liquidity_real_data.py``.

The architectural claim under test throughout: **a level is not a sweep**.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest

from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame
from ict_kronos.ict import (
    Direction,
    EventType,
    LiquidityConfig,
    LiquidityDetector,
    LiquiditySide,
    LiquidityStatus,
    LiquidityType,
    SwingConfig,
)

START = datetime(2024, 3, 8, 9, 0, tzinfo=UTC)
SWING_1_1 = SwingConfig(left=1, right=1)
#: Only the swing/equal machinery — keeps period and session levels out of the way
#: for the focused unit tests.
NO_SWINGS = LiquidityConfig(include_swing_levels=False)


def bars(spec, *, symbol=Symbol.EURUSD, timeframe=Timeframe.M5, start=START):
    """Frame from ``(high, low[, close])`` rows; close defaults to the midpoint."""
    candles = []
    for i, row in enumerate(spec):
        high, low = row[0], row[1]
        close = row[2] if len(row) > 2 else (high + low) / 2
        candles.append(
            MarketCandle(
                timestamp=start + timedelta(minutes=timeframe.minutes * i),
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


def analyse(spec, config=None, swing=SWING_1_1, symbol=Symbol.EURUSD, start=START):
    detector = LiquidityDetector(config or LiquidityConfig(), swing)
    return detector.analyse(bars(spec, symbol=symbol, start=start), symbol, Timeframe.M5)


def levels_of(analysis, kind):
    return [x for x in analysis.levels if x.liquidity_type is kind]


#: Swing highs at bars 1 and 5, equal at 1.0500. Swing low at bar 3.
EQUAL_HIGHS_SPEC = [
    (1.0200, 0.9900),
    (1.0500, 1.0000),  # 1: swing high 1.0500
    (1.0300, 0.9800),
    (1.0200, 0.9500),  # 3: swing low
    (1.0400, 0.9900),
    (1.0500, 1.0100),  # 5: swing high 1.0500 — equal
    (1.0300, 1.0000),
]


# ---------------------------------------------------------------------- config


class TestLiquidityConfig:
    def test_defaults(self):
        config = LiquidityConfig()
        assert config.equal_tolerance_points == 1.0
        assert config.equal_max_swing_distance == 1
        assert config.sweep_tolerance_points == 0.0
        assert config.approach_tolerance_points is None  # optional, off
        assert config.day_timezone == "America/New_York"
        assert config.day_boundary_local == time(17, 0)

    def test_no_require_rejection_flag_exists(self):
        """Removed deliberately: it created a state where a level was consumed but no
        event explained it. Filtering belongs downstream on `is_rejection`."""
        assert not hasattr(LiquidityConfig(), "require_rejection")

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"equal_max_swing_distance": 0}, "equal_max_swing_distance"),
            ({"equal_tolerance_points": -1}, "equal_tolerance_points"),
            ({"sweep_tolerance_points": -1}, "sweep_tolerance_points"),
            ({"approach_tolerance_points": -1}, "approach_tolerance_points"),
        ],
    )
    def test_invalid_config_is_refused(self, kwargs, match):
        with pytest.raises(ValueError, match=match):
            LiquidityConfig(**kwargs)

    def test_day_definition_is_a_local_time_window(self):
        """The trading day reuses SessionDefinition, so it is DST-aware by
        construction rather than a second calendar implementation."""
        definition = LiquidityConfig().day_definition
        assert definition.timezone == "America/New_York"
        assert definition.crosses_midnight  # 17:00 -> 17:00 next day

    def test_settings_expose_liquidity_config(self, monkeypatch):
        from ict_kronos.app.config import Settings

        monkeypatch.setenv("ICT_LIQUIDITY_EQUAL_TOLERANCE_POINTS", "5")
        monkeypatch.setenv("ICT_LIQUIDITY_DAY_TIMEZONE", "UTC")
        monkeypatch.setenv("ICT_LIQUIDITY_DAY_BOUNDARY_LOCAL", "00:00")

        liquidity = Settings.from_env().liquidity
        assert liquidity.equal_tolerance_points == 5.0
        assert liquidity.day_timezone == "UTC"
        assert liquidity.day_boundary_local == "00:00"


# ------------------------------------------------------------------ level types


class TestSwingLevels:
    def test_confirmed_swings_become_levels(self):
        analysis = analyse(EQUAL_HIGHS_SPEC)
        assert levels_of(analysis, LiquidityType.SWING_HIGH)
        assert levels_of(analysis, LiquidityType.SWING_LOW)

    def test_swing_levels_inherit_the_swing_confirmation(self):
        analysis = analyse(EQUAL_HIGHS_SPEC)
        level = levels_of(analysis, LiquidityType.SWING_HIGH)[0]
        # Pivot at bar 1 (09:05); right=1 so it confirms at bar 2's close (09:15).
        assert level.created_timestamp == START + timedelta(minutes=5)
        assert level.confirmation_timestamp == START + timedelta(minutes=15)

    def test_swing_levels_can_be_disabled(self):
        analysis = analyse(EQUAL_HIGHS_SPEC, NO_SWINGS)
        assert levels_of(analysis, LiquidityType.SWING_HIGH) == []
        assert levels_of(analysis, LiquidityType.SWING_LOW) == []

    def test_source_swing_is_preserved(self):
        level = levels_of(analyse(EQUAL_HIGHS_SPEC), LiquidityType.SWING_HIGH)[0]
        assert level.source_swing_timestamps == (level.created_timestamp,)


class TestEqualLevels:
    def test_two_equal_swing_highs_create_a_level(self):
        analysis = analyse(EQUAL_HIGHS_SPEC, NO_SWINGS)
        equal = levels_of(analysis, LiquidityType.EQUAL_HIGHS)
        assert len(equal) == 1
        assert equal[0].price_level == pytest.approx(1.0500)

    def test_confirmation_is_the_LATER_of_the_two_swing_confirmations(self):
        """The story's specific requirement: the level cannot be observable until the
        later required information is."""
        analysis = analyse(EQUAL_HIGHS_SPEC, NO_SWINGS)
        level = levels_of(analysis, LiquidityType.EQUAL_HIGHS)[0]

        # Swings at bars 1 and 5 confirm at bars 2 and 6 -> 09:15 and 09:35.
        assert level.created_timestamp == START + timedelta(minutes=25)  # later pivot
        assert level.confirmation_timestamp == START + timedelta(minutes=35)  # later confirm

    def test_both_source_swings_are_preserved(self):
        level = levels_of(analyse(EQUAL_HIGHS_SPEC, NO_SWINGS), LiquidityType.EQUAL_HIGHS)[0]
        assert level.source_swing_timestamps == (
            START + timedelta(minutes=5),
            START + timedelta(minutes=25),
        )

    def test_tolerance_is_configurable_not_float_equality(self):
        near = [
            (1.02000, 0.99),
            (1.05000, 1.00),
            (1.03000, 0.98),
            (1.02000, 0.95),
            (1.04000, 0.99),
            (1.05002, 1.01),  # 2 points higher
            (1.03000, 1.00),
        ]
        strict = analyse(near, LiquidityConfig(include_swing_levels=False, equal_tolerance_points=1))
        loose = analyse(near, LiquidityConfig(include_swing_levels=False, equal_tolerance_points=5))

        assert levels_of(strict, LiquidityType.EQUAL_HIGHS) == []
        assert len(levels_of(loose, LiquidityType.EQUAL_HIGHS)) == 1

    def test_level_price_is_the_extreme_of_the_pair(self):
        """Stops rest beyond the furthest touch, so that is what a sweep must exceed."""
        near = [
            (1.02000, 0.99),
            (1.05000, 1.00),
            (1.03000, 0.98),
            (1.02000, 0.95),
            (1.04000, 0.99),
            (1.05002, 1.01),
            (1.03000, 1.00),
        ]
        analysis = analyse(near, LiquidityConfig(include_swing_levels=False, equal_tolerance_points=5))
        assert levels_of(analysis, LiquidityType.EQUAL_HIGHS)[0].price_level == pytest.approx(1.05002)

    def test_equal_lows(self):
        spec = [
            (1.05, 1.0000),
            (1.04, 0.9500),  # 1: swing low 0.95
            (1.06, 1.0000),
            (1.07, 1.0100),
            (1.06, 1.0000),
            (1.05, 0.9500),  # 5: swing low 0.95 — equal
            (1.06, 1.0000),
        ]
        analysis = analyse(spec, NO_SWINGS)
        equal = levels_of(analysis, LiquidityType.EQUAL_LOWS)
        assert len(equal) == 1
        assert equal[0].side is LiquiditySide.SELL_SIDE

    def test_a_run_of_three_produces_two_overlapping_levels(self):
        """Documented: merging would mutate an already-confirmed level."""
        spec = [
            (1.02, 0.99),
            (1.05, 1.00),  # swing high
            (1.03, 0.98),
            (1.02, 0.95),
            (1.04, 0.99),
            (1.05, 1.01),  # swing high
            (1.03, 1.00),
            (1.02, 0.95),
            (1.04, 0.99),
            (1.05, 1.01),  # swing high
            (1.03, 1.00),
        ]
        analysis = analyse(spec, NO_SWINGS)
        assert len(levels_of(analysis, LiquidityType.EQUAL_HIGHS)) == 2

    def test_distance_beyond_one_can_be_configured(self):
        analysis = analyse(
            EQUAL_HIGHS_SPEC,
            LiquidityConfig(include_swing_levels=False, equal_max_swing_distance=3),
        )
        assert levels_of(analysis, LiquidityType.EQUAL_HIGHS)


class TestSideClassification:
    @pytest.mark.parametrize(
        ("kind", "side"),
        [
            (LiquidityType.SWING_HIGH, LiquiditySide.BUY_SIDE),
            (LiquidityType.EQUAL_HIGHS, LiquiditySide.BUY_SIDE),
            (LiquidityType.PREVIOUS_DAY_HIGH, LiquiditySide.BUY_SIDE),
            (LiquidityType.PREVIOUS_WEEK_HIGH, LiquiditySide.BUY_SIDE),
            (LiquidityType.SESSION_HIGH, LiquiditySide.BUY_SIDE),
            (LiquidityType.SWING_LOW, LiquiditySide.SELL_SIDE),
            (LiquidityType.EQUAL_LOWS, LiquiditySide.SELL_SIDE),
            (LiquidityType.PREVIOUS_DAY_LOW, LiquiditySide.SELL_SIDE),
            (LiquidityType.PREVIOUS_WEEK_LOW, LiquiditySide.SELL_SIDE),
            (LiquidityType.SESSION_LOW, LiquiditySide.SELL_SIDE),
        ],
    )
    def test_side_follows_the_type(self, kind, side):
        assert kind.is_high == (side is LiquiditySide.BUY_SIDE)

    def test_side_never_changes_as_price_moves(self):
        """A level's side describes what orders rest there, not where price is. If it
        flipped, the same historical level would classify differently depending on
        when you asked — destroying immutability."""
        spec = [*EQUAL_HIGHS_SPEC, (1.10, 1.04, 1.09), (1.11, 1.05, 1.10)]
        analysis = analyse(spec)
        for level in levels_of(analysis, LiquidityType.SWING_HIGH):
            assert level.side is LiquiditySide.BUY_SIDE  # even after price traded above

    def test_direction_maps_from_side(self):
        analysis = analyse(EQUAL_HIGHS_SPEC)
        for level in analysis.levels:
            expected = Direction.BULLISH if level.is_buy_side else Direction.BEARISH
            assert level.direction is expected


# --------------------------------------------------------------------- sweeps


class TestSweeps:
    #: Equal highs at 1.0500, then a bar wicking to 1.0600 and closing back at 1.0450.
    SWEEP_SPEC = [*EQUAL_HIGHS_SPEC, (1.0450, 1.0300), (1.0600, 1.0400, 1.0450)]

    def test_the_documented_sequence_end_to_end(self):
        """Swing A -> swing B within tolerance -> equal-high level -> price trades
        above -> price rejects -> sweep confirmed."""
        analysis = analyse(self.SWEEP_SPEC, NO_SWINGS)
        level = levels_of(analysis, LiquidityType.EQUAL_HIGHS)[0]
        sweeps = [s for s in analysis.sweeps if s.liquidity_type is LiquidityType.EQUAL_HIGHS]

        assert len(sweeps) == 1
        sweep = sweeps[0]
        assert sweep.level_id == level.level_id  # references the EXACT level
        assert sweep.is_rejection
        assert not sweep.closed_beyond

    def test_sweep_timestamps(self):
        analysis = analyse(self.SWEEP_SPEC, NO_SWINGS)
        sweep = next(s for s in analysis.sweeps if s.liquidity_type is LiquidityType.EQUAL_HIGHS)
        # Bar 8 opens 09:40, closes 09:45.
        assert sweep.event_timestamp == START + timedelta(minutes=40)
        assert sweep.confirmation_timestamp == START + timedelta(minutes=45)

    def test_penetration_distance_is_recorded(self):
        analysis = analyse(self.SWEEP_SPEC, NO_SWINGS)
        sweep = next(s for s in analysis.sweeps if s.liquidity_type is LiquidityType.EQUAL_HIGHS)
        expected = (1.0600 - 1.0500) / Symbol.EURUSD.spec.point_value
        assert sweep.penetration_points == pytest.approx(expected, rel=1e-6)
        assert sweep.extreme_price == pytest.approx(1.0600)

    def test_closed_beyond_distinguishes_break_from_rejection(self):
        rejected = analyse([*EQUAL_HIGHS_SPEC, (1.0450, 1.03), (1.0600, 1.04, 1.0450)], NO_SWINGS)
        broken = analyse([*EQUAL_HIGHS_SPEC, (1.0450, 1.03), (1.0600, 1.04, 1.0580)], NO_SWINGS)

        assert next(s for s in rejected.sweeps).is_rejection
        assert not next(s for s in broken.sweeps).is_rejection

    def test_a_wick_alone_is_enough(self):
        """Penetration is judged on the extreme, not the close — a close through is a
        structural BREAK (R2-03), which is a different concept."""
        analysis = analyse([*EQUAL_HIGHS_SPEC, (1.0450, 1.03), (1.0600, 1.04, 1.0400)], NO_SWINGS)
        assert analysis.sweeps

    def test_touching_the_level_is_not_a_sweep(self):
        analysis = analyse([*EQUAL_HIGHS_SPEC, (1.0450, 1.03), (1.0500, 1.04, 1.0450)], NO_SWINGS)
        assert [s for s in analysis.sweeps if s.liquidity_type is LiquidityType.EQUAL_HIGHS] == []

    def test_sweep_tolerance_requires_a_deeper_penetration(self):
        spec = [*EQUAL_HIGHS_SPEC, (1.0450, 1.03), (1.05001, 1.04, 1.0450)]
        loose = analyse(spec, LiquidityConfig(include_swing_levels=False))
        strict = analyse(spec, LiquidityConfig(include_swing_levels=False, sweep_tolerance_points=5))

        assert [s for s in loose.sweeps if s.liquidity_type is LiquidityType.EQUAL_HIGHS]
        assert [s for s in strict.sweeps if s.liquidity_type is LiquidityType.EQUAL_HIGHS] == []

    def test_a_consumed_level_can_never_be_swept_again(self):
        """SWEPT is terminal; the level leaves the active set."""
        spec = [
            *EQUAL_HIGHS_SPEC,
            (1.0450, 1.0300),
            (1.0600, 1.0400, 1.0450),  # sweep
            (1.0450, 1.0300),
            (1.0700, 1.0400, 1.0450),  # would sweep again if it were still active
        ]
        analysis = analyse(spec, NO_SWINGS)
        equal_sweeps = [s for s in analysis.sweeps if s.liquidity_type is LiquidityType.EQUAL_HIGHS]
        assert len(equal_sweeps) == 1

    def test_sell_side_sweep(self):
        spec = [
            (1.05, 1.0000),
            (1.04, 0.9500),
            (1.06, 1.0000),
            (1.07, 1.0100),
            (1.06, 1.0000),
            (1.05, 0.9500),
            (1.06, 1.0000),
            (1.05, 0.9400, 1.0000),  # wicks below 0.95
        ]
        analysis = analyse(spec, NO_SWINGS)
        sweeps = [s for s in analysis.sweeps if s.liquidity_type is LiquidityType.EQUAL_LOWS]
        assert len(sweeps) == 1
        assert sweeps[0].direction is Direction.BEARISH

    def test_the_creating_bar_cannot_sweep_its_own_level(self):
        """A level's price IS that bar's extreme, so it can never exceed it."""
        analysis = analyse(EQUAL_HIGHS_SPEC)
        for sweep in analysis.sweeps:
            level = analysis.level_by_id(sweep.level_id)
            assert sweep.event_timestamp > level.created_timestamp


class TestMultipleLevelsAtOnePrice:
    def test_one_bar_sweeping_several_levels_emits_one_sweep_each(self):
        """The deliberate policy: preserve individual level identity so downstream can
        distinguish 'PDH swept' from 'session high swept' even at the same price."""
        analysis = analyse(TestSweeps.SWEEP_SPEC)  # swings + equal levels both on
        by_bar: dict[datetime, list] = {}
        for sweep in analysis.sweeps:
            by_bar.setdefault(sweep.event_timestamp, []).append(sweep)

        multi = [v for v in by_bar.values() if len(v) > 1]
        assert multi, "expected at least one bar to sweep several stacked levels"
        for group in multi:
            assert len({s.level_id for s in group}) == len(group)  # distinct levels

    def test_levels_at_the_same_price_keep_separate_identities(self):
        analysis = analyse(TestSweeps.SWEEP_SPEC)
        at_price = [x for x in analysis.levels if abs(x.price_level - 1.0500) < 1e-9]
        assert len(at_price) >= 2
        assert len({x.level_id for x in at_price}) == len(at_price)
        assert len({x.liquidity_type for x in at_price}) >= 2


# ------------------------------------------------------------------ lifecycle


class TestLifecycle:
    def test_unswept_levels_are_active(self):
        analysis = analyse(EQUAL_HIGHS_SPEC)
        assert any(v is LiquidityStatus.ACTIVE for v in analysis.status.values())

    def test_swept_levels_are_terminal(self):
        analysis = analyse(TestSweeps.SWEEP_SPEC, NO_SWINGS)
        level = levels_of(analysis, LiquidityType.EQUAL_HIGHS)[0]
        assert analysis.status[level.level_id] is LiquidityStatus.SWEPT
        assert level.level_id in analysis.swept_at

    def test_approach_tracking_is_off_by_default(self):
        analysis = analyse(TestSweeps.SWEEP_SPEC)
        assert analysis.approached == {}

    def test_approach_tracking_can_be_enabled(self):
        """Bar 7 reaches 1.0450 against a 1.0500 level — 50 points short, so a
        500-point tolerance marks it approached without it being a sweep."""
        analysis = analyse(
            TestSweeps.SWEEP_SPEC,
            LiquidityConfig(include_swing_levels=False, approach_tolerance_points=1000),
        )
        assert analysis.approached
        assert any(v is LiquidityStatus.SWEPT for v in analysis.status.values())

    def test_a_distant_price_is_not_an_approach(self):
        analysis = analyse(
            TestSweeps.SWEEP_SPEC,
            LiquidityConfig(include_swing_levels=False, approach_tolerance_points=1),
        )
        assert analysis.approached == {}

    def test_approach_does_not_gate_anything(self):
        """An APPROACHED level is still fully usable liquidity."""
        analysis = analyse(
            TestSweeps.SWEEP_SPEC,
            LiquidityConfig(include_swing_levels=False, approach_tolerance_points=1000),
        )
        approached_id = next(iter(analysis.approached))
        moment = analysis.approached[approached_id]
        assert approached_id in {x.level_id for x in analysis.active_at(moment)}

    def test_active_at_is_point_in_time(self):
        analysis = analyse(TestSweeps.SWEEP_SPEC, NO_SWINGS)
        level = levels_of(analysis, LiquidityType.EQUAL_HIGHS)[0]
        swept_at = analysis.swept_at[level.level_id]

        before = {x.level_id for x in analysis.active_at(swept_at - timedelta(minutes=1))}
        after = {x.level_id for x in analysis.active_at(swept_at)}

        assert level.level_id in before  # still resting
        assert level.level_id not in after  # taken

    def test_active_at_rejects_naive_timestamps(self):
        analysis = analyse(EQUAL_HIGHS_SPEC)
        with pytest.raises(ValueError, match="timezone-aware"):
            analysis.active_at(datetime(2024, 3, 8, 10, 0))  # noqa: DTZ001

    def test_state_of_answers_every_ml_question(self):
        analysis = analyse(TestSweeps.SWEEP_SPEC, NO_SWINGS)
        level = levels_of(analysis, LiquidityType.EQUAL_HIGHS)[0]
        state = analysis.state_of(level.level_id)

        for key in (
            "price_level",
            "liquidity_type",
            "side",
            "created_timestamp",
            "confirmation_timestamp",
            "source_swing_timestamps",
            "status",
            "is_active",
            "swept",
            "swept_at",
            "penetration_points",
            "closed_beyond",
            "is_rejection",
        ):
            assert key in state, key
        assert state["swept"] is True
        assert state["is_active"] is False

    def test_state_of_unknown_level_is_none(self):
        assert analyse(EQUAL_HIGHS_SPEC).state_of("nope") is None


# -------------------------------------------------------- day / week calendar


def daily(days: int, *, start_hour: int = 22, symbol=Symbol.EURUSD):
    """One bar per hour spanning ``days`` trading days from a 22:00 UTC boundary.

    22:00 UTC is 17:00 New York under EST — the default day boundary in March 2024
    before the DST switch.
    """
    base = datetime(2024, 3, 4, start_hour, 0, tzinfo=UTC)
    spec = []
    for i in range(days * 24):
        drift = 0.0010 * (i % 24)
        spec.append((1.10 + drift + 0.0005, 1.10 + drift - 0.0005))
    return bars(spec, symbol=symbol, timeframe=Timeframe.H1, start=base)


class TestPeriodLevels:
    @pytest.fixture
    def detector(self):
        return LiquidityDetector(NO_SWINGS, SWING_1_1)

    def test_completed_days_produce_pdh_and_pdl(self, detector):
        analysis = detector.analyse(daily(3), Symbol.EURUSD, Timeframe.H1)
        assert levels_of(analysis, LiquidityType.PREVIOUS_DAY_HIGH)
        assert levels_of(analysis, LiquidityType.PREVIOUS_DAY_LOW)

    def test_pdh_confirms_at_the_day_end_not_at_the_extreme(self, detector):
        """A day's high at 15:00 cannot be known as the day's FINAL high at 15:00."""
        analysis = detector.analyse(daily(3), Symbol.EURUSD, Timeframe.H1)
        for level in levels_of(analysis, LiquidityType.PREVIOUS_DAY_HIGH):
            assert level.confirmation_timestamp == level.period_end
            assert level.confirmation_timestamp > level.created_timestamp

    def test_an_incomplete_day_produces_no_level(self, detector):
        """Only 6 hours of the first day — nothing completes."""
        frame = daily(3).iloc[:6]
        analysis = detector.analyse(frame, Symbol.EURUSD, Timeframe.H1)
        assert levels_of(analysis, LiquidityType.PREVIOUS_DAY_HIGH) == []

    def test_an_incomplete_day_is_exposed_as_pending(self, detector):
        frame = daily(3).iloc[:6]
        analysis = detector.analyse(frame, Symbol.EURUSD, Timeframe.H1)
        assert any(p.kind == "day" for p in analysis.pending)
        pending = next(p for p in analysis.pending if p.kind == "day")
        assert pending.running_high is not None
        assert pending.bar_count == 6

    def test_the_day_boundary_is_configurable(self, detector):
        utc_day = LiquidityDetector(
            LiquidityConfig(include_swing_levels=False, day_timezone="UTC", day_boundary_local=time(0, 0)),
            SWING_1_1,
        )
        ny_levels = detector.analyse(daily(3), Symbol.EURUSD, Timeframe.H1)
        utc_levels = utc_day.analyse(daily(3), Symbol.EURUSD, Timeframe.H1)

        ny_prices = {x.price_level for x in levels_of(ny_levels, LiquidityType.PREVIOUS_DAY_HIGH)}
        utc_prices = {x.price_level for x in levels_of(utc_levels, LiquidityType.PREVIOUS_DAY_HIGH)}
        assert ny_prices != utc_prices, "the day boundary is not actually configurable"

    def test_period_label_identifies_the_day(self, detector):
        analysis = detector.analyse(daily(3), Symbol.EURUSD, Timeframe.H1)
        for level in levels_of(analysis, LiquidityType.PREVIOUS_DAY_HIGH):
            assert level.period_label.startswith("day:")

    def test_an_incomplete_week_produces_no_pwh(self, detector):
        analysis = detector.analyse(daily(3), Symbol.EURUSD, Timeframe.H1)
        assert levels_of(analysis, LiquidityType.PREVIOUS_WEEK_HIGH) == []
        assert any(p.kind == "week" for p in analysis.pending)


class TestSessionLevels:
    def test_completed_sessions_become_levels(self):
        analysis = LiquidityDetector(NO_SWINGS, SWING_1_1).analyse(daily(3), Symbol.EURUSD, Timeframe.H1)
        assert levels_of(analysis, LiquidityType.SESSION_HIGH)
        assert levels_of(analysis, LiquidityType.SESSION_LOW)

    def test_session_levels_carry_r2_01_confirmation(self):
        analysis = LiquidityDetector(NO_SWINGS, SWING_1_1).analyse(daily(3), Symbol.EURUSD, Timeframe.H1)
        for level in levels_of(analysis, LiquidityType.SESSION_HIGH):
            assert level.confirmation_timestamp == level.period_end
            assert level.period_label is not None


# ---------------------------------------------------------------------- events


class TestEvents:
    def test_levels_and_sweeps_are_separate_events(self):
        detector = LiquidityDetector(NO_SWINGS, SWING_1_1)
        events = detector.events(bars(TestSweeps.SWEEP_SPEC), Symbol.EURUSD, Timeframe.M5)

        kinds = {e.event_type for e in events}
        assert EventType.EQUAL_HIGHS in kinds
        assert EventType.LIQUIDITY_SWEEP in kinds

    def test_a_sweep_event_references_its_level(self):
        detector = LiquidityDetector(NO_SWINGS, SWING_1_1)
        events = detector.events(bars(TestSweeps.SWEEP_SPEC), Symbol.EURUSD, Timeframe.M5)

        sweep = next(e for e in events if e.event_type is EventType.LIQUIDITY_SWEEP)
        level_ids = {e.metadata["level_id"] for e in events if "level_id" in e.metadata}
        assert sweep.metadata["level_id"] in level_ids

    def test_events_are_ordered_by_confirmation(self):
        detector = LiquidityDetector(LiquidityConfig(), SWING_1_1)
        events = detector.events(bars(TestSweeps.SWEEP_SPEC), Symbol.EURUSD, Timeframe.M5)
        assert [e.confirmation_timestamp for e in events] == sorted(e.confirmation_timestamp for e in events)

    def test_level_metadata_carries_the_lifecycle(self):
        detector = LiquidityDetector(NO_SWINGS, SWING_1_1)
        events = detector.events(bars(TestSweeps.SWEEP_SPEC), Symbol.EURUSD, Timeframe.M5)
        level_event = next(e for e in events if e.event_type is EventType.EQUAL_HIGHS)

        for key in ("level_id", "liquidity_type", "side", "lifecycle_status", "swept_at"):
            assert key in level_event.metadata, key

    def test_sweep_metadata_carries_rejection_information(self):
        detector = LiquidityDetector(NO_SWINGS, SWING_1_1)
        events = detector.events(bars(TestSweeps.SWEEP_SPEC), Symbol.EURUSD, Timeframe.M5)
        sweep = next(e for e in events if e.event_type is EventType.LIQUIDITY_SWEEP)

        assert "closed_beyond" in sweep.metadata
        assert "is_rejection" in sweep.metadata
        assert sweep.reference_level is not None
        assert sweep.strength is not None  # penetration in points


class TestBoundaries:
    def test_empty_frame(self):
        analysis = LiquidityDetector(LiquidityConfig(), SWING_1_1).analyse(
            bars([]), Symbol.EURUSD, Timeframe.M5
        )
        assert analysis.levels == [] and analysis.sweeps == []

    def test_too_few_bars_for_any_swing(self):
        analysis = analyse([(1.02, 0.99), (1.05, 1.00)])
        assert analysis.levels == []

    def test_one_swing_high_gives_no_equal_level(self):
        analysis = analyse([(1.02, 0.99), (1.05, 1.00), (1.03, 0.98)], NO_SWINGS)
        assert levels_of(analysis, LiquidityType.EQUAL_HIGHS) == []

    def test_levels_are_frozen(self):
        analysis = analyse(EQUAL_HIGHS_SPEC)
        with pytest.raises(Exception):  # noqa: B017 - FrozenInstanceError
            analysis.levels[0].price_level = 9.99

    def test_sweeps_are_frozen(self):
        analysis = analyse(TestSweeps.SWEEP_SPEC)
        with pytest.raises(Exception):  # noqa: B017
            analysis.sweeps[0].penetration_points = 9.99
