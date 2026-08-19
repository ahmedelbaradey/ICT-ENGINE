"""Look-ahead leakage tests — CLAUDE.md rule 1, Master Plan §19.

These are the most important tests in Phase 1. A leak here does not produce a
failing test downstream; it produces *excellent* backtest results that are entirely
fictitious. Every one of these tests encodes a specific way that has been known to
happen in practice.

The canonical failure this suite exists to prevent:

    A 4H bar timestamped 08:00 is not knowable until 12:00. Any join that attaches
    it to a 5M observation before 12:00 leaks up to four hours of the future into
    the feature vector.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from ict_kronos.data import (
    align_htf_context,
    build_timeframe_stack,
    latest_closed_bar,
    resample,
    with_close_time,
)
from ict_kronos.domain import Symbol, Timeframe

from .conftest import FIXTURE_START, make_frame

pytestmark = pytest.mark.leakage


# ------------------------------------------------------- observability anchor


class TestCloseTimeIsTheObservabilityAnchor:
    def test_a_bar_is_not_observable_before_its_close(self):
        frame = with_close_time(make_frame(4, timeframe=Timeframe.H1), Timeframe.H1)

        # The first bar opens at 00:00 and closes at 01:00.
        mid_bar = FIXTURE_START + timedelta(minutes=30)
        assert latest_closed_bar(frame, mid_bar) is None, "a bar that has not closed must never be returned"

    def test_a_bar_is_observable_exactly_at_its_close(self):
        frame = with_close_time(make_frame(4, timeframe=Timeframe.H1), Timeframe.H1)
        at_close = FIXTURE_START + timedelta(hours=1)

        bar = latest_closed_bar(frame, at_close)
        assert bar is not None
        assert bar["timestamp"] == pd.Timestamp(FIXTURE_START)

    def test_only_the_last_closed_bar_is_returned(self):
        frame = with_close_time(make_frame(6, timeframe=Timeframe.H1), Timeframe.H1)
        as_of = FIXTURE_START + timedelta(hours=3, minutes=17)

        bar = latest_closed_bar(frame, as_of)
        # Bars closing at 01:00, 02:00, 03:00 are observable; 04:00 is not.
        assert bar["timestamp"] == pd.Timestamp(FIXTURE_START + timedelta(hours=2))

    def test_no_bar_is_ever_returned_from_the_future(self):
        """Property test across the whole series: for every instant, the returned
        bar's close_time is <= that instant."""
        frame = with_close_time(make_frame(24, timeframe=Timeframe.H1), Timeframe.H1)

        for minutes in range(0, 24 * 60, 7):
            as_of = FIXTURE_START + timedelta(minutes=minutes)
            bar = latest_closed_bar(frame, as_of)
            if bar is not None:
                assert bar["close_time"] <= pd.Timestamp(as_of)

    def test_naive_as_of_is_rejected(self):
        frame = with_close_time(make_frame(4, timeframe=Timeframe.H1), Timeframe.H1)
        with pytest.raises(ValueError, match="timezone-aware"):
            latest_closed_bar(frame, datetime(2024, 3, 4, 5))  # noqa: DTZ001

    def test_frame_without_close_time_is_refused(self):
        """The only way to bypass the point-in-time rule would be to align on a
        frame that has no observability anchor. That is refused loudly."""
        with pytest.raises(ValueError, match="close_time"):
            latest_closed_bar(make_frame(4), FIXTURE_START)


# -------------------------------------------------- multi-timeframe alignment


