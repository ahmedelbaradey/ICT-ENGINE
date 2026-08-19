"""Symbol — the instruments under study, with their quoting conventions.

The MVP (Master Plan §31) is EURUSD and XAUUSD. Both are carried from Phase 1 so
that Phase 8's cross-instrument robustness check has two genuinely different
instruments (a major FX pair and a metal) rather than two correlated pairs.

``pip_size`` and ``price_precision`` are quoting metadata, NOT trading assumptions
— they describe how the instrument is priced, not how it should be traded. Costs
(spread, commission, slippage) live in BacktestConfig, per CLAUDE.md rule 4.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Symbol(StrEnum):
    """Instruments under study. String values are used in file paths and configs."""

    EURUSD = "EURUSD"
    XAUUSD = "XAUUSD"

    @property
    def spec(self) -> SymbolSpec:
        return _SPECS[self]

    @property
    def pip_size(self) -> float:
        return self.spec.pip_size

    @property
    def price_precision(self) -> int:
        return self.spec.price_precision

    @property
    def dukascopy_code(self) -> str:
        return self.spec.dukascopy_code

    @classmethod
    def from_string(cls, raw: str) -> Symbol:
        key = raw.strip().upper()
        try:
            return cls(key)
        except ValueError as exc:
            valid = ", ".join(s.value for s in cls)
            raise ValueError(f"unknown symbol {raw!r}; expected one of: {valid}") from exc


@dataclass(frozen=True)
class SymbolSpec:
    """Quoting metadata for an instrument.

    ``point_value`` is the price increment of the last decimal place; ``pip_size``
    is the conventional pip (10 points on a 5-decimal FX quote). ``asset_class``
    drives session/holiday handling in Phase 2 — metals and FX do not keep
    identical trading calendars.
    """

    symbol: Symbol
    asset_class: str
    price_precision: int
    point_value: float
    pip_size: float
    dukascopy_code: str


_SPECS: dict[Symbol, SymbolSpec] = {
    Symbol.EURUSD: SymbolSpec(
        symbol=Symbol.EURUSD,
        asset_class="fx",
        price_precision=5,
        point_value=0.00001,
        pip_size=0.0001,
        dukascopy_code="EURUSD",
    ),
    Symbol.XAUUSD: SymbolSpec(
        symbol=Symbol.XAUUSD,
        asset_class="metal",
        price_precision=3,
        point_value=0.001,
        pip_size=0.01,
        dukascopy_code="XAUUSD",
    ),
}
