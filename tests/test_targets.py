"""R2-08 prediction targets — the only layer allowed to read the future.

Three properties carry this module, and each is a way a dataset could be quietly wrong:

* **Unresolved is a real answer.** A target that cannot be computed must never become
  ``0``, ``NEUTRAL`` or ``False`` — a model trained on that is being taught that missing
  data means indecision.
* **Intrabar order is unknowable.** A bar touching both barriers says nothing about
  which came first, and inventing an order fabricates the label.
* **Every convention is declared.** Reference price, window bounds, threshold unit and
  boundary precedence are all explicit, because each of them silently changes what the
  model is being asked to predict.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame
from ict_kronos.features import (
    TARGET_SCHEMA_VERSION,
    TargetDirection,
    TargetEngine,
    TargetSpec,
    TargetSpecError,
    TargetType,
    TargetValue,
    TpSlOutcome,
    TradeSide,
    UnresolvedReason,
)

START = datetime(2024, 3, 8, 9, 0, tzinfo=UTC)
M5 = Timeframe.M5
SYM = Symbol.EURUSD
POINT = SYM.spec.point_value


def frame_of(rows, *, symbol=SYM, timeframe=M5, start=START):
    """Bars from explicit ``(open, high, low, close)`` tuples — no derived wicks.

    Every TP/SL and excursion test depends on exact highs and lows, so the fixture
    states them rather than computing them from the close.
    """
    return candles_to_frame(
        [
            MarketCandle(
                timestamp=start + timedelta(minutes=timeframe.minutes * i),
                symbol=symbol,
                timeframe=timeframe,
                open=o,
                high=h,
                low=low,
                close=c,
                volume=1.0,
            )
            for i, (o, h, low, c) in enumerate(rows)
        ]
    )


def flat(count, price=1.0):
    return [(price, price, price, price)] * count


def engine_of(rows, **kwargs):
    return TargetEngine(symbol=SYM, timeframe=M5, frame=frame_of(rows, **kwargs))


def closes(*prices):
    """Bars whose OHLC all equal the close — movement without wick ambiguity."""
    return [(p, p, p, p) for p in prices]


class TestSpecValidation:
    def test_a_horizon_below_one_is_refused(self):
        for bad in (0, -1):
            with pytest.raises(TargetSpecError, match="horizon_bars"):
                TargetSpec(name="x", target_type=TargetType.FUTURE_RETURN, horizon_bars=bad)

    def test_a_horizon_of_zero_would_make_the_target_readable_at_prediction_time(self):
        """The reason the check exists, stated as a test rather than a comment."""
        with pytest.raises(TargetSpecError, match="readable at prediction time"):
            TargetSpec(name="x", target_type=TargetType.DIRECTION, horizon_bars=0, threshold_points=1.0)

    def test_direction_requires_a_threshold(self):
        with pytest.raises(TargetSpecError, match="threshold_points"):
            TargetSpec(name="d", target_type=TargetType.DIRECTION, horizon_bars=4)

    def test_a_negative_threshold_is_refused(self):
        with pytest.raises(TargetSpecError, match="must be >= 0"):
            TargetSpec(name="d", target_type=TargetType.DIRECTION, horizon_bars=4, threshold_points=-1.0)

    def test_a_zero_threshold_is_allowed_because_it_is_a_real_configuration(self):
        spec = TargetSpec(name="d", target_type=TargetType.DIRECTION, horizon_bars=4, threshold_points=0.0)
        assert spec.threshold_points == 0.0

    def test_a_threshold_on_a_non_direction_target_is_refused(self):
        """Silently ignoring it would let a caller believe a return target was filtered."""
        with pytest.raises(TargetSpecError, match="only for DIRECTION"):
            TargetSpec(name="r", target_type=TargetType.FUTURE_RETURN, horizon_bars=4, threshold_points=5.0)

    def test_tp_sl_requires_an_explicit_side(self):
        with pytest.raises(TargetSpecError, match="explicit side"):
            TargetSpec(
                name="t",
                target_type=TargetType.TP_BEFORE_SL,
                horizon_bars=4,
                take_profit_points=10.0,
                stop_loss_points=10.0,
            )

    def test_tp_sl_requires_positive_distances(self):
        for bad in (0.0, -5.0):
            with pytest.raises(TargetSpecError, match="must be > 0"):
                TargetSpec(
                    name="t",
                    target_type=TargetType.TP_BEFORE_SL,
                    horizon_bars=4,
                    side=TradeSide.LONG,
                    take_profit_points=bad,
                    stop_loss_points=10.0,
                )

    def test_tp_sl_fields_on_another_target_type_are_refused(self):
        with pytest.raises(TargetSpecError, match="only for TP_BEFORE_SL"):
            TargetSpec(name="r", target_type=TargetType.FUTURE_RETURN, horizon_bars=4, side=TradeSide.LONG)

    def test_an_unnamed_specification_is_refused(self):
        with pytest.raises(TargetSpecError, match="named"):
            TargetSpec(name="", target_type=TargetType.FUTURE_RETURN, horizon_bars=1)

    def test_a_spec_records_its_version(self):
        spec = TargetSpec(name="r", target_type=TargetType.FUTURE_RETURN, horizon_bars=1)
        assert spec.version == TARGET_SCHEMA_VERSION

    def test_a_spec_round_trips(self):
        for spec in (
            TargetSpec(name="r", target_type=TargetType.FUTURE_RETURN, horizon_bars=4),
            TargetSpec(name="d", target_type=TargetType.DIRECTION, horizon_bars=8, threshold_points=12.5),
            TargetSpec(name="e", target_type=TargetType.EXCURSION, horizon_bars=2),
            TargetSpec(
                name="t",
                target_type=TargetType.TP_BEFORE_SL,
                horizon_bars=6,
                side=TradeSide.SHORT,
                take_profit_points=20.0,
                stop_loss_points=10.0,
            ),
        ):
            assert TargetSpec.from_dict(spec.as_dict()) == spec


class TestFutureReturn:
    SPEC = TargetSpec(name="ret", target_type=TargetType.FUTURE_RETURN, horizon_bars=2)

    def test_the_reference_is_the_close_of_the_bar_being_observed(self):
        engine = engine_of(closes(1.0, 1.1, 1.2, 1.3))
        value = engine.value_at(self.SPEC, engine.observation_instants()[0])
        assert value.reference_price == pytest.approx(1.0)
        assert value.reference_timestamp == engine.observation_instants()[0]

    def test_the_window_starts_at_the_next_bar_and_ends_at_bar_i_plus_h(self):
        engine = engine_of(closes(1.0, 1.1, 1.2, 1.3))
        instants = engine.observation_instants()
        value = engine.value_at(self.SPEC, instants[0])
        assert value.future_window_start == instants[1]
        assert value.future_window_end == instants[2]

    def test_the_return_is_close_to_close(self):
        engine = engine_of(closes(1.0, 1.5, 1.2, 1.3))
        value = engine.value_at(self.SPEC, engine.observation_instants()[0])
        assert value.future_return == pytest.approx((1.2 - 1.0) / 1.0)

    def test_the_move_is_also_reported_in_instrument_points(self):
        engine = engine_of(closes(1.0, 1.0, 1.0002))
        value = engine.value_at(self.SPEC, engine.observation_instants()[0])
        assert value.future_move_points == pytest.approx(0.0002 / POINT)

    def test_an_intermediate_bar_never_affects_a_close_to_close_return(self):
        """The convention is close-to-close; a spike between the two is not the answer."""
        calm = engine_of(closes(1.0, 1.1, 1.2))
        spiky = engine_of([(1.0, 1.0, 1.0, 1.0), (1.1, 9.0, 0.1, 1.1), (1.2, 1.2, 1.2, 1.2)])
        a = calm.value_at(self.SPEC, calm.observation_instants()[0])
        b = spiky.value_at(self.SPEC, spiky.observation_instants()[0])
        assert a.future_return == pytest.approx(b.future_return)

    def test_zero_movement_is_a_resolved_zero_not_a_missing_value(self):
        engine = engine_of(flat(4))
        value = engine.value_at(self.SPEC, engine.observation_instants()[0])
        assert value.resolved is True
        assert value.future_return == 0.0
        assert value.future_move_points == 0.0


class TestHorizonBoundaries:
    SPEC = TargetSpec(name="ret", target_type=TargetType.FUTURE_RETURN, horizon_bars=2)

    def test_a_horizon_ending_exactly_on_the_last_bar_resolves(self):
        engine = engine_of(closes(1.0, 1.1, 1.2))
        value = engine.value_at(self.SPEC, engine.observation_instants()[0])
        assert value.resolved is True
        assert value.future_window_end == engine.observation_instants()[-1]

    def test_a_horizon_one_bar_past_the_end_is_unresolved(self):
        engine = engine_of(closes(1.0, 1.1, 1.2))
        value = engine.value_at(self.SPEC, engine.observation_instants()[1])
        assert value.resolved is False
        assert value.unresolved_reason is UnresolvedReason.INSUFFICIENT_HISTORY
        assert value.future_return is None
        assert value.future_move_points is None

    def test_an_unresolved_target_is_never_zero(self):
        """The distinction the whole missing-value policy exists to protect."""
        engine = engine_of(closes(1.0, 1.1, 1.2))
        value = engine.value_at(self.SPEC, engine.observation_instants()[-1])
        assert value.future_return is None and value.future_move_points is None

    def test_a_single_bar_horizon_reads_exactly_the_next_bar(self):
        spec = TargetSpec(name="one", target_type=TargetType.FUTURE_RETURN, horizon_bars=1)
        engine = engine_of(closes(1.0, 1.4, 9.9))
        instants = engine.observation_instants()
        value = engine.value_at(spec, instants[0])
        assert value.future_window_start == value.future_window_end == instants[1]
        assert value.future_return == pytest.approx(0.4)

    def test_the_final_bar_can_never_resolve_a_forward_target(self):
        engine = engine_of(closes(1.0, 1.1, 1.2, 1.3))
        for spec in (
            TargetSpec(name="a", target_type=TargetType.FUTURE_RETURN, horizon_bars=1),
            TargetSpec(name="b", target_type=TargetType.EXCURSION, horizon_bars=1),
        ):
            value = engine.value_at(spec, engine.observation_instants()[-1])
            assert value.resolved is False
            assert value.unresolved_reason is UnresolvedReason.INSUFFICIENT_HISTORY

    def test_an_instant_that_is_not_a_bar_close_is_refused(self):
        engine = engine_of(closes(1.0, 1.1, 1.2))
        between = engine.observation_instants()[0] + timedelta(seconds=1)
        with pytest.raises(TargetSpecError, match="not a bar close"):
            engine.value_at(self.SPEC, between)


class TestDirection:
    def spec(self, threshold, horizon=2):
        return TargetSpec(
            name="dir",
            target_type=TargetType.DIRECTION,
            horizon_bars=horizon,
            threshold_points=threshold,
        )

    def direction_for(self, move_points, threshold):
        """Build a frame whose 2-bar move is exactly ``move_points``."""
        end = 1.0 + move_points * POINT
        engine = engine_of(closes(1.0, 1.0, end))
        return engine.value_at(self.spec(threshold), engine.observation_instants()[0])

    def test_a_move_above_the_threshold_is_up(self):
        assert self.direction_for(30.0, 20.0).direction is TargetDirection.UP

    def test_a_move_below_the_negative_threshold_is_down(self):
        assert self.direction_for(-30.0, 20.0).direction is TargetDirection.DOWN

    def test_a_move_inside_the_band_is_neutral(self):
        assert self.direction_for(5.0, 20.0).direction is TargetDirection.NEUTRAL

    def test_exactly_the_threshold_classifies_as_up(self):
        """``>=`` is the declared rule, so the boundary belongs to the direction."""
        value = self.direction_for(20.0, 20.0)
        assert value.direction is TargetDirection.UP
        assert value.future_move_points == pytest.approx(20.0)

    def test_exactly_the_negative_threshold_classifies_as_down(self):
        assert self.direction_for(-20.0, 20.0).direction is TargetDirection.DOWN

    def test_just_inside_the_threshold_is_neutral(self):
        assert self.direction_for(19.0, 20.0).direction is TargetDirection.NEUTRAL

    def test_a_zero_threshold_makes_neutral_unreachable_and_zero_resolves_up(self):
        """A declared property of a degenerate configuration, not a hidden tie-break.

        With a threshold of 0 the UP and DOWN conditions overlap at exactly zero. UP is
        checked first, so a flat market classifies UP and NEUTRAL can never occur. That
        is recorded here so nobody discovers it from a confusing class balance.
        """
        assert self.direction_for(0.0, 0.0).direction is TargetDirection.UP
        assert self.direction_for(-1.0, 0.0).direction is TargetDirection.DOWN
        assert self.direction_for(1.0, 0.0).direction is TargetDirection.UP

    def test_an_unresolved_direction_is_never_neutral(self):
        engine = engine_of(closes(1.0, 1.1, 1.2))
        value = engine.value_at(self.spec(10.0), engine.observation_instants()[-1])
        assert value.resolved is False
        assert value.direction is None
        assert value.direction is not TargetDirection.NEUTRAL

    def test_the_threshold_is_never_derived_from_the_data(self):
        """Same bars, two thresholds, two answers — proof nothing is fitted."""
        engine = engine_of(closes(1.0, 1.0, 1.0 + 25 * POINT))
        moment = engine.observation_instants()[0]
        assert engine.value_at(self.spec(20.0), moment).direction is TargetDirection.UP
        assert engine.value_at(self.spec(50.0), moment).direction is TargetDirection.NEUTRAL

    def test_the_exact_boundary_survives_binary_float_representation(self):
        """Why the points conversion is rounded, stated as a test.

        ``(1.0002 - 1.0) / 1e-5`` evaluates to ``19.999999999997797`` in binary floating
        point. Compared raw, a move of exactly the threshold would classify NEUTRAL and
        the declared ``>=`` rule would be quietly false at the one place it is stated.
        """
        raw = (1.0 + 20 * POINT - 1.0) / POINT
        assert raw != 20.0, "if this ever becomes exact the rounding can be revisited"
        value = self.direction_for(20.0, 20.0)
        assert value.future_move_points == 20.0
        assert value.direction is TargetDirection.UP

    def test_the_threshold_unit_is_points_so_instruments_do_not_rescale(self):
        """20 points is 20 points on both instruments — the same *distance* question."""
        move = 25
        for symbol in (Symbol.EURUSD, Symbol.XAUUSD):
            point = symbol.spec.point_value
            engine = TargetEngine(
                symbol=symbol,
                timeframe=M5,
                frame=frame_of(closes(1.0, 1.0, 1.0 + move * point), symbol=symbol),
            )
            value = engine.value_at(self.spec(20.0), engine.observation_instants()[0])
            assert value.direction is TargetDirection.UP
            assert value.future_move_points == pytest.approx(move)


class TestExcursion:
    SPEC = TargetSpec(name="exc", target_type=TargetType.EXCURSION, horizon_bars=2)

    def test_up_and_down_are_reported_separately(self):
        """No single ambiguous MFE/MAE: the long and short readings stay distinguishable."""
        engine = engine_of([(1.0, 1.0, 1.0, 1.0), (1.0, 1.0030, 0.9990, 1.0), (1.0, 1.0010, 0.9950, 1.0)])
        value = engine.value_at(self.SPEC, engine.observation_instants()[0])
        assert value.up_excursion_points == pytest.approx(0.0030 / POINT)
        assert value.down_excursion_points == pytest.approx(0.0050 / POINT)

    def test_the_reference_bar_itself_is_excluded_from_the_window(self):
        """A spike on the observed bar is already known at T and is not the future."""
        spiky = engine_of([(1.0, 5.0, 0.5, 1.0), (1.0, 1.001, 0.999, 1.0), (1.0, 1.0, 1.0, 1.0)])
        value = spiky.value_at(self.SPEC, spiky.observation_instants()[0])
        assert value.up_excursion_points == pytest.approx(0.001 / POINT)

    def test_excursions_are_signed_rather_than_clamped(self):
        """A window that never trades above the reference has a NEGATIVE up-excursion.

        Clamping it to zero would assert the market touched a price it never touched.
        """
        engine = engine_of([(1.0, 1.0, 1.0, 1.0), (0.99, 0.995, 0.990, 0.99), (0.98, 0.985, 0.980, 0.98)])
        value = engine.value_at(self.SPEC, engine.observation_instants()[0])
        assert value.up_excursion_points < 0
        assert value.down_excursion_points > 0

    def test_a_partial_window_is_unresolved_rather_than_understated(self):
        """Half a window would report a smaller excursion — a wrong answer, not a partial one."""
        engine = engine_of(closes(1.0, 1.1, 1.2))
        value = engine.value_at(self.SPEC, engine.observation_instants()[1])
        assert value.resolved is False
        assert value.up_excursion_points is None and value.down_excursion_points is None

    def test_a_degenerate_flat_window_gives_zero_excursions_not_missing_ones(self):
        engine = engine_of(flat(4))
        value = engine.value_at(self.SPEC, engine.observation_instants()[0])
        assert value.resolved is True
        assert value.up_excursion_points == 0.0
        assert value.down_excursion_points == 0.0


class TestTpBeforeSl:
    def spec(self, side=TradeSide.LONG, tp=20.0, sl=20.0, horizon=4):
        return TargetSpec(
            name="tpsl",
            target_type=TargetType.TP_BEFORE_SL,
            horizon_bars=horizon,
            side=side,
            take_profit_points=tp,
            stop_loss_points=sl,
        )

    def test_take_profit_reached_first(self):
        engine = engine_of([(1.0, 1.0, 1.0, 1.0), (1.0, 1.00025, 0.99990, 1.0001), (1.0, 1.0, 0.99, 0.99)])
        value = engine.value_at(self.spec(), engine.observation_instants()[0])
        assert value.outcome is TpSlOutcome.TP_FIRST
        assert value.resolved is True
        assert value.resolving_bar_timestamp == engine.observation_instants()[1]

    def test_stop_loss_reached_first(self):
        engine = engine_of([(1.0, 1.0, 1.0, 1.0), (1.0, 1.00010, 0.99975, 0.9999), (1.0, 1.01, 1.0, 1.01)])
        value = engine.value_at(self.spec(), engine.observation_instants()[0])
        assert value.outcome is TpSlOutcome.SL_FIRST

    def test_a_short_reverses_which_barrier_is_the_profit(self):
        """Identical bars, opposite sides, opposite outcomes — the side is never inferred."""
        rows = [(1.0, 1.0, 1.0, 1.0), (1.0, 1.00010, 0.99975, 0.9999), (1.0, 1.0, 1.0, 1.0)]
        engine = engine_of(rows)
        moment = engine.observation_instants()[0]
        assert engine.value_at(self.spec(TradeSide.LONG), moment).outcome is TpSlOutcome.SL_FIRST
        assert engine.value_at(self.spec(TradeSide.SHORT), moment).outcome is TpSlOutcome.TP_FIRST

    def test_both_barriers_in_one_bar_is_unresolved(self):
        """OHLC records no sequence, so there is no honest answer. The brief's hard rule."""
        engine = engine_of([(1.0, 1.0, 1.0, 1.0), (1.0, 1.0030, 0.9970, 1.0), (1.0, 1.0, 1.0, 1.0)])
        value = engine.value_at(self.spec(), engine.observation_instants()[0])
        assert value.resolved is False
        assert value.unresolved_reason is UnresolvedReason.SAME_BAR_AMBIGUITY
        assert value.outcome is TpSlOutcome.UNRESOLVED
        assert value.resolving_bar_timestamp == engine.observation_instants()[1]

    def test_a_same_bar_touch_is_not_broken_by_the_close(self):
        """A close above the entry must not be read as "so TP came first"."""
        engine = engine_of([(1.0, 1.0, 1.0, 1.0), (1.0, 1.0030, 0.9970, 1.0029), (1.0, 1.0, 1.0, 1.0)])
        value = engine.value_at(self.spec(), engine.observation_instants()[0])
        assert value.outcome is TpSlOutcome.UNRESOLVED

    def test_an_exact_touch_of_the_barrier_counts_as_a_touch(self):
        exact = 1.0 + 20 * POINT
        engine = engine_of([(1.0, 1.0, 1.0, 1.0), (1.0, exact, 0.9999, 1.0), (1.0, 1.0, 1.0, 1.0)])
        value = engine.value_at(self.spec(), engine.observation_instants()[0])
        assert value.outcome is TpSlOutcome.TP_FIRST

    def test_no_touch_within_the_horizon_is_a_distinct_reason(self):
        engine = engine_of(flat(6))
        value = engine.value_at(self.spec(horizon=2), engine.observation_instants()[0])
        assert value.resolved is False
        assert value.unresolved_reason is UnresolvedReason.NO_TOUCH_WITHIN_HORIZON

    def test_running_out_of_bars_is_a_different_reason_from_never_touching(self):
        engine = engine_of(flat(3))
        value = engine.value_at(self.spec(horizon=8), engine.observation_instants()[0])
        assert value.unresolved_reason is UnresolvedReason.INSUFFICIENT_HISTORY

    def test_a_race_decided_early_resolves_even_when_the_horizon_runs_off_the_end(self):
        """Later bars cannot change an outcome that already happened."""
        engine = engine_of([(1.0, 1.0, 1.0, 1.0), (1.0, 1.00025, 0.99990, 1.0001)])
        value = engine.value_at(self.spec(horizon=16), engine.observation_instants()[0])
        assert value.resolved is True
        assert value.outcome is TpSlOutcome.TP_FIRST

    def test_the_first_touching_bar_wins_even_if_a_later_bar_touches_the_other(self):
        engine = engine_of(
            [
                (1.0, 1.0, 1.0, 1.0),
                (1.0, 1.00025, 0.99990, 1.0001),
                (1.0, 1.0, 0.99900, 0.999),
            ]
        )
        value = engine.value_at(self.spec(), engine.observation_instants()[0])
        assert value.outcome is TpSlOutcome.TP_FIRST

    def test_asymmetric_distances_are_honoured(self):
        engine = engine_of([(1.0, 1.0, 1.0, 1.0), (1.0, 1.00009, 0.99994, 1.0), (1.0, 1.0, 1.0, 1.0)])
        moment = engine.observation_instants()[0]
        # TP 5 points away is touched; SL 10 points away is not.
        assert engine.value_at(self.spec(tp=5.0, sl=10.0), moment).outcome is TpSlOutcome.TP_FIRST
        # TP 100 points away is not; SL 5 points away is.
        assert engine.value_at(self.spec(tp=100.0, sl=5.0), moment).outcome is TpSlOutcome.SL_FIRST


