"""Gap causes: market closure, provider loss, dataset boundary — and honest ignorance.

Fresh July-2026 data showed that pooling every missing observation into one "incomplete"
category destroyed 100% of Daily bars and 22.5% of 4H bars, while the timeframes the
engine was validated on lost almost nothing. The rule was answering the wrong question:
*"did every constituent minute trade?"* is not *"is this a valid aggregation of what
traded?"*.

These tests pin the replacement, and the shape of the replacement matters as much as the
outcome:

* **Only a boundary-incomplete bar is rejected.** A coverage ratio is a quality signal.
  There is no 95%, 98% or 99% cut-off, because no evidence supports one.
* **A closure must be PROVEN before it is claimed.** The session profile only calls a
  slot closed when it is absent on every observed occurrence of that weekday — so the
  rule can only ever under-claim, and a real outage is never relabelled as "the market
  was shut".
* **Nothing is fabricated.** No bar is invented, forward-filled or interpolated on any
  path here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from ict_kronos.data import resample
from ict_kronos.data.coverage import (
    BarQuality,
    GapCause,
    SessionProfile,
    coverage_report,
)
from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame

SYM = Symbol.EURUSD
M1 = Timeframe.M1


def minute_bars(start: datetime, count: int, *, skip=(), price=1.0):
    """``count`` consecutive 1m bars from ``start``, omitting the offsets in ``skip``.

    Omitting rather than blanking: a minute with no ticks produces no bar at all, which
    is exactly the condition being classified.
    """
    candles = [
        MarketCandle(
            timestamp=start + timedelta(minutes=i),
            symbol=SYM,
            timeframe=M1,
            open=price + 0.00001 * i,
            high=price + 0.00001 * i + 0.00002,
            low=price + 0.00001 * i - 0.00002,
            close=price + 0.00001 * i,
            volume=1.0,
        )
        for i in range(count)
        if i not in set(skip)
    ]
    return candles_to_frame(candles)


def week_of_minutes(*, closed_slots=(), skip_absolute=()):
    """A full Mon→Fri week of 1m bars, minus whole ``(weekday, minute_of_day)`` slots.

    Long enough for the session profile to see each weekday at least twice where the
    test needs it, and honest about the shape of a real FX week.
    """
    start = datetime(2026, 6, 1, tzinfo=UTC)  # a Monday
    closed = set(closed_slots)
    candles = []
    for i in range(14 * 24 * 60):  # two full weeks
        moment = start + timedelta(minutes=i)
        slot = (moment.weekday(), moment.hour * 60 + moment.minute)
        if slot in closed or moment in set(skip_absolute):
            continue
        candles.append(
            MarketCandle(
                timestamp=moment,
                symbol=SYM,
                timeframe=M1,
                open=1.0,
                high=1.0001,
                low=0.9999,
                close=1.0,
                volume=1.0,
            )
        )
    return candles_to_frame(candles)


class TestSessionProfileProvesRatherThanAssumes:
    def test_a_slot_absent_on_every_occurrence_is_proven_closed(self):
        """Case A — a genuine recurring session closure, discovered from the data."""
        closed = {(weekday, minute) for weekday in range(7) for minute in range(21 * 60, 22 * 60)}
        profile = SessionProfile.from_source(week_of_minutes(closed_slots=closed), M1, SYM)
        assert profile.is_closed(pd.Timestamp("2026-06-02 21:30", tz="UTC"))
        assert not profile.is_closed(pd.Timestamp("2026-06-02 20:30", tz="UTC"))

    def test_a_single_traded_observation_disqualifies_the_whole_slot(self):
        """The conservative direction: under-claim closure, never over-claim it.

        One tick anywhere in the window is enough to say "this minute can trade", and
        every other day's absence then needs a different explanation.
        """
        closed = {(weekday, 21 * 60) for weekday in range(7) if weekday != 1}
        profile = SessionProfile.from_source(week_of_minutes(closed_slots=closed), M1, SYM)
        assert profile.is_closed(pd.Timestamp("2026-06-01 21:00", tz="UTC"))  # Monday: never traded
        assert not profile.is_closed(pd.Timestamp("2026-06-02 21:00", tz="UTC"))  # Tuesday: traded

    def test_the_profile_is_per_weekday_because_the_week_is_not_uniform(self):
        """Friday evening is shut; Tuesday evening is not. One rule cannot cover both."""
        closed = {(4, minute) for minute in range(21 * 60, 24 * 60)}
        profile = SessionProfile.from_source(week_of_minutes(closed_slots=closed), M1, SYM)
        assert profile.is_closed(pd.Timestamp("2026-06-05 22:00", tz="UTC"))  # Friday
        assert not profile.is_closed(pd.Timestamp("2026-06-02 22:00", tz="UTC"))  # Tuesday

    def test_one_occurrence_of_a_weekday_proves_nothing(self):
        """`min_occurrences` is a sample-size guard, not a coverage threshold."""
        start = datetime(2026, 6, 1, tzinfo=UTC)
        frame = minute_bars(start, 24 * 60, skip=range(21 * 60, 22 * 60))
        profile = SessionProfile.from_source(frame, M1, SYM)
        assert not profile.is_closed(pd.Timestamp("2026-06-01 21:30", tz="UTC"))

    def test_an_empty_frame_claims_nothing(self):
        profile = SessionProfile.from_source(candles_to_frame([]), M1, SYM)
        assert profile.closed_slots == frozenset()


class TestGapClassification:
    def report(self, frame, target=Timeframe.H1, profile=None):
        return coverage_report(frame, M1, target, SYM, profile=profile)

    def test_a_fully_covered_bar_is_complete(self):
        frame = minute_bars(datetime(2026, 6, 1, tzinfo=UTC), 180)
        bars = self.report(frame).bars
        assert bars[0].quality is BarQuality.COMPLETE
        assert bars[0].cause is GapCause.NONE
        assert bars[0].coverage_ratio == 1.0

    def test_C_provider_like_loss_inside_an_open_session_is_undetermined(self):
        """Case C — missing minutes with no proven cause are FLAGGED, not explained."""
        frame = minute_bars(datetime(2026, 6, 1, tzinfo=UTC), 180, skip=(10, 11, 12))
        bar = self.report(frame).bars[0]
        assert bar.quality is BarQuality.DEGRADED_UNKNOWN
        assert bar.cause is GapCause.UNDETERMINED
        assert bar.undetermined_observations == 3
        assert bar.market_closed_observations == 0

    def test_A_a_proven_closure_is_reported_as_a_market_gap(self):
        """Case A — the same missing minutes, but now provably a recurring closure."""
        closed = {(weekday, minute) for weekday in range(7) for minute in range(21 * 60, 22 * 60)}
        frame = week_of_minutes(closed_slots=closed)
        profile = SessionProfile.from_source(frame, M1, SYM)
        report = self.report(frame, Timeframe.H4, profile)
        affected = [b for b in report.bars if b.market_closed_observations > 0]
        assert affected, "the fixture must contain a closed hour inside a 4H period"
        for bar in affected:
            assert bar.undetermined_observations == 0
            assert bar.quality is BarQuality.MARKET_GAP
            assert bar.cause is GapCause.MARKET_CLOSED

    def test_a_mixture_of_proven_and_unproven_is_never_called_a_market_gap(self):
        """One unexplained minute is enough to withhold the clean label."""
        closed = {(weekday, minute) for weekday in range(7) for minute in range(21 * 60, 22 * 60)}
        extra = {datetime(2026, 6, 2, 20, 5, tzinfo=UTC)}
        frame = week_of_minutes(closed_slots=closed, skip_absolute=extra)
        profile = SessionProfile.from_source(week_of_minutes(closed_slots=closed), M1, SYM)
        report = self.report(frame, Timeframe.H4, profile)
        bar = next(b for b in report.bars if b.timestamp == pd.Timestamp("2026-06-02 20:00", tz="UTC"))
        assert bar.market_closed_observations > 0
        assert bar.undetermined_observations == 1
        assert bar.quality is BarQuality.DEGRADED_UNKNOWN

    def test_D_a_truncated_period_at_the_dataset_edge_is_boundary_incomplete(self):
        """Case D — the ONLY category that makes a bar ineligible."""
        frame = minute_bars(datetime(2026, 6, 1, 0, 30, tzinfo=UTC), 200)
        first = self.report(frame).bars[0]
        assert first.boundary_incomplete is True
        assert first.quality is BarQuality.BOUNDARY_INCOMPLETE
        assert first.production_eligible is False

    def test_D_the_trailing_period_is_also_boundary_incomplete(self):
        frame = minute_bars(datetime(2026, 6, 1, tzinfo=UTC), 90)
        last = self.report(frame).bars[-1]
        assert last.boundary_incomplete is True
        assert last.production_eligible is False

    def test_E_consecutive_missing_observations_are_measured_as_a_run(self):
        """Case E — a structured outage looks nothing like scattered quiet minutes."""
        scattered = minute_bars(datetime(2026, 6, 1, tzinfo=UTC), 180, skip=(5, 25, 45))
        contiguous = minute_bars(datetime(2026, 6, 1, tzinfo=UTC), 180, skip=(5, 6, 7))
        a, b = self.report(scattered).bars[0], self.report(contiguous).bars[0]
        assert a.missing_observations == b.missing_observations == 3
        assert a.coverage_ratio == b.coverage_ratio
        assert a.longest_missing_run == 1
        assert b.longest_missing_run == 3

    def test_B_an_opening_gap_in_PRICE_is_not_a_coverage_gap(self):
        """Case B — a jump between bars is market behaviour, not a missing observation.

        Conflating the two would let a violent Sunday open look like data loss.
        """
        start = datetime(2026, 6, 1, tzinfo=UTC)
        early = minute_bars(start, 60, price=1.0)
        late = minute_bars(start + timedelta(minutes=60), 60, price=1.05)
        frame = pd.concat([early, late], ignore_index=True)
        for bar in self.report(frame).bars:
            assert bar.quality is BarQuality.COMPLETE
            assert bar.missing_observations == 0

    def test_F_a_gap_next_to_a_large_price_move_is_still_only_a_gap(self):
        """Case F — proximity to a big move must not change the classification."""
        start = datetime(2026, 6, 1, tzinfo=UTC)
        calm = minute_bars(start, 180, skip=(30, 31))
        violent = calm.copy()
        index = violent.index[violent["timestamp"] >= pd.Timestamp(start + timedelta(minutes=32))]
        for column in ("open", "high", "low", "close"):
            violent.loc[index, column] = violent.loc[index, column] * 1.05

        a, b = self.report(calm).bars[0], self.report(violent).bars[0]
        assert a.quality is b.quality is BarQuality.DEGRADED_UNKNOWN
        assert a.as_dict() == b.as_dict(), "coverage must read observations, never prices"


class TestOnlyBoundaryRejects:
    def test_a_heavily_gapped_interior_bar_is_still_production_eligible(self):
        """The rule the fresh month forced: coverage is a signal, not a gate."""
        frame = minute_bars(datetime(2026, 6, 1, tzinfo=UTC), 180, skip=range(60, 115))
        bar = next(
            b
            for b in coverage_report(frame, M1, Timeframe.H1, SYM).bars
            if b.timestamp == pd.Timestamp("2026-06-01 01:00", tz="UTC")
        )
        assert bar.coverage_ratio < 0.2
        assert bar.production_eligible is True
        assert bar.quality is BarQuality.DEGRADED_UNKNOWN

    def test_no_percentage_threshold_appears_anywhere_in_the_module(self):
        """The user's explicit prohibition, enforced on the source rather than trusted."""
        from tests.test_market_state import _code_of

        code = _code_of("ict_kronos/data/coverage.py")
        for banned in ("0.95", "0.98", "0.99", "95", "98", "99"):
            assert banned not in code, f"coverage.py hides a coverage threshold: {banned!r}"

    def test_the_module_never_fabricates_or_fills(self):
        from tests.test_market_state import _code_of

        code = _code_of("ict_kronos/data/coverage.py")
        for banned in ("ffill", "bfill", "fillna", "interpolate", "reindex(", "pad("):
            assert banned not in code, f"coverage.py repairs data: {banned!r}"


