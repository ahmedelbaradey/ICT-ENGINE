"""The production timeframe lock, and the Daily discrepancy it forced us to name.

Production trades completed **1H / 4H / Daily** candles on EURUSD and XAUUSD. Lower
timeframes stay fully supported for research and regression, and are refused at the
production boundary — because a lower timeframe reaching a model does not look like an
error, it looks like more data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame
from ict_kronos.features import (
    DatasetSpec,
    TargetSpec,
    TargetType,
)
from ict_kronos.features.production import (
    PRODUCTION_SYMBOLS,
    PRODUCTION_TIMEFRAMES,
    RESEARCH_ONLY_TIMEFRAMES,
    ProductionTargetParameters,
    ProductionUniverseError,
    assert_production_pair,
    build_production_dataset,
    is_production_timeframe,
)
from ict_kronos.ict import MarketStateBuilder, TrueDailyOpenDetector

START = datetime(2026, 6, 1, tzinfo=UTC)

#: The zigzag the R2-07 suites use, so the same price sequence can be stamped at any
#: timeframe and the detector output compared like for like.
TREND = [
    1.0000, 1.0020, 1.0040, 1.0060, 1.0030, 1.0010, 1.0025, 1.0050,
    1.0080, 1.0100, 1.0070, 1.0120, 1.0090, 1.0140, 1.0110, 1.0160,
    1.0130, 1.0180, 1.0150, 1.0200, 1.0170, 1.0090, 1.0050, 1.0020,
]  # fmt: skip


def bars(timeframe, prices=TREND, *, symbol=Symbol.EURUSD, start=START, wick=0.0005):
    return candles_to_frame(
        [
            MarketCandle(
                timestamp=start + timedelta(minutes=timeframe.minutes * i),
                symbol=symbol,
                timeframe=timeframe,
                open=p,
                high=p + wick,
                low=p - wick,
                close=p,
                volume=1.0,
            )
            for i, p in enumerate(prices)
        ]
    )


class TestTheUniverseLock:
    def test_the_production_universe_is_exactly_1h_4h_and_daily(self):
        assert PRODUCTION_TIMEFRAMES == (Timeframe.H1, Timeframe.H4, Timeframe.D1)
        assert PRODUCTION_SYMBOLS == (Symbol.EURUSD, Symbol.XAUUSD)

    def test_every_production_pair_is_accepted(self):
        for symbol in PRODUCTION_SYMBOLS:
            for timeframe in PRODUCTION_TIMEFRAMES:
                assert_production_pair(symbol, timeframe)

    @pytest.mark.parametrize("timeframe", RESEARCH_ONLY_TIMEFRAMES)
    def test_every_research_timeframe_is_refused(self, timeframe):
        with pytest.raises(ProductionUniverseError, match="research/regression"):
            assert_production_pair(Symbol.EURUSD, timeframe)

    def test_the_refusal_names_what_production_actually_trades(self):
        """An error that only says "no" sends the reader to the source to find out why."""
        with pytest.raises(ProductionUniverseError) as caught:
            assert_production_pair(Symbol.EURUSD, Timeframe.M5)
        message = str(caught.value)
        assert "1h" in message and "4h" in message and "1d" in message

    def test_research_and_production_timeframes_do_not_overlap(self):
        assert set(RESEARCH_ONLY_TIMEFRAMES).isdisjoint(PRODUCTION_TIMEFRAMES)

    def test_is_production_timeframe_agrees_with_the_guard(self):
        for timeframe in (*PRODUCTION_TIMEFRAMES, *RESEARCH_ONLY_TIMEFRAMES):
            expected = timeframe in PRODUCTION_TIMEFRAMES
            assert is_production_timeframe(timeframe) is expected

    def test_the_guard_raises_rather_than_filtering(self):
        """A dropped combination is indistinguishable from one that produced no rows."""
        spec = DatasetSpec(
            targets=(TargetSpec(name="r", target_type=TargetType.FUTURE_RETURN, horizon_bars=2),)
        )
        with pytest.raises(ProductionUniverseError):
            build_production_dataset(bars(Timeframe.M15), Symbol.EURUSD, Timeframe.M15, spec)

    def test_a_production_pair_builds_normally(self):
        spec = DatasetSpec(
            targets=(TargetSpec(name="r", target_type=TargetType.FUTURE_RETURN, horizon_bars=2),)
        )
        dataset = build_production_dataset(bars(Timeframe.H1), Symbol.EURUSD, Timeframe.H1, spec)
        assert len(dataset) == len(TREND)

    def test_the_universe_is_a_constant_not_configuration(self):
        """A configurable production universe is one env var away from 1-minute bars."""
        from tests.test_market_state import _code_of

        code = _code_of("ict_kronos/features/production.py")
        for banned in ("os.environ", "getenv", "from_env", "Settings"):
            assert banned not in code, f"production.py reads configuration: {banned!r}"


class TestDetectorsAreTimeframeAgnostic:
    """The same price sequence, stamped at any timeframe, must give the same geometry.

    This is what makes the lower-timeframe regression suite worth anything: if a
    detector behaved differently on Daily bars, "validated on 15m" would say nothing
    about production.

    **Liquidity is the deliberate exception**, and it is exercised separately below.
    """

    def state(self, timeframe):
        return MarketStateBuilder().analyse(bars(timeframe), Symbol.EURUSD, timeframe).states()[-1]

    def geometry(self, timeframe):
        """Price-geometry counts only — everything that reads bars and nothing else."""
        state = self.state(timeframe)
        return {
            "breaks": state.structure.bos_count + state.structure.mss_count + state.structure.choch_count,
            "structure_state": state.structure.state,
            "fvg": state.imbalance.bullish_fvg_count + state.imbalance.bearish_fvg_count,
            "ifvg": state.imbalance.ifvg_count,
            "bpr": state.imbalance.bpr_count,
            "order_blocks": (
                state.institutional.bullish_order_block_count + state.institutional.bearish_order_block_count
            ),
            "breakers": (
                state.institutional.bullish_breaker_count + state.institutional.bearish_breaker_count
            ),
            "rdrb": state.composites.rdrb_count,
            "unicorn": state.composites.unicorn_count,
            "delivery": state.composites.delivery_state,
            "has_range": state.premium_discount.range_id is not None,
            "zone": state.premium_discount.zone,
        }

    def test_daily_produces_the_same_geometry_as_five_minute(self):
        assert self.geometry(Timeframe.D1) == self.geometry(Timeframe.M5)

    def test_every_production_timeframe_agrees_with_the_research_one(self):
        reference = self.geometry(Timeframe.M5)
        for timeframe in PRODUCTION_TIMEFRAMES:
            assert self.geometry(timeframe) == reference, f"{timeframe.value} diverges"

    def test_the_fixture_is_not_vacuously_empty(self):
        """Zero everywhere would make the agreement above meaningless."""
        found = self.geometry(Timeframe.D1)
        assert found["breaks"] > 0 and found["fvg"] > 0 and found["has_range"]

    def test_liquidity_is_session_anchored_and_therefore_NOT_timeframe_agnostic(self):
        """The one legitimate divergence, asserted rather than left as a surprise.

        Liquidity levels are session highs and lows. Twenty-four bars span two hours at
        5m and twenty-four days at 1D, so the same prices sit inside a different number
        of trading sessions and produce a different number of levels. That is the
        detector being right about the calendar, not being inconsistent about price.
        """
        counts = {
            timeframe: (
                self.state(timeframe).liquidity.buy_side_count
                + self.state(timeframe).liquidity.sell_side_count
            )
            for timeframe in (Timeframe.M5, Timeframe.H1, Timeframe.H4)
        }
        assert len(set(counts.values())) > 1, (
            "liquidity is session-anchored; identical counts across timeframes would mean "
            "the session dimension had stopped mattering"
        )


class TestTheDailyOpenDiscrepancy:
    """A documented, deliberate consequence — pinned so it cannot change silently.

    `Timeframe.D1` here is a **UTC-midnight** day. R2-05.1's True Daily Open is 00:00
    **America/New_York**. They never coincide, so a D1 frame yields no daily-open level.
    See `docs/features/production_universe.md` §2.
    """

    def daily_frame(self, days=40):
        prices = [1.0 + 0.002 * i for i in range(days)]
        return candles_to_frame(
            [
                MarketCandle(
                    timestamp=START + timedelta(days=i),
                    symbol=Symbol.EURUSD,
                    timeframe=Timeframe.D1,
                    open=p,
                    high=p + 0.004,
                    low=p - 0.004,
                    close=p,
                    volume=1.0,
                )
                for i, p in enumerate(prices)
            ]
        )

    def test_the_new_york_boundary_is_not_utc_midnight(self):
        instant, anomaly = TrueDailyOpenDetector().boundary_for(datetime(2026, 6, 15).date())
        assert instant.hour == 4, "00:00 New York in EDT is 04:00 UTC"
        assert anomaly.value == "none"

        winter, _ = TrueDailyOpenDetector().boundary_for(datetime(2026, 1, 15).date())
        assert winter.hour == 5, "00:00 New York in EST is 05:00 UTC"

    def test_a_daily_frame_yields_no_true_daily_open(self):
        levels = TrueDailyOpenDetector().detect(self.daily_frame(), Symbol.EURUSD, Timeframe.D1)
        assert levels == [], "a UTC-midnight grid cannot contain a 00:00 New York boundary bar"

    def test_the_state_reports_the_absence_rather_than_inventing_a_level(self):
        state = MarketStateBuilder().analyse(self.daily_frame(), Symbol.EURUSD, Timeframe.D1).states()[-1]
        assert state.daily_open.level_id is None
        assert state.daily_open.price is None
        assert state.daily_open.distance_points is None
        assert state.session.trading_day_age_minutes is None

    def test_the_daily_open_IS_available_on_the_hourly_production_timeframe(self):
        """The feature is not lost to production — 1H bars do contain the boundary."""
        hours = 24 * 5
        frame = candles_to_frame(
            [
                MarketCandle(
                    timestamp=START + timedelta(hours=i),
                    symbol=Symbol.EURUSD,
                    timeframe=Timeframe.H1,
                    open=1.0 + 0.0001 * i,
                    high=1.0 + 0.0001 * i + 0.0004,
                    low=1.0 + 0.0001 * i - 0.0004,
                    close=1.0 + 0.0001 * i,
                    volume=1.0,
                )
                for i in range(hours)
            ]
        )
        levels = TrueDailyOpenDetector().detect(frame, Symbol.EURUSD, Timeframe.H1)
        assert levels, "a 00:00 New York boundary lands exactly on a 1H bar open"
        assert all(level.event_timestamp.hour in (4, 5) for level in levels)


class TestNoLowerTimeframeBackDoor:
    """A production label may never be resolved by looking at a finer timeframe."""

    @staticmethod
    def _code(path):
        from tests.test_market_state import _code_of

        return _code_of(path)

    def test_the_target_engine_never_reaches_for_another_timeframe(self):
        code = self._code("ict_kronos/features/targets.py")
        # ``resample(`` -- the CALL. ``targets.py`` legitimately imports ``with_close_time``
        # from the resampler MODULE, which attaches close times to the frame it was
        # given; it never converts that frame to another timeframe.
        for banned in ("resample(", "Timeframe.M1", "Timeframe.M5", "Timeframe.M15", "ParquetCandleStore"):
            assert banned not in code, f"targets.py reaches for other bars: {banned!r}"
        assert "with_close_time" in code, "the guard must still be reading real code"

    def test_the_same_bar_rule_is_still_unresolved_and_not_broken_by_a_finer_look(self):
        from ict_kronos.features import TargetEngine, TpSlOutcome, TradeSide, UnresolvedReason

        point = Symbol.EURUSD.spec.point_value
        rows = [
            (1.0, 1.0, 1.0, 1.0),
            (1.0, 1.0 + 30 * point, 1.0 - 30 * point, 1.0 + 29 * point),
            (1.0, 1.0, 1.0, 1.0),
        ]
        frame = candles_to_frame(
            [
                MarketCandle(
                    timestamp=START + timedelta(hours=i),
                    symbol=Symbol.EURUSD,
                    timeframe=Timeframe.H1,
                    open=o,
                    high=h,
                    low=low,
                    close=c,
                    volume=1.0,
                )
                for i, (o, h, low, c) in enumerate(rows)
            ]
        )
        engine = TargetEngine(symbol=Symbol.EURUSD, timeframe=Timeframe.H1, frame=frame)
        spec = TargetSpec(
            name="tpsl",
            target_type=TargetType.TP_BEFORE_SL,
            horizon_bars=2,
            side=TradeSide.LONG,
            take_profit_points=20.0,
            stop_loss_points=20.0,
        )
        value = engine.value_at(spec, engine.observation_instants()[0])
        assert value.outcome is TpSlOutcome.UNRESOLVED
        assert value.unresolved_reason is UnresolvedReason.SAME_BAR_AMBIGUITY


class TestProductionTargetParameters:
    def test_parameters_carry_their_rationale(self):
        params = ProductionTargetParameters(
            symbol=Symbol.XAUUSD,
            timeframe=Timeframe.H1,
            threshold_points=500.0,
            take_profit_points=1500.0,
            stop_loss_points=1500.0,
            horizons=(1, 2, 4),
            rationale="median 1H bar range",
        )
        assert params.as_dict()["rationale"] == "median 1H bar range"
        assert params.as_dict()["horizons"] == [1, 2, 4]

    def test_parameters_are_immutable(self):
        from dataclasses import FrozenInstanceError

        params = ProductionTargetParameters(
            symbol=Symbol.EURUSD,
            timeframe=Timeframe.H1,
            threshold_points=20.0,
            take_profit_points=50.0,
            stop_loss_points=50.0,
            horizons=(1,),
        )
        with pytest.raises(FrozenInstanceError):
            params.threshold_points = 1.0
