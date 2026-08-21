"""Production ingestion — native 1H and 1D, plus 4H derived from native 1H (R2-08.2).

The whole production data architecture, in five lines:

.. code-block:: text

    Provider native 1H ──────────────► Production 1H
                       └─────────────► Production 4H   (four native 1H bars, nothing else)
    Provider native 1D ──────────────► Production 1D

    ticks / 1M / 5M / 15M            ► NOT a production dependency, at any point

Ticks are never downloaded here. No minute-level series is created, persisted or read.
The tick lane still exists for the historical research fixture and is not on this path.

Three rules carry the module, and each is a way production data could be quietly wrong:

* **Provider padding is removed, not consumed.** Dukascopy fills closed periods with
  flat zero-volume candles carrying the prior close forward. Feeding those to a feature
  pipeline would be feeding it forward-filled prices. They are dropped at decode and
  counted.
* **A 4H bar needs its four 1H bars.** A window with a missing source hour is not
  compressed into a shorter candle; it is emitted only when the absence is a *proven*
  market closure, and otherwise withheld. Never three-hours-called-four.
* **Nothing is fabricated.** No gap is filled, forward-filled, interpolated or
  synthesised on any path.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from ..app.logging import get_logger
from ..domain import CANDLE_COLUMNS, Symbol, Timeframe, empty_frame
from .coverage import GapCause, SessionProfile
from .dukascopy_candles import (
    NativeCandleFetchResult,
    decode_candles,
    months_in_window,
    native_candle_url,
)

logger = get_logger(__name__)

#: The production universe's timeframes, in dependency order: the natives first, then
#: the one derived from a native.
NATIVE_PRODUCTION_TIMEFRAMES: tuple[Timeframe, ...] = (Timeframe.H1, Timeframe.D1)
DERIVED_PRODUCTION_TIMEFRAME: Timeframe = Timeframe.H4
#: 4H is four 1H bars. Stated once, used everywhere, never recomputed from `minutes`.
HOURS_PER_H4 = 4

_AGGREGATION = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


class ProductionIngestError(RuntimeError):
    """Raised when production data cannot be built without inventing something."""


@dataclass
class NativeCandleClient:
    """Downloads monthly native candle files. Network I/O only; decoding is pure.

    Sequential with a persistent session and backoff, for the same measured reason the
    tick lane is: Dukascopy's free feed serves one connection at a time and answers
    parallel clients with 503s and resets. Monthly candle files make that cheap —
    six months of 1H and 1D for two symbols is **24 requests**, against roughly 1,500
    hourly tick files for the same window.
    """

    cache_dir: Path
    base_url: str = "https://datafeed.dukascopy.com/datafeed"
    timeout_seconds: float = 45.0
    max_retries: int = 4
    backoff_seconds: float = 2.0
    _local: threading.local = field(default_factory=threading.local, repr=False)

    def fetch(
        self, symbol: Symbol, timeframe: Timeframe, start: datetime, end: datetime
    ) -> NativeCandleFetchResult:
        """Every native candle in ``[start, end)``, month by month."""
        months = months_in_window(start, end)
        result = NativeCandleFetchResult(
            symbol=symbol.value, timeframe=timeframe.value, months_requested=len(months)
        )

        frames: list[pd.DataFrame] = []
        for month in months:
            payload, retries, error = self._payload_for(symbol, timeframe, month)
            result.retries += retries
            if error is not None:
                result.download_failures.append(f"{symbol.value}/{timeframe.value} {month:%Y-%m}: {error}")
                continue
            if not payload:
                # A zero-length body is the provider saying "nothing here", which is an
                # answer rather than a failure.
                continue

            decoded = decode_candles(payload, symbol, timeframe, month)
            result.months_returned += 1
            result.records_seen += decoded.record_count
            result.padding_dropped += decoded.padding_dropped
            if len(decoded.frame):
                frames.append(decoded.frame)

        if frames:
            merged = pd.concat(frames, ignore_index=True)
            merged = merged[
                (merged["timestamp"] >= pd.Timestamp(start)) & (merged["timestamp"] < pd.Timestamp(end))
            ]
            result.frame = merged.sort_values("timestamp", kind="mergesort").reset_index(drop=True)
        return result

    # ------------------------------------------------------------------ network

    def _payload_for(
        self, symbol: Symbol, timeframe: Timeframe, month: datetime
    ) -> tuple[bytes, int, str | None]:
        cache_path = self._cache_path(symbol, timeframe, month)
        if cache_path.is_file():
            return cache_path.read_bytes(), 0, None

        url = native_candle_url(self.base_url, symbol, timeframe, month)
        payload, retries, error = self._download(url)
        if error is not None:
            return b"", retries, error

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        # Write-once: the raw archive is immutable (CLAUDE.md rule 7).
        if not cache_path.exists():
            cache_path.write_bytes(payload)
        return payload, retries, None

    def _cache_path(self, symbol: Symbol, timeframe: Timeframe, month: datetime) -> Path:
        return (
            Path(self.cache_dir)
            / symbol.dukascopy_code
            / "native"
            / timeframe.value
            / f"{month.year:04d}-{month.month:02d}.bi5"
        )

    def _session(self):
        existing = getattr(self._local, "session", None)
        if existing is not None:
            return existing

        import requests
        from requests.adapters import HTTPAdapter

        session = requests.Session()
        session.mount("https://", HTTPAdapter(pool_connections=2, pool_maxsize=2, max_retries=0))
        self._local.session = session
        return session

    def _download(self, url: str) -> tuple[bytes, int, str | None]:
        """Fetch one monthly payload. 404 is a meaningful answer, not an error to retry."""
        from .dukascopy import BROWSER_HEADERS

        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._session().get(url, timeout=self.timeout_seconds, headers=BROWSER_HEADERS)
                if response.status_code == 404:
                    return b"", attempt, None
                if response.status_code >= 500:
                    raise OSError(f"HTTP {response.status_code}")
                response.raise_for_status()
                return response.content, attempt, None
            except Exception as exc:  # noqa: BLE001 - retried, then surfaced
                last = exc
                self._local.session = None
                if attempt < self.max_retries:
                    time.sleep(self.backoff_seconds * (2**attempt))

        return b"", self.max_retries, f"{type(last).__name__}: {last}"


@dataclass(frozen=True)
class H4Window:
    """One 4H window's provenance: which native 1H bars built it, and which were absent."""

    timestamp: datetime
    present_hours: tuple[datetime, ...]
    missing_hours: tuple[datetime, ...]
    #: Missing hours the session profile proves were market-closed.
    closed_hours: tuple[datetime, ...]
    emitted: bool
    reason: str

    @property
    def cause(self) -> GapCause:
        if not self.missing_hours:
            return GapCause.NONE
        if len(self.closed_hours) == len(self.missing_hours):
            return GapCause.MARKET_CLOSED
        return GapCause.UNDETERMINED

    def as_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "present_hours": len(self.present_hours),
            "missing_hours": len(self.missing_hours),
            "closed_hours": len(self.closed_hours),
            "cause": self.cause.value,
            "emitted": self.emitted,
            "reason": self.reason,
        }


