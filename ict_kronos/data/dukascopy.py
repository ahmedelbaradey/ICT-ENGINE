"""Dukascopy tick backend — the LIVE market-data source (opt-in).

Selected only when ``MARKET_DATA_BACKEND=dukascopy``. ``requests`` is imported
lazily inside the network path so the default CI gate never needs it
(CLAUDE.md rule 9).

Design split, deliberate and load-bearing for testability:

* :func:`decode_bi5`, :func:`ticks_to_frame`, :func:`aggregate_ticks_to_bars` are
  **pure functions** over bytes and DataFrames. They carry all the parsing and
  aggregation correctness risk, and they are fully unit-tested against synthetic
  payloads with no network.
* :class:`DukascopyProvider` is the thin I/O shell: build a URL, fetch, cache,
  delegate to the pure functions. Only this part needs the network, and it is
  marked ``@pytest.mark.live``.

**Wire format.** Dukascopy publishes one LZMA-compressed ``.bi5`` file per hour at::

    {base}/{SYMBOL}/{YYYY}/{MM}/{DD}/{HH}h_ticks.bi5

where ``MM`` is **zero-indexed** (January = ``00``) — a notorious off-by-one that
:func:`bi5_url` centralises and :func:`tests` pin. Each decompressed record is 20
bytes, big-endian::

    uint32  milliseconds since the top of the hour
    uint32  ask price, in integer points
    uint32  bid price, in integer points
    float32 ask volume
    float32 bid volume

Integer prices are scaled by ``10 ** price_precision`` for the instrument.

**Bar construction.** Ticks are aggregated on the **bid** by default (the
conventional FX charting convention, and the side a long exit executes against).
This is configurable, not hardcoded, per CLAUDE.md rule 4. ``volume`` is the tick
count — Dukascopy's per-tick volumes are indicative broker volumes, not exchange
volume, so a tick count is the more honest and more reproducible measure. This is
recorded in the manifest so no downstream consumer mistakes it for real volume.
"""

from __future__ import annotations

import lzma
import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

import numpy as np
import pandas as pd

from ..app.logging import get_logger
from ..domain import CANDLE_COLUMNS, Symbol, Timeframe, empty_frame
from .base import MarketDataError, require_utc

logger = get_logger(__name__)

#: Big-endian: ms-offset, ask(points), bid(points), ask-volume, bid-volume.
_TICK_STRUCT = struct.Struct(">IIIff")
TICK_RECORD_BYTES = _TICK_STRUCT.size  # 20

TICK_COLUMNS: tuple[str, ...] = ("timestamp", "bid", "ask", "bid_volume", "ask_volume")


class PriceSide(StrEnum):
    """Which side of the spread the OHLC bars are built from."""

    BID = "bid"
    ASK = "ask"
    MID = "mid"


@dataclass(frozen=True)
class TickDecodeResult:
    """Decoded ticks plus the diagnostics the ingest lane records."""

    frame: pd.DataFrame
    record_count: int
    truncated_bytes: int


def bi5_url(base_url: str, symbol: Symbol, hour: datetime) -> str:
    """Build the Dukascopy hourly tick URL.

    ``hour`` must be a timezone-aware UTC datetime truncated to the hour. The month
    component is **zero-indexed** in Dukascopy's scheme.
    """
    require_utc("hour", hour)
    hour = hour.astimezone(UTC)
    return (
        f"{base_url.rstrip('/')}/{symbol.dukascopy_code}"
        f"/{hour.year:04d}/{hour.month - 1:02d}/{hour.day:02d}"
        f"/{hour.hour:02d}h_ticks.bi5"
    )


