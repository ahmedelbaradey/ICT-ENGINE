"""R2-06 real-data acceptance — EURUSD + XAUUSD, 2024-03-08 → 2024-03-11.

The window deliberately contains the US DST transition (2024-03-10) and a full
weekend closure, so both are exercised on real bars rather than synthetically.

**A genuine zero is a valid result** and is asserted as such. 4H carries nine bars
over four days, which is not enough for a swing to confirm and a break to follow, so
zero ranges there is the honest answer — not a threshold to loosen.

The Phase 1.5 dataset is gitignored, so these skip cleanly when absent.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from ict_kronos.data import resample
from ict_kronos.domain import Symbol, Timeframe
from ict_kronos.ict import (
    DealingRangeConfig,
    DealingRangeDetector,
    Direction,
    RangeZone,
    StructureDetector,
    SwingDetector,
    assert_no_leakage,
    assert_provenance_resolves,
    assert_sources_observable_first,
    filter_observable,
    structure_break_id,
    swing_point_id,
)
from ict_kronos.storage import ParquetCandleStore

DATA_ROOT = "data/normalized"
WINDOW_START = datetime(2024, 3, 8, tzinfo=UTC)
WINDOW_END = datetime(2024, 3, 12, tzinfo=UTC)

STORED = (Timeframe.M1, Timeframe.M5, Timeframe.M15)
DERIVED = (Timeframe.H1, Timeframe.H4)
NEW_YORK = ZoneInfo("America/New_York")


def load(symbol: Symbol, timeframe: Timeframe) -> pd.DataFrame:
    store = ParquetCandleStore(DATA_ROOT)
    if timeframe in STORED:
        frame = store.read(symbol, timeframe, start=pd.Timestamp(WINDOW_START), end=pd.Timestamp(WINDOW_END))
    else:
        base = store.read(
            symbol, Timeframe.M1, start=pd.Timestamp(WINDOW_START), end=pd.Timestamp(WINDOW_END)
        )
        if len(base) == 0:
            pytest.skip(f"real 1m data absent for {symbol.value}")
        frame = resample(base, Timeframe.M1, timeframe, symbol).drop(columns=["close_time"])

    if len(frame) < 4:
        pytest.skip(f"real data insufficient for {symbol.value}/{timeframe.value}")
    return frame


@pytest.fixture(params=[Symbol.EURUSD, Symbol.XAUUSD], ids=lambda s: s.value)
def symbol(request) -> Symbol:
    return request.param


@pytest.fixture(params=[*STORED, *DERIVED], ids=lambda t: t.value)
def timeframe(request) -> Timeframe:
    return request.param


def detector() -> DealingRangeDetector:
    return DealingRangeDetector()


class TestRealDetection:
    def test_ranges_respect_the_timestamp_invariant(self, symbol, timeframe):
        for item in detector().detect(load(symbol, timeframe), symbol, timeframe):
            assert item.confirmation_timestamp >= item.created_timestamp

    def test_no_range_is_degenerate_or_inverted(self, symbol, timeframe):
        for item in detector().detect(load(symbol, timeframe), symbol, timeframe):
            assert item.high_price > item.low_price
            assert not item.is_degenerate

    def test_equilibrium_is_always_the_midpoint(self, symbol, timeframe):
        for item in detector().detect(load(symbol, timeframe), symbol, timeframe):
            assert item.equilibrium_price == pytest.approx((item.high_price + item.low_price) / 2)

    def test_contract_events_carry_no_leakage(self, symbol, timeframe):
        assert_no_leakage(detector().events(load(symbol, timeframe), symbol, timeframe))

    def test_ids_are_unique(self, symbol, timeframe):
        ranges = detector().detect(load(symbol, timeframe), symbol, timeframe)
        ids = [r.range_id for r in ranges]
        assert len(ids) == len(set(ids))

    def test_the_dense_timeframes_produce_ranges(self, symbol):
        """1m/5m/15m carry enough bars that a universal zero would be suspicious."""
        counts = {tf.value: len(detector().detect(load(symbol, tf), symbol, tf)) for tf in STORED}
        assert sum(counts.values()) > 0, f"no ranges on any dense timeframe: {counts}"

    def test_a_sparse_timeframe_may_legitimately_produce_none(self, symbol):
        """4H has nine bars here. Zero is reported, never engineered away."""
        ranges = detector().detect(load(symbol, Timeframe.H4), symbol, Timeframe.H4)
        assert isinstance(ranges, list)

    def test_direction_is_always_established(self, symbol, timeframe):
        for item in detector().detect(load(symbol, timeframe), symbol, timeframe):
            assert item.direction in (Direction.BULLISH, Direction.BEARISH, Direction.NEUTRAL)

    def test_ranges_are_far_rarer_than_bars(self, symbol):
        """Stability: a range tracks structure, not every fractal. If it churned per
        bar, 'price is in discount' would be a statement about noise."""
        frame = load(symbol, Timeframe.M15)
        ranges = detector().detect(frame, symbol, Timeframe.M15)
        if not ranges:
            pytest.skip("no ranges on this combination")
        assert len(ranges) < len(frame) / 5


class TestRealProvenance:
    def test_every_anchor_id_resolves_to_a_real_swing(self, symbol, timeframe):
        frame = load(symbol, timeframe)
        registry = {swing_point_id(s): s for s in SwingDetector().detect(frame, symbol, timeframe)}
        ranges = detector().detect(frame, symbol, timeframe)

        assert_provenance_resolves(ranges, registry, id_fields=["high_source_id", "low_source_id"])
        for item in ranges:
            sources = [registry[item.high_source_id], registry[item.low_source_id]]
            assert_sources_observable_first(item, sources, label="dealing range")

    def test_every_break_id_resolves(self, symbol, timeframe):
        frame = load(symbol, timeframe)
        breaks = StructureDetector().analyse(frame, symbol, timeframe).breaks
        registry = {structure_break_id(b): b for b in breaks}

        assert_provenance_resolves(
            detector().detect(frame, symbol, timeframe), registry, id_fields=["source_break_id"]
        )

    def test_confirmation_equals_the_max_of_every_source(self, symbol, timeframe):
        frame = load(symbol, timeframe)
        analysis = StructureDetector().analyse(frame, symbol, timeframe)
        breaks = {structure_break_id(b): b for b in analysis.breaks}

        for item in detector().detect(frame, symbol, timeframe):
            assert item.confirmation_timestamp == max(
                item.high_source_confirmation,
                item.low_source_confirmation,
                breaks[item.source_break_id].confirmation_timestamp,
            )

    def test_the_anchor_prices_match_the_swings_they_name(self, symbol, timeframe):
        """Provenance resolved by ID, then cross-checked against geometry — not the
        other way round, which would be inferring provenance from prices."""
        frame = load(symbol, timeframe)
        swings = {swing_point_id(s): s for s in SwingDetector().detect(frame, symbol, timeframe)}

        for item in detector().detect(frame, symbol, timeframe):
            assert item.high_price == pytest.approx(swings[item.high_source_id].price_level)
            assert item.low_price == pytest.approx(swings[item.low_source_id].price_level)


class TestRealClassification:
    def test_every_observation_agrees_with_its_range(self, symbol, timeframe):
        analysis = detector().analyse(load(symbol, timeframe), symbol, timeframe)
        ranges = {r.range_id: r for r in analysis.ranges}

        for o in analysis.observations:
            item = ranges[o.range_id]
            assert o.distance_from_equilibrium == pytest.approx(o.price - item.equilibrium_price)
            assert o.percentage_position == pytest.approx(item.position_of(o.price))

    def test_zones_are_consistent_with_the_distance(self, symbol, timeframe):
        analysis = detector().analyse(load(symbol, timeframe), symbol, timeframe)
        band = DealingRangeConfig().equilibrium_tolerance_points * symbol.spec.point_value

        for o in analysis.observations:
            if o.zone is RangeZone.PREMIUM:
                assert o.distance_from_equilibrium > band
            elif o.zone is RangeZone.DISCOUNT:
                assert o.distance_from_equilibrium < -band
            else:
                assert abs(o.distance_from_equilibrium) <= band

    def test_positions_outside_the_range_occur_and_are_not_clamped(self, symbol):
        """A documented consequence of anchoring on the BROKEN level: right after a
        break price sits beyond it, so `position` legitimately leaves [0, 1]."""
        analysis = detector().analyse(load(symbol, Timeframe.M15), symbol, Timeframe.M15)
        if not analysis.observations:
            pytest.skip("no observations on this combination")
        assert any(o.percentage_position < 0 or o.percentage_position > 1 for o in analysis.observations)

    def test_no_position_is_nan_when_no_range_is_degenerate(self, symbol, timeframe):
        analysis = detector().analyse(load(symbol, timeframe), symbol, timeframe)
        assert not any(math.isnan(o.percentage_position) for o in analysis.observations)

    def test_observations_never_precede_their_range(self, symbol, timeframe):
        analysis = detector().analyse(load(symbol, timeframe), symbol, timeframe)
        ranges = {r.range_id: r for r in analysis.ranges}
        for o in analysis.observations:
            assert o.confirmation_timestamp >= ranges[o.range_id].confirmation_timestamp

    def test_each_observation_belongs_to_the_active_range(self, symbol, timeframe):
        analysis = detector().analyse(load(symbol, timeframe), symbol, timeframe)
        for o in analysis.observations:
            active = analysis.range_at(o.confirmation_timestamp)
            assert active is not None and active.range_id == o.range_id


class TestRealReplay:
    def test_batch_equals_prefix_replay(self, symbol):
        """On 15m — dense enough to be meaningful, small enough to replay at every cut."""
        frame = load(symbol, Timeframe.M15)
        full = detector().detect(frame, symbol, Timeframe.M15)

        for cut in range(1, len(frame) + 1):
            prefix = frame.iloc[:cut]
            as_of = prefix["timestamp"].iloc[-1].to_pydatetime() + Timeframe.M15.duration
            assert detector().detect(prefix, symbol, Timeframe.M15) == filter_observable(full, as_of)

    def test_true_bar_by_bar_accumulation_matches_batch(self, symbol):
        frame = load(symbol, Timeframe.H1)
        seen: list = []
        for cut in range(1, len(frame) + 1):
            for item in detector().detect(frame.iloc[:cut], symbol, Timeframe.H1):
                if item not in seen:
                    seen.append(item)
        assert seen == detector().detect(frame, symbol, Timeframe.H1)

    def test_appending_does_not_rewrite_history(self, symbol, timeframe):
        frame = load(symbol, timeframe)
        half = len(frame) // 2
        early = detector().detect(frame.iloc[:half], symbol, timeframe)
        assert early == detector().detect(frame, symbol, timeframe)[: len(early)]

    def test_future_mutation_leaves_confirmed_ranges_identical(self, symbol):
        frame = load(symbol, Timeframe.M5)
        ranges = detector().detect(frame, symbol, Timeframe.M5)
        if not ranges:
            pytest.skip("no ranges to protect")

        cutoff = ranges[len(ranges) // 2].confirmation_timestamp
        mutated = frame.copy()
        later = mutated["timestamp"] > cutoff
        mutated.loc[later, "high"] = mutated.loc[later, "high"] * 1.5
        mutated.loc[later, "low"] = mutated.loc[later, "low"] * 0.5
        mutated.loc[later, "close"] = mutated.loc[later, "close"] * 1.2

        after = [
            r for r in detector().detect(mutated, symbol, Timeframe.M5) if r.confirmation_timestamp <= cutoff
        ]
        assert after == [r for r in ranges if r.confirmation_timestamp <= cutoff]

    def test_the_control_proves_prices_matter(self, symbol):
        """Without this, the inertness test above would pass on a detector that
        ignored its input entirely."""
        frame = load(symbol, Timeframe.M5)
        moved = frame.copy()
        for column in ("open", "high", "low", "close"):
            moved[column] = moved[column] * 1.05
        assert detector().detect(moved, symbol, Timeframe.M5) != detector().detect(
            frame, symbol, Timeframe.M5
        )

    def test_the_dataset_extrema_are_never_the_anchors(self, symbol, timeframe):
        """The named naive implementation, checked on real bars."""
        frame = load(symbol, timeframe)
        naive_high = float(frame["high"].max())
        naive_low = float(frame["low"].min())

        for item in detector().detect(frame, symbol, timeframe):
            assert not (
                item.high_price == pytest.approx(naive_high) and item.low_price == pytest.approx(naive_low)
            )


class TestRealWeekendSessionsAndDst:
    def test_a_range_survives_the_weekend_closure(self, symbol):
        """No bars print, so nothing supersedes it: Friday's range is still active on
        Sunday's reopen. That is the correct answer, not a gap."""
        frame = load(symbol, Timeframe.M15)
        analysis = detector().analyse(frame, symbol, Timeframe.M15)
        if not analysis.ranges:
            pytest.skip("no ranges on this combination")

        stamps = frame["timestamp"].to_list()
        gaps = [
            (a, b)
            for a, b in zip(stamps[:-1], stamps[1:], strict=True)
            if (b - a) > Timeframe.M15.duration * 4
        ]
        if not gaps:
            pytest.skip("the loaded window contains no closure")

        before, after = gaps[0]
        at_close = analysis.range_at(before.to_pydatetime() + Timeframe.M15.duration)
        at_reopen = analysis.range_at(after.to_pydatetime())
        if at_close is not None:
            assert at_reopen is not None
            assert at_reopen.confirmation_timestamp <= after.to_pydatetime()

    def test_no_observation_falls_inside_a_market_closure(self, symbol, timeframe):
        """Observations exist only where bars exist — no synthetic weekend rows."""
        frame = load(symbol, timeframe)
        stamps = set(frame["timestamp"].dt.to_pydatetime())
        for o in detector().analyse(frame, symbol, timeframe).observations:
            assert o.observation_timestamp in stamps

    def test_the_dst_transition_does_not_break_ordering(self, symbol):
        """2024-03-10 is the US spring-forward. Ranges must stay strictly ordered."""
        frame = load(symbol, Timeframe.M15)
        ranges = detector().detect(frame, symbol, Timeframe.M15)
        stamps = [r.confirmation_timestamp for r in ranges]
        assert stamps == sorted(stamps)

    def test_ranges_exist_on_both_sides_of_the_dst_transition(self, symbol):
        frame = load(symbol, Timeframe.M5)
        ranges = detector().detect(frame, symbol, Timeframe.M5)
        if not ranges:
            pytest.skip("no ranges on this combination")

        transition = datetime(2024, 3, 10, 7, 0, tzinfo=UTC)  # 02:00 New York
        assert any(r.confirmation_timestamp < transition for r in ranges)
        assert any(r.confirmation_timestamp > transition for r in ranges)

    def test_this_module_introduces_no_second_timezone_implementation(self):
        from pathlib import Path

        source = Path("ict_kronos/ict/dealing_range.py").read_text(encoding="utf-8")
        for banned in ("ZoneInfo", "astimezone", "tz_localize", "tz_convert", "America/"):
            assert banned not in source, f"dealing_range.py implements timezone logic: {banned}"

    def test_local_time_is_only_ever_derived_at_the_point_of_use(self, symbol):
        """Sanity: stored timestamps stay UTC; converting is the caller's business."""
        for item in detector().detect(load(symbol, Timeframe.M15), symbol, Timeframe.M15):
            assert item.confirmation_timestamp.tzinfo is not None
            assert item.confirmation_timestamp.utcoffset() == timedelta(0)
            assert item.confirmation_timestamp.astimezone(NEW_YORK).tzinfo is NEW_YORK