@dataclass
class H4BuildResult:
    """The derived 4H series plus a per-window account of how it was built."""

    frame: pd.DataFrame = field(default_factory=empty_frame)
    windows: tuple[H4Window, ...] = ()

    def emitted(self) -> tuple[H4Window, ...]:
        return tuple(w for w in self.windows if w.emitted)

    def withheld(self) -> tuple[H4Window, ...]:
        return tuple(w for w in self.windows if not w.emitted)

    def counts(self) -> dict[str, int]:
        return {
            "windows": len(self.windows),
            "emitted": len(self.emitted()),
            "withheld": len(self.withheld()),
            "complete": sum(1 for w in self.windows if not w.missing_hours),
            "market_closed": sum(1 for w in self.windows if w.cause is GapCause.MARKET_CLOSED),
            "undetermined": sum(1 for w in self.windows if w.cause is GapCause.UNDETERMINED),
        }


def build_h4_from_native_h1(
    hourly: pd.DataFrame,
    symbol: Symbol,
    *,
    profile: SessionProfile | None = None,
) -> H4BuildResult:
    """Aggregate native 1H bars into 4H. **The only permitted production aggregation.**

    ``open`` is the first hour's open, ``high`` the maximum of the four highs, ``low``
    the minimum of the four lows, ``close`` the last hour's close, and ``volume`` their
    sum — the provider's 1H volume is a tick count per bar, which is additive, so the
    aggregate is a tick count for the window rather than an invented quantity.

    A window is emitted when all four hours are present, **or** when every absent hour
    is a proven market closure under the session profile. Anything else is withheld:
    a three-hour window relabelled 4H would be a different candle wearing the same name,
    and nothing here fills the gap to avoid that.
    """
    if len(hourly) == 0:
        return H4BuildResult()

    known = profile or SessionProfile.from_source(hourly, Timeframe.H1, symbol)

    work = hourly.copy(deep=True)
    work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True)
    work = work.sort_values("timestamp", kind="mergesort").set_index("timestamp")

    observed_start = work.index.min()
    observed_end = work.index.max() + Timeframe.H1.duration

    grouped = work.resample(Timeframe.H4.pandas_freq, label="left", closed="left")
    aggregated = grouped.agg(_AGGREGATION)
    present_counts = grouped.size()

    windows: list[H4Window] = []
    keep: list[pd.Timestamp] = []

    # Membership is answered from a set built ONCE. Scanning ``work.index`` per window
    # made this quadratic -- 806 windows x 3120 rows -- and turned a six-month ingest
    # into minutes of pure index arithmetic.
    observed = set(work.index)

    for start, count in present_counts.items():
        if count == 0:
            continue
        expected = [start + timedelta(hours=i) for i in range(HOURS_PER_H4)]
        present = tuple(h.to_pydatetime() for h in expected if h in observed)
        missing = tuple(h.to_pydatetime() for h in expected if h not in observed)
        closed = tuple(h for h in missing if known.is_closed(pd.Timestamp(h)))

        end = start + Timeframe.H4.duration
        if start < observed_start or end > observed_end:
            emitted, reason = False, "boundary: the window is not fully inside the observed data"
        elif not missing:
            emitted, reason = True, "complete: all four native 1H bars present"
        elif len(closed) == len(missing):
            emitted, reason = True, f"market-closed: {len(closed)} absent hour(s) proven shut"
        else:
            emitted, reason = (
                False,
                f"withheld: {len(missing) - len(closed)} absent hour(s) with no proven cause",
            )

        windows.append(
            H4Window(
                timestamp=start.to_pydatetime(),
                present_hours=present,
                missing_hours=missing,
                closed_hours=closed,
                emitted=emitted,
                reason=reason,
            )
        )
        if emitted:
            keep.append(start)

    if not keep:
        return H4BuildResult(windows=tuple(windows))

    bars = aggregated.loc[keep].reset_index()
    bars["symbol"] = pd.Series([symbol.value] * len(bars), dtype="string")
    bars["timeframe"] = pd.Series([Timeframe.H4.value] * len(bars), dtype="string")
    for column in ("open", "high", "low", "close", "volume"):
        bars[column] = bars[column].astype("float64")

    return H4BuildResult(frame=bars[list(CANDLE_COLUMNS)].reset_index(drop=True), windows=tuple(windows))