class TestTheResamplerFollowsTheSameRule:
    """G — 1H, 4H and 1D independently, through the function production actually calls."""

    def frame_with_gaps(self, days=3):
        """Three full UTC days of minutes, each missing a couple of scattered minutes."""
        start = datetime(2026, 6, 1, tzinfo=UTC)
        skip = {i for day in range(days) for i in (day * 1440 + 63, day * 1440 + 1419)}
        return minute_bars(start, days * 1440, skip=skip)

    @pytest.mark.parametrize("target", [Timeframe.H1, Timeframe.H4, Timeframe.D1])
    def test_scattered_missing_minutes_no_longer_destroy_the_bar(self, target):
        frame = self.frame_with_gaps()
        bars = resample(frame, M1, target, SYM)
        assert len(bars) > 0, f"{target.value} must survive a handful of quiet minutes"

    def test_daily_bars_exist_at_all_which_they_previously_did_not(self):
        """The headline regression: the old rule produced ZERO daily bars, always."""
        bars = resample(self.frame_with_gaps(days=5), M1, Timeframe.D1, SYM)
        assert len(bars) >= 4

    def test_the_aggregation_itself_is_unchanged(self):
        """Only the REJECTION rule moved. Open/high/low/close still come from the ticks."""
        frame = minute_bars(datetime(2026, 6, 1, tzinfo=UTC), 120)
        bar = resample(frame, M1, Timeframe.H1, SYM).iloc[0]
        hour = frame[frame["timestamp"] < pd.Timestamp("2026-06-01 01:00", tz="UTC")]
        assert bar["open"] == pytest.approx(hour["open"].iloc[0])
        assert bar["close"] == pytest.approx(hour["close"].iloc[-1])
        assert bar["high"] == pytest.approx(hour["high"].max())
        assert bar["low"] == pytest.approx(hour["low"].min())

    def test_a_boundary_truncated_bar_is_still_dropped(self):
        """The original intent survives: no bar built from a fragment of its period."""
        frame = minute_bars(datetime(2026, 6, 1, 0, 30, tzinfo=UTC), 200)
        bars = resample(frame, M1, Timeframe.H1, SYM)
        assert pd.Timestamp("2026-06-01 00:00", tz="UTC") not in set(bars["timestamp"])

    def test_keeping_boundary_bars_is_still_available_and_still_explicit(self):
        frame = minute_bars(datetime(2026, 6, 1, 0, 30, tzinfo=UTC), 200)
        kept = resample(frame, M1, Timeframe.H1, SYM, drop_boundary_incomplete=False)
        assert pd.Timestamp("2026-06-01 00:00", tz="UTC") in set(kept["timestamp"])

    def test_the_resampler_never_fabricates_a_missing_bar(self):
        """A closed weekend stays absent — the count is what traded, nothing more."""
        start = datetime(2026, 6, 5, tzinfo=UTC)  # Friday
        friday = minute_bars(start, 1440)
        monday = minute_bars(start + timedelta(days=3), 1440)
        frame = pd.concat([friday, monday], ignore_index=True)
        days = set(resample(frame, M1, Timeframe.D1, SYM)["timestamp"].dt.date)
        assert days == {start.date(), (start + timedelta(days=3)).date()}