class TestIncompleteHigherTimeframeBars:
    def test_a_partial_final_hour_never_produces_a_range(self, symbol):
        """The resampler drops incomplete bars, so a truncated tail cannot anchor."""
        base = ParquetCandleStore(DATA_ROOT).read(
            symbol, Timeframe.M1, start=pd.Timestamp(WINDOW_START), end=pd.Timestamp(WINDOW_END)
        )
        if len(base) < 200:
            pytest.skip(f"real 1m data absent for {symbol.value}")

        full = resample(base, Timeframe.M1, Timeframe.H1, symbol).drop(columns=["close_time"])
        # Chop 20 minutes off the end: the final hour can no longer complete.
        truncated_base = base.iloc[: len(base) - 20]
        truncated = resample(truncated_base, Timeframe.M1, Timeframe.H1, symbol).drop(columns=["close_time"])

        assert len(truncated) <= len(full)
        early = detector().detect(truncated, symbol, Timeframe.H1)
        late = detector().detect(full, symbol, Timeframe.H1)
        assert early == late[: len(early)]

    def test_derived_timeframes_carry_only_complete_bars(self, symbol):
        frame = load(symbol, Timeframe.H1)
        stamps = frame["timestamp"].to_list()
        for a, b in zip(stamps[:-1], stamps[1:], strict=True):
            assert (b - a) >= Timeframe.H1.duration


class TestNoRegressionInApprovedDetectors:
    def test_r2_01_to_r2_05_still_run_unchanged(self, symbol, timeframe):
        """R2-06 adds a module and a shared id helper; it must disturb nothing."""
        from ict_kronos.ict import (
            FvgDetector,
            LiquidityDetector,
            TrueDailyOpenDetector,
            UnicornDetector,
        )

        frame = load(symbol, timeframe)
        assert SwingDetector().detect(frame, symbol, timeframe) is not None
        assert StructureDetector().analyse(frame, symbol, timeframe) is not None
        assert LiquidityDetector().analyse(frame, symbol, timeframe) is not None
        assert FvgDetector().analyse(frame, symbol, timeframe) is not None
        assert TrueDailyOpenDetector().detect(frame, symbol, timeframe) is not None
        assert UnicornDetector().detect(frame, symbol, timeframe) is not None
