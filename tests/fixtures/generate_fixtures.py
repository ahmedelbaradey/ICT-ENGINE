"""Regenerate the checked-in market-data CSV fixtures.

Run: ``python tests/fixtures/generate_fixtures.py``

The fixtures are deterministic (fixed seed) and checked in, so the default CI gate
needs no network and every run sees byte-identical inputs. Regenerate only when the
fixture schema itself changes — changing the data invalidates the hand-computed
expectations in the tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent / "market_data"

# A full trading day of 5-minute bars: 2024-03-04 00:00 UTC .. 2024-03-05 00:00 UTC.
# 288 bars, exactly divisible by 3 (15m), 12 (1h) and 48 (4h), so every resample
# target in the MVP tiles the window exactly with no partial bars.
START = datetime(2024, 3, 4, 0, 0, tzinfo=UTC)
BAR_COUNT = 288


def build_series(seed: int, base: float, tick: float, precision: int) -> list[dict]:
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    close = base
    for i in range(BAR_COUNT):
        ts = START + timedelta(minutes=5 * i)
        open_ = close
        drift = rng.normal(0.0, 12.0) * tick
        close = open_ + drift
        wick_hi = abs(rng.normal(0.0, 6.0)) * tick
        wick_lo = abs(rng.normal(0.0, 6.0)) * tick
        high = max(open_, close) + wick_hi
        low = min(open_, close) - wick_lo
        rows.append(
            {
                "timestamp": ts.isoformat().replace("+00:00", "Z"),
                "open": round(open_, precision),
                "high": round(high, precision),
                "low": round(low, precision),
                "close": round(close, precision),
                "volume": float(int(rng.integers(80, 400))),
            }
        )
    return rows


def enforce_invariants(rows: list[dict], precision: int) -> list[dict]:
    """Rounding can push high/low inside the body — repair before writing.

    Fixtures must be VALID data; invalid-bar handling is tested with purpose-built
    frames in the test suite, not with accidentally-broken fixtures.
    """
    for row in rows:
        body_hi = max(row["open"], row["close"])
        body_lo = min(row["open"], row["close"])
        row["high"] = round(max(row["high"], body_hi), precision)
        row["low"] = round(min(row["low"], body_lo), precision)
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "timestamp,open,high,low,close,volume"
    lines = [header]
    lines.extend(
        f"{r['timestamp']},{r['open']},{r['high']},{r['low']},{r['close']},{r['volume']}" for r in rows
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {path} ({len(rows)} rows)")


def main() -> None:
    eurusd = enforce_invariants(build_series(seed=20240304, base=1.0850, tick=0.00001, precision=5), 5)
    write_csv(ROOT / "EURUSD" / "5m.csv", eurusd)

    xauusd = enforce_invariants(build_series(seed=20240305, base=2082.500, tick=0.001, precision=3), 3)
    write_csv(ROOT / "XAUUSD" / "5m.csv", xauusd)


if __name__ == "__main__":
    main()
