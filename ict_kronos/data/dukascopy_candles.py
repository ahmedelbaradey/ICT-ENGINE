"""Dukascopy **native candle** backend — the production market-data source (R2-08.2).

Production trades completed 1H, 4H and Daily candles. This module fetches the two the
provider publishes natively and nothing else:

.. code-block:: text

    {base}/{SYMBOL}/{YYYY}/{MM}/BID_candles_hour_1.bi5   -> native 1H, one file per month
    {base}/{SYMBOL}/{YYYY}/{MM}/BID_candles_day_1.bi5    -> native 1D, one file per month

``MM`` is **zero-based** — July is ``06`` — which is Dukascopy's convention and the
single easiest thing to get silently wrong here.

There is no native 4H series. Every plausible path was probed against the live feed and
returns 404::

    BID_candles_hour_4.bi5   404      BID_candles_min_240.bi5   404
    BID_candles_min_5.bi5    404      BID_candles_min_15.bi5    404

so 4H is aggregated from native 1H by :mod:`ict_kronos.data.production_ingest`, and from
nothing else. **Ticks are not a production dependency.**

Record layout — 24 bytes, big-endian, identical for both timeframes::

    int32   seconds since the file's period start
    int32   open   \\
    int32   close   |  in instrument POINTS: divide by symbol.spec.point_value
    int32   low     |
    int32   high   /
    float32 volume

**The provider pads closed periods.** A market-closed hour is not absent from the file:
it is present as a flat, zero-volume placeholder carrying the previous close forward.
Measured on EURUSD July 2026: 195 of 744 hourly records, every one with
``open == high == low == close`` and ``volume == 0``, including all of Saturday::

    2026-07-04T00:00  O=1.14337 H=1.14337 L=1.14337 C=1.14337 V=0
    2026-07-04T12:00  O=1.14337 H=1.14337 L=1.14337 C=1.14337 V=0

Those are the provider's fabrication, not the market's. They are **identified and
dropped** by :func:`decode_candles`, and counted so the removal is visible rather than
silent — a forward-filled flat bar is exactly what a feature pipeline must never see.
Dropping them is not a repair: it restores the absence the market actually had.
"""

from __future__ import annotations

import lzma
import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pandas as pd

from ..app.logging import get_logger
from ..domain import CANDLE_COLUMNS, Symbol, Timeframe, empty_frame

logger = get_logger(__name__)

#: The two natively published production timeframes, and their file names.
NATIVE_CANDLE_FILES: dict[Timeframe, str] = {
    Timeframe.H1: "BID_candles_hour_1.bi5",
    Timeframe.D1: "BID_candles_day_1.bi5",
}

#: Probed against the live feed on 2026-08-21; every one returns 404. Recorded so the
#: absence is evidence rather than an assumption, and so a future probe can re-check.
KNOWN_ABSENT_NATIVE_FILES: tuple[str, ...] = (
    "BID_candles_hour_4.bi5",
    "BID_candles_min_240.bi5",
    "BID_candles_min_5.bi5",
    "BID_candles_min_15.bi5",
)

_RECORD = struct.Struct(">iiiiif")
_RECORD_BYTES = 24


class NativeCandleError(RuntimeError):
    """Raised when a native candle payload cannot be decoded."""


def native_candle_url(base_url: str, symbol: Symbol, timeframe: Timeframe, month: datetime) -> str:
    """Build the monthly native-candle URL. ``month`` is any instant inside the month."""
    if timeframe not in NATIVE_CANDLE_FILES:
        raise NativeCandleError(
            f"{timeframe.value} is not published natively by Dukascopy; native candles exist "
            f"only for {tuple(t.value for t in NATIVE_CANDLE_FILES)}"
        )
    return (
        f"{base_url.rstrip('/')}/{symbol.dukascopy_code}"
        # Dukascopy months are ZERO-BASED: January is 00, July is 06.
        f"/{month.year:04d}/{month.month - 1:02d}/{NATIVE_CANDLE_FILES[timeframe]}"
    )


@dataclass
class CandleDecodeResult:
    """Decoded candles plus the diagnostics the production audit records."""

    frame: pd.DataFrame
    record_count: int = 0
    #: Flat zero-volume placeholders the PROVIDER inserted for closed periods, removed
    #: here. Counted, never silently dropped.
    padding_dropped: int = 0
    truncated_bytes: int = 0

    def as_dict(self) -> dict:
        return {
            "rows": len(self.frame),
            "record_count": self.record_count,
            "padding_dropped": self.padding_dropped,
            "truncated_bytes": self.truncated_bytes,
        }


