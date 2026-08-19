"""Tick-integrity validation: malformed records are quarantined, never repaired."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from ict_kronos.data.tick_quality import TickQualityReport, validate_ticks

BASE = datetime(2024, 3, 8, 9, 0, tzinfo=UTC)


def ticks(rows: list[tuple[int, float, float]], volumes: list[tuple[float, float]] | None = None):
    """Build a tick frame from ``(second_offset, bid, ask)`` triples."""
    volumes = volumes or [(1.0, 1.0)] * len(rows)
    return pd.DataFrame(
        {
            "timestamp": [pd.Timestamp(BASE + timedelta(seconds=s)) for s, _, _ in rows],
            "bid": [b for _, b, _ in rows],
            "ask": [a for _, _, a in rows],
            "bid_volume": [bv for bv, _ in volumes],
            "ask_volume": [av for _, av in volumes],
        }
    )


class TestCleanInput:
    def test_valid_ticks_pass_through(self):
        clean, report = validate_ticks(ticks([(0, 1.0850, 1.0851), (1, 1.0851, 1.0852)]))
        assert len(clean) == 2
        assert report.rejected == 0
        assert report.reasons == {}

    def test_empty_input(self):
        empty = ticks([])
        clean, report = validate_ticks(empty)
        assert len(clean) == 0
        assert report.input_ticks == 0

    def test_input_is_never_mutated(self):
        original = ticks([(0, 1.0850, 1.0851), (1, 0.0, 1.0852)])
        before = original.copy(deep=True)
        validate_ticks(original)
        pd.testing.assert_frame_equal(original, before)

    def test_max_spread_is_recorded(self):
        _, report = validate_ticks(ticks([(0, 1.0850, 1.0851), (1, 1.0800, 1.0900)]))
        assert report.max_spread == pytest.approx(0.0100)


class TestPriceIntegrity:
    def test_zero_price_is_rejected(self):
        clean, report = validate_ticks(ticks([(0, 1.0850, 1.0851), (1, 0.0, 1.0852)]))
        assert len(clean) == 1
        assert report.non_positive_price == 1
        assert report.reasons["non_positive_price"] == 1

    def test_negative_price_is_rejected(self):
        clean, report = validate_ticks(ticks([(0, -1.0, 1.0851)]))
        assert len(clean) == 0
        assert report.non_positive_price == 1

    def test_nan_price_is_rejected(self):
        clean, report = validate_ticks(ticks([(0, float("nan"), 1.0851), (1, 1.0850, 1.0851)]))
        assert len(clean) == 1
        assert report.nan_price == 1

    def test_crossed_book_is_rejected(self):
        """bid > ask is physically impossible and reliably signals a corrupt record."""
        clean, report = validate_ticks(ticks([(0, 1.0900, 1.0850), (1, 1.0850, 1.0851)]))
        assert len(clean) == 1
        assert report.crossed_book == 1

    def test_equal_bid_ask_is_allowed(self):
        """A zero spread is unusual but not impossible, and rejecting it would throw
        away legitimate data."""
        clean, report = validate_ticks(ticks([(0, 1.0850, 1.0850)]))
        assert len(clean) == 1
        assert report.crossed_book == 0

    def test_negative_volume_is_rejected(self):
        clean, report = validate_ticks(
            ticks([(0, 1.0850, 1.0851), (1, 1.0851, 1.0852)], volumes=[(1.0, 1.0), (-1.0, 1.0)])
        )
        assert len(clean) == 1
        assert report.negative_volume == 1

    def test_bad_ticks_are_dropped_not_repaired(self):
        """Repairing a tick means inventing a price that never traded."""
        clean, _ = validate_ticks(ticks([(0, 0.0, 1.0851), (1, 1.0850, 1.0851)]))
        assert len(clean) == 1
        assert (clean["bid"] > 0).all()


class TestOrderingAndDuplicates:
    def test_out_of_order_is_counted_and_sorted(self):
        frame = ticks([(10, 1.0850, 1.0851), (0, 1.0840, 1.0841), (20, 1.0860, 1.0861)])
        clean, report = validate_ticks(frame)

        assert report.out_of_order == 1
        assert clean["timestamp"].is_monotonic_increasing

    def test_identical_duplicate_is_removed(self):
        """The same quote delivered twice is redundancy, not two trades — counting it
        twice would inflate tick-count volume."""
        clean, report = validate_ticks(ticks([(0, 1.0850, 1.0851), (0, 1.0850, 1.0851)]))
        assert len(clean) == 1
        assert report.duplicate_ticks == 1

    def test_same_timestamp_different_price_is_kept(self):
        """Two genuinely different quotes inside the same millisecond are real."""
        clean, report = validate_ticks(ticks([(0, 1.0850, 1.0851), (0, 1.0852, 1.0853)]))
        assert len(clean) == 2
        assert report.duplicate_ticks == 0


class TestReportAggregation:
    def test_merge_sums_counters_and_keeps_max_spread(self):
        a = TickQualityReport(input_ticks=10, output_ticks=9, non_positive_price=1, max_spread=0.001)
        b = TickQualityReport(input_ticks=5, output_ticks=5, duplicate_ticks=2, max_spread=0.004)
        a.merge(b)

        assert a.input_ticks == 15
        assert a.output_ticks == 14
        assert a.non_positive_price == 1
        assert a.duplicate_ticks == 2
        assert a.max_spread == pytest.approx(0.004)

    def test_merge_combines_reason_maps(self):
        a = TickQualityReport(reasons={"nan_price": 2})
        b = TickQualityReport(reasons={"nan_price": 3, "crossed_book": 1})
        a.merge(b)
        assert a.reasons == {"nan_price": 5, "crossed_book": 1}

    def test_as_dict_is_serialisable(self):
        _, report = validate_ticks(ticks([(0, 0.0, 1.0851), (1, 1.0850, 1.0851)]))
        payload = report.as_dict()
        assert payload["rejected_ticks"] == 1
        assert payload["non_positive_price"] == 1
        assert isinstance(payload["reasons"], dict)