class TestMalformedData:
    """Malformed bars are reported, never repaired. CLAUDE.md rule 7's spirit."""

    SPEC = TargetSpec(name="ret", target_type=TargetType.FUTURE_RETURN, horizon_bars=2)

    def frame_with_nan(self, column, position):
        frame = frame_of(closes(1.0, 1.1, 1.2, 1.3)).copy()
        frame.loc[frame.index[position], column] = math.nan
        return TargetEngine(symbol=SYM, timeframe=M5, frame=frame)

    def test_a_nan_in_the_future_close_is_unresolved_not_interpolated(self):
        engine = self.frame_with_nan("close", 2)
        value = engine.value_at(self.SPEC, engine.observation_instants()[0])
        assert value.resolved is False
        assert value.unresolved_reason is UnresolvedReason.MALFORMED_FUTURE_BAR

    def test_a_nan_reference_price_is_unresolved(self):
        engine = self.frame_with_nan("close", 0)
        value = engine.value_at(self.SPEC, engine.observation_instants()[0])
        assert value.resolved is False
        assert value.unresolved_reason is UnresolvedReason.MALFORMED_FUTURE_BAR

    def test_a_nan_high_breaks_an_excursion_but_not_a_close_to_close_return(self):
        """Only the values a formula actually reads can invalidate it."""
        engine = self.frame_with_nan("high", 2)
        moment = engine.observation_instants()[0]
        excursion = TargetSpec(name="e", target_type=TargetType.EXCURSION, horizon_bars=2)
        assert engine.value_at(excursion, moment).resolved is False
        assert engine.value_at(self.SPEC, moment).resolved is True

    def test_a_nan_inside_a_tp_sl_window_is_unresolved(self):
        engine = self.frame_with_nan("high", 1)
        spec = TargetSpec(
            name="t",
            target_type=TargetType.TP_BEFORE_SL,
            horizon_bars=2,
            side=TradeSide.LONG,
            take_profit_points=5.0,
            stop_loss_points=5.0,
        )
        value = engine.value_at(spec, engine.observation_instants()[0])
        assert value.unresolved_reason is UnresolvedReason.MALFORMED_FUTURE_BAR