class TestHtfAlignmentIsPointInTime:
    @pytest.fixture
    def stack(self):
        base = make_frame(288, timeframe=Timeframe.M5)  # a full day of 5M bars
        return build_timeframe_stack(base, Timeframe.M5, (Timeframe.H1, Timeframe.H4), Symbol.EURUSD)

    def test_htf_context_is_never_from_an_unclosed_bar(self, stack):
        """THE core leakage test. For every 5M observation, the attached 4H context
        must come from a 4H bar that had already closed."""
        base = stack[Timeframe.M5]
        h4 = stack[Timeframe.H4]

        merged = align_htf_context(base, h4, suffix="h4")

        for row in merged.itertuples(index=False):
            if pd.isna(row.close_h4):
                continue
            # Find which 4H bar supplied this context, and assert it had closed.
            source = h4.loc[h4["close"] == row.close_h4]
            assert not source.empty
            assert (source["close_time"] <= row.close_time).any(), (
                f"5M bar closing at {row.close_time} received 4H context from a bar "
                f"that had not yet closed — this is look-ahead leakage"
            )

    def test_first_bars_get_null_context_not_backfilled_context(self, stack):
        """Before the first 4H close, no 4H bar exists. Backfilling would invent
        context from the future."""
        merged = align_htf_context(stack[Timeframe.M5], stack[Timeframe.H4], suffix="h4")

        # 4H bar 1 opens 00:00, closes 04:00. 5M bars closing at or before 04:00
        # can only see it from 04:00 onward, so the first 47 must be null.
        early = merged.loc[merged["close_time"] < pd.Timestamp(FIXTURE_START + timedelta(hours=4))]
        assert len(early) == 47
        assert early["close_h4"].isna().all()

    def test_context_changes_exactly_at_the_htf_close(self, stack):
        merged = align_htf_context(stack[Timeframe.M5], stack[Timeframe.H1], suffix="h1")
        indexed = merged.set_index("close_time")

        first_h1_close = pd.Timestamp(FIXTURE_START + timedelta(hours=1))
        # The 5M bar closing at exactly 01:00 is the first that can see the 01:00 close.
        assert pd.isna(indexed.loc[first_h1_close - timedelta(minutes=5), "close_h1"])
        assert not pd.isna(indexed.loc[first_h1_close, "close_h1"])

    def test_naive_join_on_open_timestamp_would_leak(self, stack):
        """Demonstrates the bug this module exists to prevent, so the guarantee is
        not merely asserted but contrasted with the wrong answer."""
        base = stack[Timeframe.M5]
        h4 = stack[Timeframe.H4]

        correct = align_htf_context(base, h4, suffix="h4")

        # The classic mistake: merge_asof on the OPEN timestamp.
        naive = pd.merge_asof(
            base.sort_values("timestamp"),
            h4.sort_values("timestamp")
            .loc[:, ["timestamp", "close"]]
            .rename(columns={"close": "close_h4_naive"}),
            on="timestamp",
            direction="backward",
        )

        # At 00:05 the naive join already supplies the 00:00 4H bar's CLOSE — a
        # price that will not be known until 04:00.
        leaked = naive.loc[naive["timestamp"] == pd.Timestamp(FIXTURE_START + timedelta(minutes=5))]
        assert not pd.isna(leaked["close_h4_naive"].iloc[0]), "sanity: the naive join does leak"

        safe = correct.loc[correct["timestamp"] == pd.Timestamp(FIXTURE_START + timedelta(minutes=5))]
        assert pd.isna(safe["close_h4"].iloc[0]), "the correct join must NOT know the future"

    def test_alignment_requires_close_time_on_both_sides(self, stack):
        with pytest.raises(ValueError, match="close_time"):
            align_htf_context(make_frame(10), stack[Timeframe.H1], suffix="h1")
        with pytest.raises(ValueError, match="close_time"):
            align_htf_context(stack[Timeframe.M5], make_frame(10), suffix="h1")

    def test_empty_htf_yields_null_context_not_an_error(self, stack):
        empty_h4 = stack[Timeframe.H4].iloc[0:0]
        merged = align_htf_context(stack[Timeframe.M5], empty_h4, suffix="h4")
        assert merged["close_h4"].isna().all()


# ------------------------------------------------------------ streaming replay


