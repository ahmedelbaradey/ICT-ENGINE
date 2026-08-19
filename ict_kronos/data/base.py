"""MarketDataProvider — the contract every data backend implements.

Ported shape: ``Learnexia/python/curriculum_intelligence/parsers/base.py`` — a narrow
Protocol with a ``name`` for logging, so the factory can swap a deterministic
fixture backend for a live one without any caller changing (CLAUDE.md rule 9).

A provider's ONLY job is to return raw bars for a requested window. It does not
normalize, deduplicate, resample, or persist — those are separate, separately
tested stages. Keeping fetch dumb is what lets the fixture and live backends be
exercised by the identical normalizer test suite.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

import pandas as pd

from ..domain import Symbol, Timeframe


class MarketDataError(RuntimeError):
    """A provider failed to retrieve data. Carries the provider name for diagnostics."""

    def __init__(self, provider: str, message: str) -> None:
        super().__init__(f"[{provider}] {message}")
        self.provider = provider


@runtime_checkable
class MarketDataProvider(Protocol):
    """Fetches raw OHLCV bars for a symbol/timeframe/window."""

    @property
    def name(self) -> str:
        """Backend key used in logs and in the dataset manifest's provenance record."""
        ...

    def supports(self, symbol: Symbol, timeframe: Timeframe) -> bool:
        """Whether this backend can serve the given pair at all."""
        ...

    def fetch(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Return bars whose OPEN time falls in ``[start, end)``.

        The window is half-open so that adjacent calls tile without overlap or gap.
        Both bounds must be timezone-aware UTC.

        The returned frame carries :data:`ict_kronos.domain.CANDLE_COLUMNS`. It may
        contain duplicates, be unsorted, or contain gaps — the normalizer is what
        guarantees those properties, not the provider.
        """
        ...


def require_utc(name: str, value: datetime) -> datetime:
    """Guard every provider boundary against naive datetimes.

    Naive timestamps are the single most common source of silent off-by-one-session
    bugs in FX research, so they are rejected at the edge rather than coerced.
    """
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware UTC; got naive {value!r}")
    return value