def is_provider_padding(open_: float, high: float, low: float, close: float, volume: float) -> bool:
    """A provider-inserted placeholder for a period in which nothing traded.

    Both conditions are required. A zero-volume bar that actually MOVED would be a
    genuine oddity worth keeping and investigating; a flat bar with real volume is a
    real, if unusual, candle. Only the conjunction identifies the padding.
    """
    return volume == 0.0 and open_ == high == low == close


def decode_candles(
    payload: bytes,
    symbol: Symbol,
    timeframe: Timeframe,
    month: datetime,
) -> CandleDecodeResult:
    """Decode one monthly native-candle payload. Pure function; no I/O.

    An empty payload is legitimate — the provider serves a zero-length body for periods
    it has no data for — and returns an empty frame rather than raising, so genuine
    absence stays distinguishable from failure.
    """
    if not payload:
        return CandleDecodeResult(frame=empty_frame())

    try:
        raw = lzma.decompress(payload)
    except lzma.LZMAError as exc:
        raise NativeCandleError(
            f"{symbol.value}/{timeframe.value} {month:%Y-%m}: payload is not valid LZMA ({exc})"
        ) from exc

    count, remainder = divmod(len(raw), _RECORD_BYTES)
    if count == 0:
        return CandleDecodeResult(frame=empty_frame(), truncated_bytes=remainder)

    point = symbol.spec.point_value
    start = datetime(month.year, month.month, 1, tzinfo=UTC)

    stamps: list[datetime] = []
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    volumes: list[float] = []
    padding = 0

    for index in range(count):
        offset, o, c, low, high, volume = _RECORD.unpack_from(raw, index * _RECORD_BYTES)
        open_price, high_price = o * point, high * point
        low_price, close_price = low * point, c * point

        if is_provider_padding(open_price, high_price, low_price, close_price, volume):
            padding += 1
            continue

        stamps.append(start + timedelta(seconds=offset))
        opens.append(open_price)
        highs.append(high_price)
        lows.append(low_price)
        closes.append(close_price)
        volumes.append(float(volume))

    if padding:
        logger.info(
            "%s/%s %s: dropped %d provider-padded flat zero-volume candle(s)",
            symbol.value,
            timeframe.value,
            f"{month:%Y-%m}",
            padding,
        )

    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(pd.Series(stamps, dtype="object"), utc=True),
            "symbol": pd.Series([symbol.value] * len(stamps), dtype="string"),
            "timeframe": pd.Series([timeframe.value] * len(stamps), dtype="string"),
            "open": pd.Series(opens, dtype="float64"),
            "high": pd.Series(highs, dtype="float64"),
            "low": pd.Series(lows, dtype="float64"),
            "close": pd.Series(closes, dtype="float64"),
            "volume": pd.Series(volumes, dtype="float64"),
        }
    )
    return CandleDecodeResult(
        frame=frame[list(CANDLE_COLUMNS)].reset_index(drop=True),
        record_count=count,
        padding_dropped=padding,
        truncated_bytes=remainder,
    )


def months_in_window(start: datetime, end: datetime) -> list[datetime]:
    """First instant of every calendar month intersecting ``[start, end)``."""
    if end <= start:
        return []
    months: list[datetime] = []
    cursor = datetime(start.year, start.month, 1, tzinfo=UTC)
    while cursor < end:
        months.append(cursor)
        cursor = (
            datetime(cursor.year + 1, 1, 1, tzinfo=UTC)
            if cursor.month == 12
            else datetime(cursor.year, cursor.month + 1, 1, tzinfo=UTC)
        )
    return months


@dataclass
class NativeCandleFetchResult:
    """One (symbol, timeframe) native download, with everything the audit needs."""

    symbol: str
    timeframe: str
    frame: pd.DataFrame = field(default_factory=empty_frame)
    months_requested: int = 0
    months_returned: int = 0
    records_seen: int = 0
    padding_dropped: int = 0
    download_failures: list[str] = field(default_factory=list)
    retries: int = 0

    @property
    def succeeded(self) -> bool:
        return not self.download_failures

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "rows": len(self.frame),
            "months_requested": self.months_requested,
            "months_returned": self.months_returned,
            "records_seen": self.records_seen,
            "padding_dropped": self.padding_dropped,
            "download_failures": list(self.download_failures),
            "retries": self.retries,
        }


__all__ = [
    "KNOWN_ABSENT_NATIVE_FILES",
    "NATIVE_CANDLE_FILES",
    "CandleDecodeResult",
    "NativeCandleError",
    "NativeCandleFetchResult",
    "decode_candles",
    "is_provider_padding",
    "months_in_window",
    "native_candle_url",
]
