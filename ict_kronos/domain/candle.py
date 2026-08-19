"""MarketCandle — the canonical market-data record (Master Plan §12).

Two representations, deliberately:

* :class:`MarketCandle` — a single immutable bar. Used in tests, fixtures, and any
  place where per-bar reasoning must be explicit and readable.
* :data:`CANDLE_COLUMNS` / :func:`candles_to_frame` / :func:`frame_to_candles` — the
  DataFrame form used by the pipeline, because ICT detection and feature building
  operate over millions of bars and must be vectorised.

The DataFrame form is the contract for everything downstream. Its invariants are
asserted by :func:`validate_frame` and enforced by the normalizer.

CLAUDE.md timestamp convention: ``timestamp`` is the bar's OPEN time, UTC and
timezone-aware. The bar covers ``[timestamp, timestamp + timeframe.duration)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd

from .symbol import Symbol
from .timeframe import Timeframe

# The canonical column order (Master Plan §12 minimum schema). Raw OHLCV only —
# ICT features NEVER live in this frame (CLAUDE.md rule 7).
CANDLE_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "symbol",
    "timeframe",
    "open",
    "high",
    "low",
    "close",
    "volume",
)

_PRICE_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close")


class InvalidCandleError(ValueError):
    """Raised when a candle or candle frame violates a structural invariant."""


@dataclass(frozen=True)
class MarketCandle:
    """A single OHLCV bar.

    Immutable by design: raw market data is never mutated in place (CLAUDE.md rule 7).
    """

    timestamp: datetime
    symbol: Symbol
    timeframe: Timeframe
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise InvalidCandleError(f"timestamp must be timezone-aware (UTC); got naive {self.timestamp!r}")
        if self.timestamp.utcoffset() != UTC.utcoffset(None):
            raise InvalidCandleError(f"timestamp must be UTC; got offset {self.timestamp.utcoffset()}")
        self._validate_ohlc()
        if self.volume < 0:
            raise InvalidCandleError(f"volume must be >= 0; got {self.volume}")

    def _validate_ohlc(self) -> None:
        """High must bound the bar above, low must bound it below.

        A bar that violates this is not a rounding artefact — it is corrupt data,
        and silently accepting it would poison every downstream swing, FVG and
        liquidity detection.
        """
        if self.high < max(self.open, self.close):
            raise InvalidCandleError(
                f"high {self.high} < max(open {self.open}, close {self.close}) at {self.timestamp}"
            )
        if self.low > min(self.open, self.close):
            raise InvalidCandleError(
                f"low {self.low} > min(open {self.open}, close {self.close}) at {self.timestamp}"
            )
        if self.high < self.low:
            raise InvalidCandleError(f"high {self.high} < low {self.low} at {self.timestamp}")

    @property
    def close_time(self) -> datetime:
        """When this bar closes — i.e. the first instant it becomes observable.

        This is the anchor for every point-in-time correctness rule: a bar's data
        MUST NOT be used to make a decision timestamped before ``close_time``.
        """
        return self.timestamp + self.timeframe.duration

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open


def empty_frame() -> pd.DataFrame:
    """An empty candle frame with the canonical dtypes.

    Returned instead of ``pd.DataFrame()`` so that downstream code can rely on the
    schema even when a provider yields nothing.
    """
    frame = pd.DataFrame(
        {
            "timestamp": pd.Series(dtype="datetime64[ns, UTC]"),
            "symbol": pd.Series(dtype="string"),
            "timeframe": pd.Series(dtype="string"),
            "open": pd.Series(dtype="float64"),
            "high": pd.Series(dtype="float64"),
            "low": pd.Series(dtype="float64"),
            "close": pd.Series(dtype="float64"),
            "volume": pd.Series(dtype="float64"),
        }
    )
    return frame[list(CANDLE_COLUMNS)]


def candles_to_frame(candles: list[MarketCandle]) -> pd.DataFrame:
    """Build a canonical candle frame from :class:`MarketCandle` objects."""
    if not candles:
        return empty_frame()
    frame = pd.DataFrame(
        {
            "timestamp": [c.timestamp for c in candles],
            "symbol": [c.symbol.value for c in candles],
            "timeframe": [c.timeframe.value for c in candles],
            "open": [c.open for c in candles],
            "high": [c.high for c in candles],
            "low": [c.low for c in candles],
            "close": [c.close for c in candles],
            "volume": [c.volume for c in candles],
        }
    )
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame["symbol"] = frame["symbol"].astype("string")
    frame["timeframe"] = frame["timeframe"].astype("string")
    for col in (*_PRICE_COLUMNS, "volume"):
        frame[col] = frame[col].astype("float64")
    return frame[list(CANDLE_COLUMNS)]


def frame_to_candles(frame: pd.DataFrame) -> list[MarketCandle]:
    """Materialise a candle frame back into objects. Test/debug convenience only —
    never call this on a full history."""
    return [
        MarketCandle(
            timestamp=row.timestamp.to_pydatetime(),
            symbol=Symbol.from_string(row.symbol),
            timeframe=Timeframe.from_string(row.timeframe),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
        )
        for row in frame.itertuples(index=False)
    ]


def validate_frame(frame: pd.DataFrame, *, strict: bool = True) -> pd.Series:
    """Check the structural invariants of a candle frame.

    Returns a boolean Series that is True for every VALID row. With ``strict=True``
    (the default) any violation raises instead — callers that want to quarantine
    bad rows rather than fail pass ``strict=False`` and filter on the result.

    Checks:
      1. the canonical columns are present
      2. ``timestamp`` is timezone-aware UTC
      3. ``high >= max(open, close)`` and ``low <= min(open, close)``
      4. ``high >= low``
      5. ``volume >= 0``
      6. no NaN in any price column
    """
    missing = [c for c in CANDLE_COLUMNS if c not in frame.columns]
    if missing:
        raise InvalidCandleError(f"candle frame is missing columns: {missing}")

    if len(frame) == 0:
        return pd.Series(dtype="bool", index=frame.index)

    ts = frame["timestamp"]
    if not isinstance(ts.dtype, pd.DatetimeTZDtype):
        raise InvalidCandleError(f"timestamp must be timezone-aware; got dtype {ts.dtype}")
    if str(ts.dtype.tz) != "UTC":
        raise InvalidCandleError(f"timestamp must be UTC; got tz {ts.dtype.tz}")

    prices = frame[list(_PRICE_COLUMNS)]
    upper = frame[["open", "close"]].max(axis=1)
    lower = frame[["open", "close"]].min(axis=1)

    ok = (
        prices.notna().all(axis=1)
        & frame["volume"].notna()
        & (frame["high"] >= upper)
        & (frame["low"] <= lower)
        & (frame["high"] >= frame["low"])
        & (frame["volume"] >= 0)
    )

    if strict and not bool(ok.all()):
        bad = frame.loc[~ok, ["timestamp", "open", "high", "low", "close", "volume"]]
        raise InvalidCandleError(
            f"{int((~ok).sum())} candle(s) violate OHLC invariants; first offenders:\n{bad.head(5)}"
        )
    return ok