def decode_bi5(payload: bytes, symbol: Symbol, hour: datetime) -> TickDecodeResult:
    """Decode one hourly ``.bi5`` payload into a tick frame.

    An empty payload is legitimate and common — Dukascopy serves a zero-length body
    for hours with no ticks (weekends, holidays, market closures). That is returned
    as an empty frame, NOT an error; genuine absence and failure must stay
    distinguishable so the gap report means something.

    A trailing partial record is reported via ``truncated_bytes`` rather than
    silently dropped, because a truncated download that decoded "fine" would be an
    invisible data-quality hole.
    """
    require_utc("hour", hour)
    hour = hour.astimezone(UTC)

    if not payload:
        return TickDecodeResult(frame=_empty_tick_frame(), record_count=0, truncated_bytes=0)

    try:
        raw = lzma.decompress(payload)
    except lzma.LZMAError as exc:
        raise MarketDataError("dukascopy", f"corrupt bi5 payload for {symbol.value} {hour}: {exc}") from exc

    if not raw:
        return TickDecodeResult(frame=_empty_tick_frame(), record_count=0, truncated_bytes=0)

    count, remainder = divmod(len(raw), TICK_RECORD_BYTES)
    if count == 0:
        return TickDecodeResult(frame=_empty_tick_frame(), record_count=0, truncated_bytes=remainder)

    usable = raw[: count * TICK_RECORD_BYTES]
    records = np.frombuffer(
        usable,
        dtype=np.dtype(
            [
                ("ms", ">u4"),
                ("ask", ">u4"),
                ("bid", ">u4"),
                ("ask_volume", ">f4"),
                ("bid_volume", ">f4"),
            ]
        ),
    )

    scale = float(10**symbol.price_precision)
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(hour, utc=True)
            + pd.to_timedelta(records["ms"].astype("int64"), unit="ms"),
            "bid": records["bid"].astype("float64") / scale,
            "ask": records["ask"].astype("float64") / scale,
            "bid_volume": records["bid_volume"].astype("float64"),
            "ask_volume": records["ask_volume"].astype("float64"),
        }
    )
    return TickDecodeResult(
        frame=frame[list(TICK_COLUMNS)],
        record_count=count,
        truncated_bytes=remainder,
    )


def _empty_tick_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.Series(dtype="datetime64[ns, UTC]"),
            "bid": pd.Series(dtype="float64"),
            "ask": pd.Series(dtype="float64"),
            "bid_volume": pd.Series(dtype="float64"),
            "ask_volume": pd.Series(dtype="float64"),
        }
    )


def ticks_to_frame(results: list[TickDecodeResult]) -> pd.DataFrame:
    """Concatenate decoded hourly results into one chronologically sorted tick frame."""
    frames = [r.frame for r in results if len(r.frame)]
    if not frames:
        return _empty_tick_frame()
    combined = pd.concat(frames, ignore_index=True)
    return combined.sort_values("timestamp", kind="mergesort").reset_index(drop=True)


def aggregate_ticks_to_bars(
    ticks: pd.DataFrame,
    symbol: Symbol,
    timeframe: Timeframe,
    *,
    side: PriceSide = PriceSide.BID,
) -> pd.DataFrame:
    """Aggregate a tick frame into OHLCV bars on ``timeframe`` boundaries.

    Bars are labelled by their OPEN time and left-closed — a bar timestamped
    ``10:00`` contains ticks in ``[10:00, 10:00 + duration)``. This matches the
    CLAUDE.md timestamp convention exactly, and it is the property that makes
    ``close_time`` a valid point-in-time observability anchor.

    Empty periods produce NO bar. A synthetic flat bar would be a fabricated
    observation, and every downstream ICT detector would treat it as real price
    action. Absence is preserved and reported by the gap detector instead.
    """
    if len(ticks) == 0:
        return empty_frame()

    price = _side_series(ticks, side)
    work = pd.DataFrame({"timestamp": ticks["timestamp"], "price": price}).set_index("timestamp")

    grouped = work["price"].resample(timeframe.pandas_freq, label="left", closed="left")
    bars = grouped.ohlc()
    bars["volume"] = grouped.count().astype("float64")

    # Drop empty periods: resample emits a NaN row for every gap in the range.
    bars = bars.loc[bars["volume"] > 0]
    if bars.empty:
        return empty_frame()

    bars = bars.reset_index().rename(columns={"timestamp": "timestamp"})
    bars["symbol"] = pd.Series([symbol.value] * len(bars), dtype="string")
    bars["timeframe"] = pd.Series([timeframe.value] * len(bars), dtype="string")
    for col in ("open", "high", "low", "close", "volume"):
        bars[col] = bars[col].astype("float64")
    return bars[list(CANDLE_COLUMNS)].reset_index(drop=True)


