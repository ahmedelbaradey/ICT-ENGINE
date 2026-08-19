"""FixtureProvider — the DEFAULT backend in dev + CI.

Deterministic, file-driven, zero network, zero heavy dependencies. Ported posture
from ``Learnexia/python/curriculum_intelligence/parsers/mock_parser.py``: the mock is
not a stub that returns garbage, it is a real backend over checked-in fixtures, so
the whole pipeline — normalizer, resampler, store, manifest — is genuinely
exercised by the default CI gate (CLAUDE.md rule 9).

Fixture layout::

    <fixture_root>/<SYMBOL>/<timeframe>.csv

with a header row ``timestamp,open,high,low,close,volume`` and ISO-8601 UTC
timestamps. ``symbol`` and ``timeframe`` are supplied by the path, not the file, so
a fixture cannot disagree with its own location.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from ..app.logging import get_logger
from ..domain import CANDLE_COLUMNS, Symbol, Timeframe, empty_frame
from .base import MarketDataError, require_utc

logger = get_logger(__name__)

_REQUIRED_CSV_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")


class FixtureProvider:
    """Serves bars from checked-in CSV fixtures."""

    def __init__(self, fixture_root: Path) -> None:
        self._root = Path(fixture_root)

    @property
    def name(self) -> str:
        return "fixture"

    def path_for(self, symbol: Symbol, timeframe: Timeframe) -> Path:
        return self._root / symbol.value / f"{timeframe.value}.csv"

    def supports(self, symbol: Symbol, timeframe: Timeframe) -> bool:
        return self.path_for(symbol, timeframe).is_file()

    def fetch(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        require_utc("start", start)
        require_utc("end", end)

        path = self.path_for(symbol, timeframe)
        if not path.is_file():
            raise MarketDataError(self.name, f"no fixture at {path}")

        raw = pd.read_csv(path)
        missing = [c for c in _REQUIRED_CSV_COLUMNS if c not in raw.columns]
        if missing:
            raise MarketDataError(self.name, f"{path} is missing columns {missing}")

        frame = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(raw["timestamp"], utc=True, format="ISO8601"),
                "symbol": pd.Series([symbol.value] * len(raw), dtype="string"),
                "timeframe": pd.Series([timeframe.value] * len(raw), dtype="string"),
                "open": raw["open"].astype("float64"),
                "high": raw["high"].astype("float64"),
                "low": raw["low"].astype("float64"),
                "close": raw["close"].astype("float64"),
                "volume": raw["volume"].astype("float64"),
            }
        )

        # Half-open window [start, end) — adjacent calls tile exactly.
        window = (frame["timestamp"] >= start) & (frame["timestamp"] < end)
        result = frame.loc[window, list(CANDLE_COLUMNS)].reset_index(drop=True)

        logger.info(
            "fixture fetch symbol=%s timeframe=%s window=[%s,%s) bars=%d",
            symbol.value,
            timeframe.value,
            start.isoformat(),
            end.isoformat(),
            len(result),
        )
        return result if len(result) else empty_frame()
