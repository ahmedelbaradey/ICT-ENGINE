"""Resampler: aggregation correctness and bar-boundary conventions.

Leakage-specific tests live in ``test_leakage.py``. This file pins the mechanical
correctness the leakage guarantees are built on — if bar boundaries are wrong, the
point-in-time logic is reasoning about the wrong instants.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from ict_kronos.data import (
    RESAMPLED_COLUMNS,
    ResampleError,
    build_timeframe_stack,
    resample,
    with_close_time,
)
from ict_kronos.domain import Symbol, Timeframe

from .conftest import FIXTURE_START, make_frame


class TestResampleGuards:
    def test_refuses_downward_aggregation(self):
        """Bars cannot be disaggregated — a 1H bar contains no information about the
        four 15M bars inside it, so inventing them would be pure fabrication."""
        with pytest.raises(ResampleError, match="exact multiple"):
            resample(make_frame(10, timeframe=Timeframe.H1), Timeframe.H1, Timeframe.M15, Symbol.EURUSD)

    def test_refuses_same_timeframe(self):
        with pytest.raises(ResampleError):
            resample(make_frame(10), Timeframe.M5, Timeframe.M5, Symbol.EURUSD)

    def test_every_mvp_timeframe_pair_is_divisible(self):
        """Every ordered pair in the MVP ladder aggregates exactly. The guard in
        resample() exists for future timeframes (30m, 2h, weekly) that would not."""
        ladder = [Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1]
        for i, source in enumerate(ladder):
            for target in ladder[i + 1 :]:
                assert target.can_aggregate_from(source), f"{source.value} -> {target.value}"

    def test_empty_input_returns_empty_with_schema(self):
        out = resample(make_frame(0), Timeframe.M5, Timeframe.M15, Symbol.EURUSD)
        assert len(out) == 0
        assert list(out.columns) == list(RESAMPLED_COLUMNS)


class TestResampleAggregation:
    def test_ohlcv_aggregation_is_first_max_min_last_sum(self):
        # 3 x 5m -> 1 x 15m. Prices rise monotonically, so the expectations are
        # checkable by hand rather than by reimplementing the aggregation.
        frame = make_frame(3, timeframe=Timeframe.M5, base_price=1.1000, price_step=0.0010)
        out = resample(frame, Timeframe.M5, Timeframe.M15, Symbol.EURUSD)

        assert len(out) == 1
        row = out.iloc[0]
        assert row["open"] == pytest.approx(frame["open"].iloc[0])
        assert row["close"] == pytest.approx(frame["close"].iloc[-1])
        assert row["high"] == pytest.approx(frame["high"].max())
        assert row["low"] == pytest.approx(frame["low"].min())
        assert row["volume"] == pytest.approx(frame["volume"].sum())

    def test_bar_is_labelled_by_open_time(self):
        frame = make_frame(12, timeframe=Timeframe.M5)
        out = resample(frame, Timeframe.M5, Timeframe.H1, Symbol.EURUSD)

        assert len(out) == 1
        assert out["timestamp"].iloc[0] == pd.Timestamp(FIXTURE_START)

    def test_close_time_is_open_plus_target_duration(self):
        frame = make_frame(24, timeframe=Timeframe.M5)
        out = resample(frame, Timeframe.M5, Timeframe.H1, Symbol.EURUSD)

        assert (out["close_time"] - out["timestamp"] == timedelta(hours=1)).all()

    def test_timeframe_column_reflects_the_target(self):
        out = resample(make_frame(12), Timeframe.M5, Timeframe.H1, Symbol.EURUSD)
        assert set(out["timeframe"].unique()) == {"1h"}
        assert set(out["symbol"].unique()) == {"EURUSD"}

    def test_multiple_target_bars(self):
        frame = make_frame(36, timeframe=Timeframe.M5)  # 3 hours
        out = resample(frame, Timeframe.M5, Timeframe.H1, Symbol.EURUSD)

        assert len(out) == 3
        assert list(out["timestamp"]) == [
            pd.Timestamp(FIXTURE_START),
            pd.Timestamp(FIXTURE_START + timedelta(hours=1)),
            pd.Timestamp(FIXTURE_START + timedelta(hours=2)),
        ]

    def test_output_satisfies_ohlc_invariants(self):
        frame = make_frame(60, timeframe=Timeframe.M5)
        out = resample(frame, Timeframe.M5, Timeframe.M15, Symbol.EURUSD)
        assert (out["high"] >= out[["open", "close"]].max(axis=1)).all()
        assert (out["low"] <= out[["open", "close"]].min(axis=1)).all()


class TestIncompleteBars:
    def test_incomplete_target_bar_is_dropped_by_default(self):
        """A 1H bar built from 5 of its 12 source bars has a real open but a
        meaningless high/low/close. Treating it as finished is a fabricated bar."""
        frame = make_frame(17, timeframe=Timeframe.M5)  # 12 + 5
        out = resample(frame, Timeframe.M5, Timeframe.H1, Symbol.EURUSD)

        assert len(out) == 1
        assert out["timestamp"].iloc[0] == pd.Timestamp(FIXTURE_START)

    def test_incomplete_bar_can_be_kept_explicitly(self):
        frame = make_frame(17, timeframe=Timeframe.M5)
        out = resample(frame, Timeframe.M5, Timeframe.H1, Symbol.EURUSD, require_complete=False)
        assert len(out) == 2

    def test_gap_inside_a_period_makes_that_bar_incomplete(self):
        frame = make_frame(12, timeframe=Timeframe.M5).drop(index=[4, 5]).reset_index(drop=True)
        out = resample(frame, Timeframe.M5, Timeframe.H1, Symbol.EURUSD)
        assert len(out) == 0


class TestWithCloseTime:
    def test_attaches_close_time_to_base_frame(self):
        frame = make_frame(5, timeframe=Timeframe.M15)
        out = with_close_time(frame, Timeframe.M15)

        assert list(out.columns) == list(RESAMPLED_COLUMNS)
        assert (out["close_time"] - out["timestamp"] == timedelta(minutes=15)).all()

    def test_does_not_mutate_input(self):
        frame = make_frame(5)
        before = frame.copy(deep=True)
        with_close_time(frame, Timeframe.M5)
        pd.testing.assert_frame_equal(frame, before)


class TestTimeframeStack:
    def test_builds_every_requested_timeframe(self):
        frame = make_frame(288, timeframe=Timeframe.M5)  # one full day
        stack = build_timeframe_stack(
            frame, Timeframe.M5, (Timeframe.M15, Timeframe.H1, Timeframe.H4), Symbol.EURUSD
        )

        assert set(stack) == {Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4}
        assert len(stack[Timeframe.M5]) == 288
        assert len(stack[Timeframe.M15]) == 96
        assert len(stack[Timeframe.H1]) == 24
        assert len(stack[Timeframe.H4]) == 6

    def test_every_level_carries_close_time(self):
        stack = build_timeframe_stack(
            make_frame(288, timeframe=Timeframe.M5), Timeframe.M5, (Timeframe.H1,), Symbol.EURUSD
        )
        for frame in stack.values():
            assert "close_time" in frame.columns

    def test_derived_timeframes_are_mutually_consistent(self):
        """Deriving everything from one base guarantees a 1H bar IS the twelve 5M
        bars beneath it. Independently fetched timeframes routinely disagree, and
        that disagreement shows up downstream as phantom ICT structure."""
        base = make_frame(288, timeframe=Timeframe.M5)
        stack = build_timeframe_stack(base, Timeframe.M5, (Timeframe.H1,), Symbol.EURUSD)

        h1 = stack[Timeframe.H1]
        first_hour = base.iloc[:12]
        assert h1["open"].iloc[0] == pytest.approx(first_hour["open"].iloc[0])
        assert h1["close"].iloc[0] == pytest.approx(first_hour["close"].iloc[-1])
        assert h1["high"].iloc[0] == pytest.approx(first_hour["high"].max())
        assert h1["low"].iloc[0] == pytest.approx(first_hour["low"].min())

    def test_chained_resample_matches_direct_resample(self):
        """5M -> 1H directly must equal 5M -> 15M -> 1H. If these disagree, the
        aggregation is boundary-sensitive and every MTF feature is suspect."""
        base = make_frame(288, timeframe=Timeframe.M5)

        direct = resample(base, Timeframe.M5, Timeframe.H1, Symbol.EURUSD)
        m15 = resample(base, Timeframe.M5, Timeframe.M15, Symbol.EURUSD)
        chained = resample(m15.drop(columns=["close_time"]), Timeframe.M15, Timeframe.H1, Symbol.EURUSD)

        pd.testing.assert_frame_equal(
            direct.reset_index(drop=True), chained.reset_index(drop=True), check_dtype=False
        )
