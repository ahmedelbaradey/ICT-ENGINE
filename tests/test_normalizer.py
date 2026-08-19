"""DataNormalizer: dedup, sort, grid alignment, invariant quarantine, gap detection."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from ict_kronos.data import DataNormalizer
from ict_kronos.domain import CANDLE_COLUMNS, Symbol, Timeframe

from .conftest import FIXTURE_START, make_frame


@pytest.fixture
def normalizer() -> DataNormalizer:
    return DataNormalizer(max_gap_bars=3)


class TestNormalizeHappyPath:
    def test_clean_frame_passes_through_unchanged(self, normalizer):
        frame = make_frame(20)
        out, report = normalizer.normalize(frame, Symbol.EURUSD, Timeframe.M5)

        assert len(out) == 20
        assert list(out.columns) == list(CANDLE_COLUMNS)
        assert report.duplicates_removed == 0
        assert report.invalid_removed == 0
        assert report.misaligned_floored == 0
        assert report.gaps == []

    def test_input_frame_is_never_mutated(self, normalizer):
        """Raw data is immutable (CLAUDE.md rule 7) — including in memory."""
        frame = make_frame(10)
        before = frame.copy(deep=True)
        normalizer.normalize(frame, Symbol.EURUSD, Timeframe.M5)
        pd.testing.assert_frame_equal(frame, before)

    def test_empty_input_returns_empty_canonical_frame(self, normalizer):
        out, report = normalizer.normalize(make_frame(0), Symbol.EURUSD, Timeframe.M5)
        assert len(out) == 0
        assert list(out.columns) == list(CANDLE_COLUMNS)
        assert report.input_rows == 0
        assert report.output_rows == 0

    def test_report_records_the_window(self, normalizer):
        out, report = normalizer.normalize(make_frame(12), Symbol.EURUSD, Timeframe.M5)
        assert report.first_timestamp == out["timestamp"].iloc[0].to_pydatetime()
        assert report.last_timestamp == out["timestamp"].iloc[-1].to_pydatetime()

    def test_symbol_and_timeframe_are_taken_from_the_caller(self, normalizer):
        """A provider must not be able to mislabel what it was asked for."""
        frame = make_frame(5)
        frame["symbol"] = "WRONG"
        frame["timeframe"] = "99h"
        out, _ = normalizer.normalize(frame, Symbol.XAUUSD, Timeframe.H1)
        assert set(out["symbol"].unique()) == {"XAUUSD"}
        assert set(out["timeframe"].unique()) == {"1h"}


class TestOrdering:
    def test_shuffled_input_is_sorted(self, normalizer):
        frame = make_frame(10).sample(frac=1.0, random_state=7).reset_index(drop=True)
        out, report = normalizer.normalize(frame, Symbol.EURUSD, Timeframe.M5)

        assert out["timestamp"].is_monotonic_increasing
        assert report.out_of_order_rows > 0

    def test_output_is_always_strictly_increasing(self, normalizer):
        frame = make_frame(30).sample(frac=1.0, random_state=3).reset_index(drop=True)
        out, _ = normalizer.normalize(frame, Symbol.EURUSD, Timeframe.M5)
        assert (out["timestamp"].diff().dropna() > pd.Timedelta(0)).all()


class TestDeduplication:
    def test_duplicates_are_removed(self, normalizer):
        frame = make_frame(5)
        doubled = pd.concat([frame, frame], ignore_index=True)
        out, report = normalizer.normalize(doubled, Symbol.EURUSD, Timeframe.M5)

        assert len(out) == 5
        assert report.duplicates_removed == 5

    def test_last_occurrence_wins(self, normalizer):
        """A re-fetch of a bar is treated as a correction of the earlier value."""
        frame = make_frame(3)
        revised = frame.iloc[[1]].copy()
        revised["close"] = 9.9999
        revised["high"] = 9.9999
        combined = pd.concat([frame, revised], ignore_index=True)

        out, report = normalizer.normalize(combined, Symbol.EURUSD, Timeframe.M5)
        assert report.duplicates_removed == 1
        assert out.loc[out["timestamp"] == frame["timestamp"].iloc[1], "close"].iloc[0] == 9.9999


class TestGridAlignment:
    def test_off_grid_timestamps_are_floored(self, normalizer):
        frame = make_frame(6, timeframe=Timeframe.M5)
        frame["timestamp"] = frame["timestamp"] + pd.Timedelta(seconds=37)

        out, report = normalizer.normalize(frame, Symbol.EURUSD, Timeframe.M5)
        assert report.misaligned_floored == 6
        assert (out["timestamp"].dt.second == 0).all()
        assert (out["timestamp"].dt.minute % 5 == 0).all()

    def test_hourly_grid_alignment(self, normalizer):
        frame = make_frame(4, timeframe=Timeframe.H1)
        frame["timestamp"] = frame["timestamp"] + pd.Timedelta(minutes=13)

        out, report = normalizer.normalize(frame, Symbol.EURUSD, Timeframe.H1)
        assert report.misaligned_floored == 4
        assert (out["timestamp"].dt.minute == 0).all()


class TestInvariantQuarantine:
    def test_invalid_bars_are_dropped_not_repaired(self, normalizer):
        """Repairing a bar means inventing a price that never traded."""
        frame = make_frame(5)
        frame.loc[2, "high"] = frame.loc[2, "low"] - 1.0

        out, report = normalizer.normalize(frame, Symbol.EURUSD, Timeframe.M5)
        assert len(out) == 4
        assert report.invalid_removed == 1

    def test_nan_prices_are_quarantined(self, normalizer):
        frame = make_frame(5)
        frame.loc[3, "open"] = float("nan")

        out, report = normalizer.normalize(frame, Symbol.EURUSD, Timeframe.M5)
        assert report.invalid_removed == 1
        assert len(out) == 4

    def test_negative_volume_is_quarantined(self, normalizer):
        frame = make_frame(4)
        frame.loc[1, "volume"] = -5.0

        out, report = normalizer.normalize(frame, Symbol.EURUSD, Timeframe.M5)
        assert report.invalid_removed == 1
        assert len(out) == 3


class TestGapDetection:
    def test_no_gaps_in_contiguous_data(self, normalizer):
        out, report = normalizer.normalize(make_frame(20), Symbol.EURUSD, Timeframe.M5)
        assert report.gaps == []
        assert report.missing_bars == 0

    def test_single_missing_bar_is_detected(self, normalizer):
        frame = make_frame(10)
        frame = frame.drop(index=4).reset_index(drop=True)

        _, report = normalizer.normalize(frame, Symbol.EURUSD, Timeframe.M5)
        assert len(report.gaps) == 1
        gap = report.gaps[0]
        assert gap.bar_count == 1
        assert gap.start == (FIXTURE_START + timedelta(minutes=20))

    def test_multi_bar_gap_counts_correctly(self, normalizer):
        frame = make_frame(20).drop(index=[5, 6, 7, 8]).reset_index(drop=True)

        _, report = normalizer.normalize(frame, Symbol.EURUSD, Timeframe.M5)
        assert len(report.gaps) == 1
        assert report.gaps[0].bar_count == 4
        assert report.missing_bars == 4

    def test_gaps_are_recorded_never_filled(self, normalizer):
        """Forward-filling would fabricate price action every ICT detector would
        read as real structure."""
        frame = make_frame(10).drop(index=[3, 4]).reset_index(drop=True)

        out, report = normalizer.normalize(frame, Symbol.EURUSD, Timeframe.M5)
        assert len(out) == 8  # the missing bars stay missing
        assert report.missing_bars == 2

    def test_significant_gaps_use_the_configured_threshold(self):
        frame = make_frame(30).drop(index=[10, 11]).reset_index(drop=True)

        lenient = DataNormalizer(max_gap_bars=5)
        _, lenient_report = lenient.normalize(frame, Symbol.EURUSD, Timeframe.M5)
        assert lenient_report.significant_gaps == []

        strict = DataNormalizer(max_gap_bars=1)
        _, strict_report = strict.normalize(frame, Symbol.EURUSD, Timeframe.M5)
        assert len(strict_report.significant_gaps) == 1

    def test_multiple_separate_gaps(self, normalizer):
        frame = make_frame(30).drop(index=[5, 15, 16]).reset_index(drop=True)

        _, report = normalizer.normalize(frame, Symbol.EURUSD, Timeframe.M5)
        assert len(report.gaps) == 2
        assert sorted(g.bar_count for g in report.gaps) == [1, 2]


class TestReportSerialization:
    def test_report_is_json_serialisable_shape(self, normalizer):
        frame = make_frame(20).drop(index=[7, 8, 9, 10, 11]).reset_index(drop=True)
        _, report = normalizer.normalize(frame, Symbol.EURUSD, Timeframe.M5)

        payload = report.as_dict()
        assert payload["symbol"] == "EURUSD"
        assert payload["timeframe"] == "5m"
        assert payload["missing_bars"] == 5
        assert payload["significant_gap_count"] == 1
        assert isinstance(payload["significant_gaps"][0]["start"], str)
        assert payload["first_timestamp"].endswith("+00:00")
