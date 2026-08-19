"""Domain model: Timeframe, Symbol, MarketCandle, frame validation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pandas as pd
import pytest

from ict_kronos.domain import (
    CANDLE_COLUMNS,
    InvalidCandleError,
    MarketCandle,
    Symbol,
    Timeframe,
    candles_to_frame,
    empty_frame,
    frame_to_candles,
    validate_frame,
)

from .conftest import make_candle, make_frame

# --------------------------------------------------------------------- Timeframe


class TestTimeframe:
    def test_minutes_are_correct(self):
        assert Timeframe.M1.minutes == 1
        assert Timeframe.M5.minutes == 5
        assert Timeframe.M15.minutes == 15
        assert Timeframe.H1.minutes == 60
        assert Timeframe.H4.minutes == 240
        assert Timeframe.D1.minutes == 1440

    def test_duration_matches_minutes(self):
        for tf in Timeframe:
            assert tf.duration == timedelta(minutes=tf.minutes)

    def test_ordering(self):
        assert Timeframe.H1.is_higher_than(Timeframe.M15)
        assert not Timeframe.M15.is_higher_than(Timeframe.H1)
        assert not Timeframe.H1.is_higher_than(Timeframe.H1)

    @pytest.mark.parametrize(
        ("target", "source", "expected"),
        [
            (Timeframe.M15, Timeframe.M5, True),
            (Timeframe.H1, Timeframe.M5, True),
            (Timeframe.H1, Timeframe.M15, True),
            (Timeframe.H4, Timeframe.H1, True),
            (Timeframe.D1, Timeframe.H4, True),
            (Timeframe.M5, Timeframe.M15, False),  # source slower than target
            (Timeframe.H1, Timeframe.H1, False),  # not strictly faster
        ],
    )
    def test_can_aggregate_from(self, target, source, expected):
        assert target.can_aggregate_from(source) is expected

    def test_from_string_is_forgiving_about_case_and_space(self):
        assert Timeframe.from_string("  1H ") is Timeframe.H1
        assert Timeframe.from_string("5m") is Timeframe.M5

    def test_from_string_rejects_unknown(self):
        with pytest.raises(ValueError, match="unknown timeframe"):
            Timeframe.from_string("30m")


# ------------------------------------------------------------------------ Symbol


class TestSymbol:
    def test_specs_present_for_every_member(self):
        for symbol in Symbol:
            assert symbol.spec.symbol is symbol
            assert symbol.price_precision > 0
            assert symbol.pip_size > 0

    def test_eurusd_and_xauusd_quote_differently(self):
        # A regression guard: treating XAUUSD like a 5-decimal FX pair silently
        # scales every Dukascopy price by 100x.
        assert Symbol.EURUSD.price_precision == 5
        assert Symbol.XAUUSD.price_precision == 3
        assert Symbol.EURUSD.spec.asset_class != Symbol.XAUUSD.spec.asset_class

    def test_from_string_normalizes_case(self):
        assert Symbol.from_string(" eurusd ") is Symbol.EURUSD

    def test_from_string_rejects_unknown(self):
        with pytest.raises(ValueError, match="unknown symbol"):
            Symbol.from_string("GBPUSD")


# ------------------------------------------------------------------ MarketCandle


class TestMarketCandle:
    def test_valid_candle_constructs(self):
        candle = make_candle(0, open_=1.1000, close=1.1010)
        assert candle.is_bullish
        assert not candle.is_bearish
        assert candle.range == pytest.approx(candle.high - candle.low)

    def test_close_time_is_open_plus_duration(self):
        candle = make_candle(0, timeframe=Timeframe.H1)
        assert candle.close_time == candle.timestamp + timedelta(hours=1)

    def test_naive_timestamp_is_rejected(self):
        with pytest.raises(InvalidCandleError, match="timezone-aware"):
            MarketCandle(
                timestamp=datetime(2024, 3, 4, 0, 0),  # noqa: DTZ001 - deliberate
                symbol=Symbol.EURUSD,
                timeframe=Timeframe.M5,
                open=1.1,
                high=1.2,
                low=1.0,
                close=1.15,
                volume=1.0,
            )

    def test_non_utc_timestamp_is_rejected(self):
        with pytest.raises(InvalidCandleError, match="must be UTC"):
            MarketCandle(
                timestamp=datetime(2024, 3, 4, 0, 0, tzinfo=timezone(timedelta(hours=2))),
                symbol=Symbol.EURUSD,
                timeframe=Timeframe.M5,
                open=1.1,
                high=1.2,
                low=1.0,
                close=1.15,
                volume=1.0,
            )

    def test_high_below_body_is_rejected(self):
        with pytest.raises(InvalidCandleError, match="high"):
            MarketCandle(
                timestamp=datetime(2024, 3, 4, tzinfo=UTC),
                symbol=Symbol.EURUSD,
                timeframe=Timeframe.M5,
                open=1.10,
                high=1.09,  # below the open
                low=1.08,
                close=1.095,
                volume=1.0,
            )

    def test_low_above_body_is_rejected(self):
        with pytest.raises(InvalidCandleError, match="low"):
            MarketCandle(
                timestamp=datetime(2024, 3, 4, tzinfo=UTC),
                symbol=Symbol.EURUSD,
                timeframe=Timeframe.M5,
                open=1.10,
                high=1.12,
                low=1.11,  # above the open
                close=1.115,
                volume=1.0,
            )

    def test_negative_volume_is_rejected(self):
        with pytest.raises(InvalidCandleError, match="volume"):
            make_candle(0, volume=-1.0)


# ----------------------------------------------------------------- frame helpers


class TestCandleFrame:
    def test_empty_frame_has_canonical_schema(self):
        frame = empty_frame()
        assert list(frame.columns) == list(CANDLE_COLUMNS)
        assert len(frame) == 0
        assert isinstance(frame["timestamp"].dtype, pd.DatetimeTZDtype)

    def test_roundtrip_candles_to_frame_and_back(self):
        original = [make_candle(i * 5, open_=1.1 + i * 0.001) for i in range(5)]
        frame = candles_to_frame(original)
        assert list(frame.columns) == list(CANDLE_COLUMNS)
        restored = frame_to_candles(frame)
        assert restored == original

    def test_candles_to_frame_handles_empty(self):
        assert len(candles_to_frame([])) == 0

    def test_validate_frame_accepts_clean_data(self):
        frame = make_frame(10)
        assert bool(validate_frame(frame).all())

    def test_validate_frame_rejects_missing_columns(self):
        frame = make_frame(3).drop(columns=["volume"])
        with pytest.raises(InvalidCandleError, match="missing columns"):
            validate_frame(frame)

    def test_validate_frame_rejects_naive_timestamps(self):
        frame = make_frame(3)
        frame["timestamp"] = frame["timestamp"].dt.tz_localize(None)
        with pytest.raises(InvalidCandleError, match="timezone-aware"):
            validate_frame(frame)

    def test_validate_frame_strict_raises_on_bad_ohlc(self):
        frame = make_frame(3)
        frame.loc[1, "high"] = frame.loc[1, "low"] - 1.0
        with pytest.raises(InvalidCandleError, match="violate OHLC invariants"):
            validate_frame(frame)

    def test_validate_frame_non_strict_flags_instead_of_raising(self):
        frame = make_frame(3)
        frame.loc[1, "high"] = frame.loc[1, "low"] - 1.0
        ok = validate_frame(frame, strict=False)
        assert list(ok) == [True, False, True]

    def test_validate_frame_flags_nan_prices(self):
        frame = make_frame(3)
        frame.loc[2, "close"] = float("nan")
        ok = validate_frame(frame, strict=False)
        assert not bool(ok.iloc[2])
