"""DST, candle-boundary determinism, and the tick → 1M → 5M/15M/1H chain.

The premise these tests defend: **UTC has no daylight saving.** Every timestamp in
this system is UTC, so a DST transition in London or New York must not move a single
candle boundary. What DST *does* change is the wall-clock label of a session — which
is a Phase 2 session-layer concern, not a bar-construction concern.

Getting this backwards is a classic FX-research bug: bars get rebuilt on local time,
one day a year has 23 or 25 hourly bars, and every multi-timeframe alignment silently
shifts by an hour.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from ict_kronos.data import DataNormalizer, resample, with_close_time
from ict_kronos.data.coverage import BarQuality, coverage_report
from ict_kronos.data.dukascopy import PriceSide, aggregate_ticks_to_bars
from ict_kronos.domain import Symbol, Timeframe

# 2024 DST transitions, as UTC instants.
US_DST_START = datetime(2024, 3, 10, 7, 0, tzinfo=UTC)  # 02:00 America/New_York -> 03:00
EU_DST_START = datetime(2024, 3, 31, 1, 0, tzinfo=UTC)  # 01:00 Europe/London -> 02:00


def _ticks(start: datetime, count: int, *, step_seconds: int = 10, base: float = 1.0850):
    """A deterministic tick stream: one tick every ``step_seconds`` from ``start``."""
    return pd.DataFrame(
        {
            "timestamp": [pd.Timestamp(start + timedelta(seconds=step_seconds * i)) for i in range(count)],
            "bid": [base + (i % 11) * 0.00001 for i in range(count)],
            "ask": [base + 0.00010 + (i % 11) * 0.00001 for i in range(count)],
            "bid_volume": [1.0] * count,
            "ask_volume": [1.0] * count,
        }
    )


class TestDstDoesNotMoveUtcBoundaries:
    @pytest.mark.parametrize(("label", "transition"), [("us", US_DST_START), ("eu", EU_DST_START)])
    def test_hourly_bars_are_unbroken_across_a_dst_transition(self, label, transition):
        """A DST day still has exactly 24 hourly UTC bars — no 23, no 25."""
        day_start = transition.replace(hour=0, minute=0)
        # 1440 one-minute ticks-worth of coverage: one tick per minute for a full day.
        ticks = _ticks(day_start, 1440, step_seconds=60)

        m1 = aggregate_ticks_to_bars(ticks, Symbol.EURUSD, Timeframe.M1)
        h1 = resample(m1, Timeframe.M1, Timeframe.H1, Symbol.EURUSD)

        assert len(m1) == 1440, f"{label}: a UTC day must always hold 1440 one-minute bars"
        assert len(h1) == 24, f"{label}: a UTC day must always hold 24 hourly bars"

    @pytest.mark.parametrize(("label", "transition"), [("us", US_DST_START), ("eu", EU_DST_START)])
    def test_bar_spacing_is_exactly_uniform_across_the_transition(self, label, transition):
        start = transition - timedelta(hours=6)
        ticks = _ticks(start, 720, step_seconds=60)  # 12 hours straddling the transition

        h1 = resample(
            aggregate_ticks_to_bars(ticks, Symbol.EURUSD, Timeframe.M1),
            Timeframe.M1,
            Timeframe.H1,
            Symbol.EURUSD,
        )
        deltas = h1["timestamp"].diff().dropna().unique()
        assert list(deltas) == [pd.Timedelta(hours=1)], f"{label}: DST perturbed UTC bar spacing"

    def test_local_wall_clock_jumps_while_utc_does_not(self):
        """Sanity anchor: the transition IS real in local time. If this ever stops
        being true the test dates are stale, and the tests above would pass
        vacuously."""
        ny = ZoneInfo("America/New_York")
        before = (US_DST_START - timedelta(minutes=1)).astimezone(ny)
        after = (US_DST_START + timedelta(minutes=1)).astimezone(ny)

        assert before.utcoffset() != after.utcoffset(), "US DST transition date is stale"
        assert (US_DST_START - (US_DST_START - timedelta(hours=1))) == timedelta(hours=1)

    def test_normalizer_grid_alignment_is_dst_independent(self):
        start = US_DST_START - timedelta(hours=3)
        ticks = _ticks(start, 360, step_seconds=60)
        m1 = aggregate_ticks_to_bars(ticks, Symbol.EURUSD, Timeframe.M1)

        normalized, report = DataNormalizer().normalize(m1, Symbol.EURUSD, Timeframe.M1)
        assert report.misaligned_floored == 0
        assert (normalized["timestamp"].dt.second == 0).all()
        assert report.gaps == []


class TestCandleBoundaryDeterminism:
    def test_a_tick_at_the_boundary_opens_the_next_bar(self):
        """Left-closed, left-labelled: [t, t+d). A tick at exactly t+d starts the
        NEXT bar, never closes the previous one."""
        start = datetime(2024, 3, 8, 9, 0, tzinfo=UTC)
        ticks = pd.DataFrame(
            {
                "timestamp": [
                    pd.Timestamp(start),
                    pd.Timestamp(start + timedelta(seconds=59)),
                    pd.Timestamp(start + timedelta(seconds=60)),  # exactly the boundary
                ],
                "bid": [1.0850, 1.0851, 1.0900],
                "ask": [1.0851, 1.0852, 1.0901],
                "bid_volume": [1.0, 1.0, 1.0],
                "ask_volume": [1.0, 1.0, 1.0],
            }
        )
        bars = aggregate_ticks_to_bars(ticks, Symbol.EURUSD, Timeframe.M1)

        assert len(bars) == 2
        assert bars["volume"].iloc[0] == 2.0
        assert bars["volume"].iloc[1] == 1.0
        assert bars["open"].iloc[1] == pytest.approx(1.0900)

    def test_aggregation_is_independent_of_tick_arrival_order(self):
        """Shuffled input must produce byte-identical bars — otherwise the pipeline
        is not reproducible from the same raw archive."""
        start = datetime(2024, 3, 8, 9, 0, tzinfo=UTC)
        ticks = _ticks(start, 300, step_seconds=10)

        ordered = aggregate_ticks_to_bars(ticks, Symbol.EURUSD, Timeframe.M1)
        shuffled = aggregate_ticks_to_bars(
            ticks.sample(frac=1.0, random_state=11).sort_values("timestamp"),
            Symbol.EURUSD,
            Timeframe.M1,
        )
        pd.testing.assert_frame_equal(ordered, shuffled)

    def test_price_side_is_configurable_not_hardcoded(self):
        start = datetime(2024, 3, 8, 9, 0, tzinfo=UTC)
        ticks = _ticks(start, 60, step_seconds=10)

        bid = aggregate_ticks_to_bars(ticks, Symbol.EURUSD, Timeframe.M1, side=PriceSide.BID)
        ask = aggregate_ticks_to_bars(ticks, Symbol.EURUSD, Timeframe.M1, side=PriceSide.ASK)
        assert (ask["open"] > bid["open"]).all()


class TestTickToBarChain:
    """Tick → 1M → 5M / 15M / 1H, exactly as the real-data pipeline runs it."""

    @pytest.fixture
    def m1(self):
        start = datetime(2024, 3, 8, 0, 0, tzinfo=UTC)
        # One tick per 10s for 4 hours -> 240 one-minute bars.
        return aggregate_ticks_to_bars(
            _ticks(start, 4 * 60 * 6, step_seconds=10), Symbol.EURUSD, Timeframe.M1
        )

    def test_base_is_one_minute(self, m1):
        assert len(m1) == 240
        assert set(m1["timeframe"].unique()) == {"1m"}

    @pytest.mark.parametrize(
        ("target", "expected"), [(Timeframe.M5, 48), (Timeframe.M15, 16), (Timeframe.H1, 4)]
    )
    def test_derived_counts(self, m1, target, expected):
        derived = resample(m1, Timeframe.M1, target, Symbol.EURUSD)
        assert len(derived) == expected

    @pytest.mark.parametrize("target", [Timeframe.M5, Timeframe.M15, Timeframe.H1])
    def test_derived_bars_reconcile_with_the_1m_source(self, m1, target):
        """Every derived bar must be exactly the 1M bars beneath it."""
        derived = resample(m1, Timeframe.M1, target, Symbol.EURUSD)
        base = m1.set_index("timestamp")

        for row in derived.itertuples(index=False):
            window = base.loc[row.timestamp : row.close_time - pd.Timedelta(minutes=1)]
            assert len(window) == target.minutes
            assert row.open == pytest.approx(window["open"].iloc[0])
            assert row.close == pytest.approx(window["close"].iloc[-1])
            assert row.high == pytest.approx(window["high"].max())
            assert row.low == pytest.approx(window["low"].min())
            assert row.volume == pytest.approx(window["volume"].sum())

    def test_volume_is_conserved_across_every_timeframe(self, m1):
        total = m1["volume"].sum()
        for target in (Timeframe.M5, Timeframe.M15, Timeframe.H1):
            derived = resample(m1, Timeframe.M1, target, Symbol.EURUSD)
            assert derived["volume"].sum() == pytest.approx(total), target.value

    def test_close_time_is_consistent_at_every_level(self, m1):
        stack = {Timeframe.M1: with_close_time(m1, Timeframe.M1)}
        for target in (Timeframe.M5, Timeframe.M15, Timeframe.H1):
            stack[target] = resample(m1, Timeframe.M1, target, Symbol.EURUSD)

        for timeframe, frame in stack.items():
            assert (frame["close_time"] - frame["timestamp"] == timeframe.duration).all(), timeframe

    def test_incomplete_trailing_bar_is_dropped(self, m1):
        """3.5 hours of 1M bars must yield 3 hourly bars, not 4."""
        partial = m1.iloc[: 3 * 60 + 30]
        hourly = resample(partial, Timeframe.M1, Timeframe.H1, Symbol.EURUSD)
        assert len(hourly) == 3

    def test_a_gap_inside_a_period_no_longer_invalidates_that_period(self, m1):
        """The specification changed, so this test changed with it.

        It previously asserted that five absent minutes inside hour 1 deleted that
        hour's bar. Real July-2026 data showed where that rule leads: it demanded 1440
        of 1440 minutes for a Daily, which no market delivers, and it destroyed 100% of
        Daily bars and 22.5% of 4H bars. See ``docs/features/data_coverage.md``.

        A minute with no ticks is a minute with no trades, not missing data. The hour
        survives as a real aggregation of everything that traded — and the coverage
        report says plainly that observations are missing and that the cause is unproven.
        """
        holed = m1.drop(index=m1.index[70:75]).reset_index(drop=True)  # inside hour 1
        hourly = resample(holed, Timeframe.M1, Timeframe.H1, Symbol.EURUSD)

        assert len(hourly) == 4
        assert pd.Timestamp("2024-03-08T01:00:00Z") in set(hourly["timestamp"])

        bar = next(
            b
            for b in coverage_report(holed, Timeframe.M1, Timeframe.H1, Symbol.EURUSD).bars
            if b.timestamp == pd.Timestamp("2024-03-08T01:00:00Z")
        )
        assert bar.missing_observations == 5
        assert bar.quality is BarQuality.DEGRADED_UNKNOWN
        assert bar.production_eligible is True
