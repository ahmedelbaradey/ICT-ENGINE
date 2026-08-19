"""Immutable Parquet market-data store.

CLAUDE.md rule 7: raw market data is never overwritten. A partition is written once;
re-writing it requires an explicit ``overwrite=True``, which is logged loudly and is
never used by the ingest lane. This is what makes "reproduce the 2023 backtest"
mean something six months from now.

Layout::

    <root>/<SYMBOL>/<timeframe>/<YYYY>.parquet

Partitioning by year keeps individual files in the tens-of-megabytes range for
intraday FX, so a walk-forward window can read only the years it needs instead of
the full history.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from ..app.logging import get_logger
from ..domain import CANDLE_COLUMNS, Symbol, Timeframe, empty_frame

logger = get_logger(__name__)


class ImmutableWriteError(RuntimeError):
    """Raised on an attempt to overwrite an existing partition without opting in."""


@dataclass(frozen=True)
class PartitionInfo:
    """One written partition — the unit the manifest checksums."""

    path: Path
    symbol: str
    timeframe: str
    year: int
    rows: int
    sha256: str
    first_timestamp: str | None
    last_timestamp: str | None

    def as_dict(self) -> dict:
        return {
            "path": self.path.as_posix(),
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "year": self.year,
            "rows": self.rows,
            "sha256": self.sha256,
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
        }


class ParquetCandleStore:
    """Reads and writes canonical candle frames as year-partitioned Parquet."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def partition_path(self, symbol: Symbol, timeframe: Timeframe, year: int) -> Path:
        return self._root / symbol.value / timeframe.value / f"{year:04d}.parquet"

    # ------------------------------------------------------------------ write

    def write(
        self,
        frame: pd.DataFrame,
        symbol: Symbol,
        timeframe: Timeframe,
        *,
        overwrite: bool = False,
    ) -> list[PartitionInfo]:
        """Write a canonical candle frame, split by calendar year.

        Returns one :class:`PartitionInfo` per written partition, including a
        SHA-256 of the file bytes so the manifest can prove later that the data has
        not changed underneath a published result.
        """
        if len(frame) == 0:
            logger.info("nothing to write for %s %s", symbol.value, timeframe.value)
            return []

        missing = [c for c in CANDLE_COLUMNS if c not in frame.columns]
        if missing:
            raise ValueError(f"cannot write: frame is missing columns {missing}")

        work = frame.loc[:, list(CANDLE_COLUMNS)].copy(deep=True)
        work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True)
        work = work.sort_values("timestamp", kind="mergesort").reset_index(drop=True)

        written: list[PartitionInfo] = []
        for year, chunk in work.groupby(work["timestamp"].dt.year, sort=True):
            written.append(
                self._write_partition(
                    chunk.reset_index(drop=True), symbol, timeframe, int(year), overwrite=overwrite
                )
            )
        return written

    def _write_partition(
        self,
        chunk: pd.DataFrame,
        symbol: Symbol,
        timeframe: Timeframe,
        year: int,
        *,
        overwrite: bool,
    ) -> PartitionInfo:
        path = self.partition_path(symbol, timeframe, year)

        if path.exists() and not overwrite:
            raise ImmutableWriteError(
                f"{path} already exists. Raw market data is immutable (CLAUDE.md rule 7). "
                f"Pass overwrite=True deliberately if this partition genuinely must be replaced."
            )
        if path.exists() and overwrite:
            logger.warning(
                "OVERWRITING immutable partition %s — this breaks reproducibility of "
                "any result already published against it",
                path,
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        chunk.to_parquet(path, index=False, engine="pyarrow", compression="snappy")

        info = PartitionInfo(
            path=path,
            symbol=symbol.value,
            timeframe=timeframe.value,
            year=year,
            rows=len(chunk),
            sha256=sha256_of(path),
            first_timestamp=chunk["timestamp"].iloc[0].isoformat(),
            last_timestamp=chunk["timestamp"].iloc[-1].isoformat(),
        )
        logger.info("wrote %s (%d rows, sha256=%s)", path.as_posix(), info.rows, info.sha256[:12])
        return info

    # ------------------------------------------------------------------- read

    def read(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        *,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        """Read bars for a symbol/timeframe, optionally clipped to ``[start, end)``.

        Only the year partitions overlapping the window are opened.
        """
        directory = self._root / symbol.value / timeframe.value
        if not directory.is_dir():
            return empty_frame()

        paths = sorted(directory.glob("*.parquet"))
        if start is not None or end is not None:
            paths = [p for p in paths if self._year_in_window(int(p.stem), start, end)]
        if not paths:
            return empty_frame()

        frames = [pd.read_parquet(p, engine="pyarrow") for p in paths]
        combined = pd.concat(frames, ignore_index=True)
        combined["timestamp"] = pd.to_datetime(combined["timestamp"], utc=True)
        combined = combined.sort_values("timestamp", kind="mergesort").reset_index(drop=True)

        if start is not None:
            combined = combined.loc[combined["timestamp"] >= start]
        if end is not None:
            combined = combined.loc[combined["timestamp"] < end]

        return combined.loc[:, list(CANDLE_COLUMNS)].reset_index(drop=True)

    def available_years(self, symbol: Symbol, timeframe: Timeframe) -> list[int]:
        directory = self._root / symbol.value / timeframe.value
        if not directory.is_dir():
            return []
        return sorted(int(p.stem) for p in directory.glob("*.parquet"))

    @staticmethod
    def _year_in_window(year: int, start: pd.Timestamp | None, end: pd.Timestamp | None) -> bool:
        if start is not None and year < start.year:
            return False
        # `end` is exclusive, but a bar at exactly `end` would live in end.year, so a
        # partition for end.year can still be needed — only later years are excluded.
        if end is not None and year > end.year:
            return False
        return True


def sha256_of(path: Path) -> str:
    """SHA-256 of a file's bytes, streamed so large partitions stay cheap."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