@dataclass
class ProductionSeries:
    """One production (symbol, timeframe) series and how it came to exist."""

    symbol: str
    timeframe: str
    frame: pd.DataFrame
    source: str
    fetch: NativeCandleFetchResult | None = None
    h4: H4BuildResult | None = None

    def as_dict(self) -> dict:
        out: dict = {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "rows": len(self.frame),
            "source": self.source,
        }
        if self.fetch is not None:
            out["fetch"] = self.fetch.as_dict()
        if self.h4 is not None:
            out["h4_windows"] = self.h4.counts()
        return out


@dataclass
class ProductionIngestResult:
    """Everything one production ingest produced."""

    start: datetime
    end: datetime
    series: list[ProductionSeries] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return not self.failures and bool(self.series)

    def by_pair(self) -> dict[tuple[str, str], ProductionSeries]:
        return {(s.symbol, s.timeframe): s for s in self.series}

    def as_dict(self) -> dict:
        return {
            "window": {"start": self.start.isoformat(), "end": self.end.isoformat()},
            "series": [s.as_dict() for s in self.series],
            "failures": list(self.failures),
        }


@dataclass
class ProductionIngest:
    """Builds the six production series and nothing else."""

    client: NativeCandleClient

    def run(self, symbols: list[Symbol], start: datetime, end: datetime) -> ProductionIngestResult:
        result = ProductionIngestResult(start=start, end=end)

        for symbol in symbols:
            hourly: pd.DataFrame | None = None

            for timeframe in NATIVE_PRODUCTION_TIMEFRAMES:
                fetched = self.client.fetch(symbol, timeframe, start, end)
                result.failures.extend(fetched.download_failures)
                if len(fetched.frame) == 0:
                    result.failures.append(f"{symbol.value}/{timeframe.value}: no native candles returned")
                    continue

                result.series.append(
                    ProductionSeries(
                        symbol=symbol.value,
                        timeframe=timeframe.value,
                        frame=fetched.frame,
                        source=f"dukascopy-native-{timeframe.value}",
                        fetch=fetched,
                    )
                )
                if timeframe is Timeframe.H1:
                    hourly = fetched.frame

            if hourly is None or len(hourly) == 0:
                result.failures.append(
                    f"{symbol.value}/{DERIVED_PRODUCTION_TIMEFRAME.value}: no native 1H to aggregate from"
                )
                continue

            built = build_h4_from_native_h1(hourly, symbol)
            result.series.append(
                ProductionSeries(
                    symbol=symbol.value,
                    timeframe=DERIVED_PRODUCTION_TIMEFRAME.value,
                    frame=built.frame,
                    source="derived-from-native-1h",
                    h4=built,
                )
            )

        return result


__all__ = [
    "DERIVED_PRODUCTION_TIMEFRAME",
    "HOURS_PER_H4",
    "NATIVE_PRODUCTION_TIMEFRAMES",
    "H4BuildResult",
    "H4Window",
    "NativeCandleClient",
    "ProductionIngest",
    "ProductionIngestError",
    "ProductionIngestResult",
    "ProductionSeries",
    "build_h4_from_native_h1",
]