class TestStreamingReplayEqualsBatchComputation:
    """The strongest available leakage guard.

    If a value computed from the full history differs from the same value computed
    by replaying bars one at a time, the batch computation used information that was
    not available at the time — by definition, a leak. This property will be applied
    to every ICT detector in Phase 2; it is established here for resampling.
    """

    def test_resample_prefix_matches_full_history_prefix(self):
        base = make_frame(288, timeframe=Timeframe.M5)
        full = resample(base, Timeframe.M5, Timeframe.H1, Symbol.EURUSD)

        # Replay: after each completed hour, recompute from only the bars seen so far.
        for hours in range(1, 25):
            seen = base.iloc[: hours * 12]
            streamed = resample(seen, Timeframe.M5, Timeframe.H1, Symbol.EURUSD)

            assert len(streamed) == hours
            pd.testing.assert_frame_equal(
                streamed.reset_index(drop=True),
                full.iloc[:hours].reset_index(drop=True),
                check_dtype=False,
            )

    def test_partial_hour_never_emits_a_bar(self):
        """Mid-period, the in-progress bar must not appear — its high/low/close are
        not yet determined."""
        base = make_frame(288, timeframe=Timeframe.M5)

        for bars_seen in range(1, 12):
            streamed = resample(base.iloc[:bars_seen], Timeframe.M5, Timeframe.H1, Symbol.EURUSD)
            assert len(streamed) == 0, f"an incomplete hour emitted a bar after {bars_seen} 5M bars"

    def test_htf_alignment_is_stable_under_replay(self):
        """Context attached to a past bar must never change when future data arrives.
        If it does, the earlier value depended on the future."""
        base = make_frame(288, timeframe=Timeframe.M5)

        full_stack = build_timeframe_stack(base, Timeframe.M5, (Timeframe.H1,), Symbol.EURUSD)
        full = align_htf_context(full_stack[Timeframe.M5], full_stack[Timeframe.H1], suffix="h1")

        for bars_seen in (24, 60, 144, 288):
            seen = base.iloc[:bars_seen]
            partial_stack = build_timeframe_stack(seen, Timeframe.M5, (Timeframe.H1,), Symbol.EURUSD)
            partial = align_htf_context(partial_stack[Timeframe.M5], partial_stack[Timeframe.H1], suffix="h1")

            pd.testing.assert_series_equal(
                partial["close_h1"].reset_index(drop=True),
                full["close_h1"].iloc[:bars_seen].reset_index(drop=True),
                check_dtype=False,
            )


# ------------------------------------------------------------------ timestamps


class TestTimestampAlignment:
    def test_all_stored_timestamps_are_utc(self):
        stack = build_timeframe_stack(
            make_frame(288, timeframe=Timeframe.M5),
            Timeframe.M5,
            (Timeframe.M15, Timeframe.H1, Timeframe.H4),
            Symbol.EURUSD,
        )
        for timeframe, frame in stack.items():
            assert str(frame["timestamp"].dtype.tz) == "UTC", timeframe
            assert str(frame["close_time"].dtype.tz) == "UTC", timeframe

    def test_every_bar_sits_on_its_timeframe_grid(self):
        stack = build_timeframe_stack(
            make_frame(288, timeframe=Timeframe.M5),
            Timeframe.M5,
            (Timeframe.M15, Timeframe.H1, Timeframe.H4),
            Symbol.EURUSD,
        )
        epoch = pd.Timestamp("1970-01-01", tz=UTC)
        for timeframe, frame in stack.items():
            offsets = (frame["timestamp"] - epoch) // pd.Timedelta(minutes=1)
            assert (offsets % timeframe.minutes == 0).all(), timeframe

    def test_close_time_of_one_bar_equals_open_of_the_next(self):
        """Bars must tile the timeline exactly — no overlap, no seam."""
        frame = with_close_time(make_frame(50, timeframe=Timeframe.M15), Timeframe.M15)
        assert (frame["close_time"].iloc[:-1].values == frame["timestamp"].iloc[1:].values).all()