class TestTimezones:
    def test_every_emitted_timestamp_is_timezone_aware_utc(self):
        engine = engine_of(closes(1.0, 1.1, 1.2, 1.3))
        spec = TargetSpec(name="r", target_type=TargetType.FUTURE_RETURN, horizon_bars=2)
        value = engine.value_at(spec, engine.observation_instants()[0])
        for moment in (
            value.reference_timestamp,
            value.future_window_start,
            value.future_window_end,
        ):
            assert moment is not None and moment.tzinfo is not None
            assert moment.utcoffset() == timedelta(0)


class TestSerialization:
    SPECS = (
        TargetSpec(name="ret", target_type=TargetType.FUTURE_RETURN, horizon_bars=2),
        TargetSpec(name="dir", target_type=TargetType.DIRECTION, horizon_bars=2, threshold_points=5.0),
        TargetSpec(name="exc", target_type=TargetType.EXCURSION, horizon_bars=2),
        TargetSpec(
            name="tpsl",
            target_type=TargetType.TP_BEFORE_SL,
            horizon_bars=2,
            side=TradeSide.LONG,
            take_profit_points=10.0,
            stop_loss_points=10.0,
        ),
    )

    def values(self):
        engine = engine_of(closes(1.0, 1.001, 1.002, 1.003, 1.004))
        return [
            engine.value_at(spec, moment) for spec in self.SPECS for moment in engine.observation_instants()
        ]

    def test_every_value_round_trips_exactly(self):
        found = self.values()
        assert found
        for value in found:
            assert TargetValue.from_dict(value.as_dict()) == value

    def test_unresolved_values_round_trip_including_their_reason(self):
        unresolved = [v for v in self.values() if not v.resolved]
        assert unresolved, "the fixture must produce unresolved values"
        for value in unresolved:
            restored = TargetValue.from_dict(value.as_dict())
            assert restored == value
            assert restored.unresolved_reason is value.unresolved_reason

    def test_enums_serialise_as_their_string_values(self):
        value = next(v for v in self.values() if v.direction is not None)
        payload = value.as_dict()
        assert payload["direction"] in {"up", "down", "neutral"}
        assert payload["target_type"] == "direction"

    def test_missing_values_are_none_and_never_zero(self):
        value = next(v for v in self.values() if not v.resolved)
        payload = value.as_dict()
        for key in ("future_return", "future_move_points", "direction"):
            assert payload[key] is None

    def test_a_value_is_immutable(self):
        from dataclasses import FrozenInstanceError

        value = self.values()[0]
        with pytest.raises(FrozenInstanceError):
            value.resolved = True


