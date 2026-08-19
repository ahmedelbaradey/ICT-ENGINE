"""Shared test fixtures and builders."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "market_data"

#: The window the checked-in fixtures cover: 2024-03-04 00:00 UTC (inclusive) to
#: 2024-03-05 00:00 UTC (exclusive) — exactly 288 five-minute bars.
FIXTURE_START = datetime(2024, 3, 4, 0, 0, tzinfo=UTC)
FIXTURE_END = datetime(2024, 3, 5, 0, 0, tzinfo=UTC)
FIXTURE_BAR_COUNT = 288


@pytest.fixture
def fixture_root() -> Path:
    return FIXTURE_ROOT


@pytest.fixture
def tmp_data_root(tmp_path: Path) -> Path:
    return tmp_path / "data"


def make_candle(
    minute_offset: int,
    *,
    symbol: Symbol = Symbol.EURUSD,
    timeframe: Timeframe = Timeframe.M5,
    open_: float = 1.1000,
    high: float | None = None,
    low: float | None = None,
    close: float | None = None,
    volume: float = 100.0,
    start: datetime = FIXTURE_START,
) -> MarketCandle:
    """Build one candle at ``start + minute_offset`` minutes with valid OHLC defaults."""
    close = open_ if close is None else close
    body_hi, body_lo = max(open_, close), min(open_, close)
    return MarketCandle(
        timestamp=start + timedelta(minutes=minute_offset),
        symbol=symbol,
        timeframe=timeframe,
        open=open_,
        high=body_hi + 0.0005 if high is None else high,
        low=body_lo - 0.0005 if low is None else low,
        close=close,
        volume=volume,
    )


def make_frame(
    count: int,
    *,
    timeframe: Timeframe = Timeframe.M5,
    symbol: Symbol = Symbol.EURUSD,
    start: datetime = FIXTURE_START,
    step_minutes: int | None = None,
    base_price: float = 1.1000,
    price_step: float = 0.0010,
) -> pd.DataFrame:
    """A clean, contiguous, monotonically rising candle frame.

    Prices rise by a fixed step per bar so that resample expectations (open of the
    first source bar, close of the last, max high, min low) are trivially checkable
    by hand rather than by reimplementing the aggregation in the test.
    """
    step = step_minutes if step_minutes is not None else timeframe.minutes
    candles = []
    for i in range(count):
        open_ = base_price + i * price_step
        close = open_ + price_step / 2
        candles.append(
            MarketCandle(
                timestamp=start + timedelta(minutes=step * i),
                symbol=symbol,
                timeframe=timeframe,
                open=open_,
                high=close + price_step / 4,
                low=open_ - price_step / 4,
                close=close,
                volume=float(10 * (i + 1)),
            )
        )
    return candles_to_frame(candles)
