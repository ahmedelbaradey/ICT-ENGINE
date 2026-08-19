"""Canonical market-data domain model (Master Plan §12)."""

from .candle import (
    CANDLE_COLUMNS,
    InvalidCandleError,
    MarketCandle,
    candles_to_frame,
    empty_frame,
    frame_to_candles,
    validate_frame,
)
from .symbol import Symbol, SymbolSpec
from .timeframe import Timeframe

__all__ = [
    "CANDLE_COLUMNS",
    "InvalidCandleError",
    "MarketCandle",
    "Symbol",
    "SymbolSpec",
    "Timeframe",
    "candles_to_frame",
    "empty_frame",
    "frame_to_candles",
    "validate_frame",
]