def _side_series(ticks: pd.DataFrame, side: PriceSide) -> pd.Series:
    if side is PriceSide.BID:
        return ticks["bid"].astype("float64")
    if side is PriceSide.ASK:
        return ticks["ask"].astype("float64")
    return (ticks["bid"].astype("float64") + ticks["ask"].astype("float64")) / 2.0


def hours_in_window(start: datetime, end: datetime) -> list[datetime]:
    """Every UTC hour boundary in the half-open window ``[start, end)``."""
    require_utc("start", start)
    require_utc("end", end)
    cursor = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    stop = end.astimezone(UTC)
    hours: list[datetime] = []
    while cursor < stop:
        hours.append(cursor)
        cursor += timedelta(hours=1)
    return hours


class DukascopyProvider:
    """Live tick provider. Network I/O only; all parsing lives in the pure functions above."""

    def __init__(
        self,
        base_url: str,
        cache_dir: Path,
        *,
        timeout_seconds: float = 30.0,
        side: PriceSide = PriceSide.BID,
    ) -> None:
        self._base_url = base_url
        self._cache_dir = Path(cache_dir)
        self._timeout = timeout_seconds
        self._side = side

    @property
    def name(self) -> str:
        return "dukascopy"

    def supports(self, symbol: Symbol, timeframe: Timeframe) -> bool:
        # Ticks aggregate up to any supported bar interval.
        return isinstance(symbol, Symbol) and isinstance(timeframe, Timeframe)

    def fetch(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        require_utc("start", start)
        require_utc("end", end)

        hours = hours_in_window(start, end)
        logger.info(
            "dukascopy fetch symbol=%s timeframe=%s hours=%d window=[%s,%s)",
            symbol.value,
            timeframe.value,
            len(hours),
            start.isoformat(),
            end.isoformat(),
        )

        decoded = [decode_bi5(self._payload_for(symbol, hour), symbol, hour) for hour in hours]

        truncated = sum(d.truncated_bytes for d in decoded)
        if truncated:
            logger.warning(
                "dukascopy %s: %d trailing byte(s) across the window did not form whole tick "
                "records — treat this window as suspect",
                symbol.value,
                truncated,
            )

        ticks = ticks_to_frame(decoded)
        if len(ticks) == 0:
            return empty_frame()

        # Ticks were fetched by whole hour; clip back to the requested half-open window
        # so the caller's [start, end) contract holds exactly.
        ticks = ticks.loc[(ticks["timestamp"] >= start) & (ticks["timestamp"] < end)]
        return aggregate_ticks_to_bars(ticks, symbol, timeframe, side=self._side)

    def _payload_for(self, symbol: Symbol, hour: datetime) -> bytes:
        """Return the raw ``.bi5`` bytes for one hour, using the on-disk cache first.

        The cache is what makes a multi-year backfill restartable and makes a
        re-run byte-identical — a precondition for the reproducibility rule.
        """
        cache_path = self._cache_path(symbol, hour)
        if cache_path.is_file():
            return cache_path.read_bytes()

        payload = self._download(bi5_url(self._base_url, symbol, hour))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(payload)
        return payload

    def _cache_path(self, symbol: Symbol, hour: datetime) -> Path:
        hour = hour.astimezone(UTC)
        return (
            self._cache_dir
            / symbol.dukascopy_code
            / f"{hour.year:04d}"
            / f"{hour.month:02d}"
            / f"{hour.day:02d}"
            / f"{hour.hour:02d}h_ticks.bi5"
        )

    def _download(self, url: str) -> bytes:
        # Lazy import: `requests` is an opt-in extra and must not be needed by the
        # default (fixture-backed) CI gate.
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise MarketDataError(
                self.name,
                "the 'dukascopy' extra is not installed; run `pip install -e \".[dukascopy]\"`",
            ) from exc

        response = requests.get(url, timeout=self._timeout)
        if response.status_code == 404:
            # No file for this hour — a normal market closure, not a failure.
            return b""
        if response.status_code != 200:
            raise MarketDataError(self.name, f"HTTP {response.status_code} for {url}")
        return response.content