class TestTheTemporalBoundaryIsStructural:
    """The feature layer and the target layer must not be able to reach each other."""

    @staticmethod
    def _code(path):
        from tests.test_market_state import _code_of

        return _code_of(path)

    def test_the_target_engine_never_imports_a_feature(self):
        code = self._code("ict_kronos/features/targets.py")
        for banned in ("market_state", "feature_vector", "ICTFeatureVector", "ICTMarketState"):
            assert banned not in code, f"targets.py reaches into the feature layer: {banned!r}"

    def test_the_feature_layer_never_imports_a_target(self):
        for path in ("ict_kronos/ict/market_state.py", "ict_kronos/ict/feature_vector.py"):
            code = self._code(path)
            for banned in ("targets", "TargetValue", "future_return", "TargetSpec"):
                assert banned not in code, f"{path} reaches into the target layer: {banned!r}"

    def test_the_target_engine_runs_no_detector(self):
        code = self._code("ict_kronos/features/targets.py")
        for banned in ("Detector", "is_observable_at", "confirmation_timestamp"):
            assert banned not in code, f"targets.py duplicates detector logic: {banned!r}"

    def test_the_guard_actually_reads_code(self):
        code = self._code("ict_kronos/features/targets.py")
        assert "def value_at(" in code
        assert '"""' not in code
