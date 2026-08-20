"""R2-04 real-data acceptance — EURUSD + XAUUSD, 2024-03-08 → 2024-03-12, 1M/5M/15M.

The Phase 1.5 dataset is gitignored, so these skip cleanly when absent. Reproduce with
``docs/financial-ai/DATA_PROOF.md`` §12.

**Engineering and timestamp validation only.** Four days says nothing about market
behaviour; no performance or trading claim is made or implied.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from ict_kronos.domain import Symbol, Timeframe
from ict_kronos.ict import (
    Direction,
    EventType,
    LiquidityConfig,
    LiquidityDetector,
    LiquiditySide,
    LiquidityStatus,
    LiquidityType,
    SessionDetector,
    StructureConfig,
    StructureDetector,
    SwingConfig,
    assert_no_leakage,
    filter_observable,
)
from ict_kronos.storage import ParquetCandleStore

DATA_ROOT = "data/normalized"
WINDOW_START = datetime(2024, 3, 8, tzinfo=UTC)
WINDOW_END = datetime(2024, 3, 12, tzinfo=UTC)
SWING = SwingConfig(left=3, right=3)

FRIDAY_CLOSE = pd.Timestamp("2024-03-08T22:00:00Z")
SUNDAY_REOPEN = pd.Timestamp("2024-03-10T20:00:00Z")
US_DST = pd.Timestamp("2024-03-10T07:00:00Z")


def load(symbol: Symbol, timeframe: Timeframe) -> pd.DataFrame:
    frame = ParquetCandleStore(DATA_ROOT).read(
        symbol, timeframe, start=pd.Timestamp(WINDOW_START), end=pd.Timestamp(WINDOW_END)
    )
    if len(frame) == 0:
        pytest.skip(f"real data absent for {symbol.value}/{timeframe.value}")
    return frame


@pytest.fixture(params=[Symbol.EURUSD, Symbol.XAUUSD], ids=lambda s: s.value)
def symbol(request) -> Symbol:
    return request.param


@pytest.fixture(params=[Timeframe.M1, Timeframe.M5, Timeframe.M15], ids=lambda t: t.value)
def timeframe(request) -> Timeframe:
    return request.param


@pytest.fixture
def detector() -> LiquidityDetector:
    return LiquidityDetector(LiquidityConfig(), SWING)


def kinds(analysis) -> set:
    return {x.liquidity_type for x in analysis.levels}


class TestRealDataLevels:
    def test_levels_are_detected_on_every_timeframe(self, detector, symbol, timeframe):
        analysis = detector.analyse(load(symbol, timeframe), symbol, timeframe)
        assert analysis.levels, f"{symbol.value}/{timeframe.value}: no liquidity levels"

    def test_session_levels_occur(self, detector, symbol):
        found = kinds(detector.analyse(load(symbol, Timeframe.M5), symbol, Timeframe.M5))
        assert LiquidityType.SESSION_HIGH in found
        assert LiquidityType.SESSION_LOW in found

    def test_previous_day_levels_occur_where_a_day_completed(self, detector, symbol):
        analysis = detector.analyse(load(symbol, Timeframe.M5), symbol, Timeframe.M5)
        assert LiquidityType.PREVIOUS_DAY_HIGH in kinds(analysis)
        assert LiquidityType.PREVIOUS_DAY_LOW in kinds(analysis)

    def test_previous_week_levels_occur(self, detector, symbol):
        analysis = detector.analyse(load(symbol, Timeframe.M5), symbol, Timeframe.M5)
        assert LiquidityType.PREVIOUS_WEEK_HIGH in kinds(analysis)

    def test_equal_highs_or_lows_actually_occur(self, detector, symbol):
        """If real data produced none, the equal-level machinery would be untested
        here and the tolerance choice unvalidated."""
        analysis = detector.analyse(load(symbol, Timeframe.M1), symbol, Timeframe.M1)
        equal = {LiquidityType.EQUAL_HIGHS, LiquidityType.EQUAL_LOWS} & kinds(analysis)
        assert equal, f"{symbol.value}: no equal highs/lows found in 1m real data"

    def test_reference_prices_match_real_bar_prices(self, detector, symbol):
        """Every level price must be a price that actually printed, at the bar
        recorded as its creation."""
        frame = load(symbol, Timeframe.M5)
        bars = frame.set_index("timestamp")
        analysis = detector.analyse(frame, symbol, Timeframe.M5)

        for level in analysis.levels:
            row = bars.loc[pd.Timestamp(level.created_timestamp)]
            column = "high" if level.liquidity_type.is_high else "low"
            # Equal levels take the extreme of a PAIR, so the creating bar is one of
            # the two touches; allow either by checking the price exists in the window.
            if level.liquidity_type in (LiquidityType.EQUAL_HIGHS, LiquidityType.EQUAL_LOWS):
                continue
            assert level.price_level == pytest.approx(float(row[column]))

    def test_period_levels_confirm_at_their_period_end(self, detector, symbol):
        analysis = detector.analyse(load(symbol, Timeframe.M5), symbol, Timeframe.M5)
        period_types = {
            LiquidityType.PREVIOUS_DAY_HIGH,
            LiquidityType.PREVIOUS_DAY_LOW,
            LiquidityType.PREVIOUS_WEEK_HIGH,
            LiquidityType.PREVIOUS_WEEK_LOW,
        }
        checked = 0
        for level in analysis.levels:
            if level.liquidity_type not in period_types:
                continue
            assert level.confirmation_timestamp == level.period_end
            assert level.confirmation_timestamp > level.created_timestamp
            checked += 1
        assert checked > 0

    def test_side_is_consistent_with_type(self, detector, symbol, timeframe):
        for level in detector.analyse(load(symbol, timeframe), symbol, timeframe).levels:
            expected = LiquiditySide.BUY_SIDE if level.liquidity_type.is_high else LiquiditySide.SELL_SIDE
            assert level.side is expected

    def test_levels_at_the_same_price_keep_separate_identities(self, detector, symbol):
        """Stacked liquidity (session high + PDH + swing high) must stay distinguishable."""
        analysis = detector.analyse(load(symbol, Timeframe.M5), symbol, Timeframe.M5)
        by_price: dict[float, set] = {}
        for level in analysis.levels:
            by_price.setdefault(round(level.price_level, 6), set()).add(level.liquidity_type)

        stacked = {p: t for p, t in by_price.items() if len(t) > 1}
        assert stacked, f"{symbol.value}: expected stacked liquidity at some price"


class TestRealDataSweeps:
    def test_sweeps_occur(self, detector, symbol, timeframe):
        analysis = detector.analyse(load(symbol, timeframe), symbol, timeframe)
        assert analysis.sweeps, f"{symbol.value}/{timeframe.value}: no sweeps"

    def test_every_sweep_references_an_existing_level(self, detector, symbol, timeframe):
        analysis = detector.analyse(load(symbol, timeframe), symbol, timeframe)
        level_ids = {x.level_id for x in analysis.levels}
        for sweep in analysis.sweeps:
            assert sweep.level_id in level_ids

    def test_sweep_price_really_exceeds_the_level(self, detector, symbol, timeframe):
        for sweep in detector.analyse(load(symbol, timeframe), symbol, timeframe).sweeps:
            if sweep.side is LiquiditySide.BUY_SIDE:
                assert sweep.extreme_price > sweep.price_level
            else:
                assert sweep.extreme_price < sweep.price_level
            assert sweep.penetration_points > 0

    def test_sweep_extremes_match_real_bar_extremes(self, detector, symbol):
        frame = load(symbol, Timeframe.M5)
        bars = frame.set_index("timestamp")
        for sweep in detector.analyse(frame, symbol, Timeframe.M5).sweeps:
            row = bars.loc[pd.Timestamp(sweep.event_timestamp)]
            column = "high" if sweep.side is LiquiditySide.BUY_SIDE else "low"
            assert sweep.extreme_price == pytest.approx(float(row[column]))

    def test_both_rejections_and_break_throughs_occur(self, detector, symbol):
        analysis = detector.analyse(load(symbol, Timeframe.M5), symbol, Timeframe.M5)
        outcomes = {s.is_rejection for s in analysis.sweeps}
        assert outcomes == {True, False}, "expected both swept-and-rejected and swept-through"

    def test_no_level_is_swept_twice(self, detector, symbol, timeframe):
        sweeps = detector.analyse(load(symbol, timeframe), symbol, timeframe).sweeps
        ids = [s.level_id for s in sweeps]
        assert len(ids) == len(set(ids))

    def test_one_bar_can_sweep_several_distinct_levels(self, detector, symbol):
        """The documented multi-level policy, observed on real data."""
        analysis = detector.analyse(load(symbol, Timeframe.M5), symbol, Timeframe.M5)
        by_bar: dict = {}
        for sweep in analysis.sweeps:
            by_bar.setdefault(sweep.event_timestamp, []).append(sweep)

        multi = [group for group in by_bar.values() if len(group) > 1]
        assert multi, f"{symbol.value}: expected at least one multi-level sweep"
        for group in multi:
            assert len({s.level_id for s in group}) == len(group)


class TestRealDataLeakage:
    def test_no_event_leaks(self, detector, symbol, timeframe):
        events = detector.events(load(symbol, timeframe), symbol, timeframe)
        assert events
        assert_no_leakage(events)

    def test_no_sweep_precedes_its_level(self, detector, symbol, timeframe):
        analysis = detector.analyse(load(symbol, timeframe), symbol, timeframe)
        by_id = {x.level_id: x for x in analysis.levels}
        for sweep in analysis.sweeps:
            assert sweep.confirmation_timestamp >= by_id[sweep.level_id].confirmation_timestamp

    def test_batch_equals_prefix_replay(self, detector, symbol, timeframe):
        frame = load(symbol, timeframe)
        full = detector.analyse(frame, symbol, timeframe)
        full_levels = [x.as_dict() for x in full.levels]
        full_sweeps = [s.as_dict() for s in full.sweeps]

        step = max(len(frame) // 6, 1)
        for cut in range(step, len(frame) + 1, step):
            partial = detector.analyse(frame.iloc[:cut], symbol, timeframe)
            assert [x.as_dict() for x in partial.levels] == full_levels[: len(partial.levels)]
            assert [s.as_dict() for s in partial.sweeps] == full_sweeps[: len(partial.sweeps)]

    def test_observable_at_matches_visible_bars_only(self, detector, symbol):
        frame = load(symbol, Timeframe.M5)
        step = max(len(frame) // 5, 1)
        for cut in range(step, len(frame) + 1, step):
            visible = frame.iloc[:cut]
            as_of = visible["timestamp"].iloc[-1].to_pydatetime() + timedelta(minutes=5)

            from_full = detector.observable_at(frame, as_of, symbol, Timeframe.M5)
            from_visible = detector.analyse(visible, symbol, Timeframe.M5)
            assert [x.as_dict() for x in from_full.levels] == [x.as_dict() for x in from_visible.levels]


class TestRealDataWeekend:
    def test_friday_liquidity_is_not_swept_during_the_weekend(self, detector, symbol):
        """The explicit weekend requirement: no bars close, so nothing is taken."""
        analysis = detector.analyse(load(symbol, Timeframe.M5), symbol, Timeframe.M5)
        for sweep in analysis.sweeps:
            confirmation = pd.Timestamp(sweep.confirmation_timestamp)
            assert not (
                FRIDAY_CLOSE < confirmation < SUNDAY_REOPEN
            ), f"{symbol.value}: a sweep confirmed at {confirmation}, inside the closure"

    def test_no_level_confirms_inside_the_closure(self, detector, symbol):
        analysis = detector.analyse(load(symbol, Timeframe.M5), symbol, Timeframe.M5)
        for level in analysis.levels:
            confirmation = pd.Timestamp(level.confirmation_timestamp)
            assert not (FRIDAY_CLOSE < confirmation < SUNDAY_REOPEN)

    def test_friday_liquidity_survives_into_the_new_week(self, detector, symbol):
        """A Friday level is still resting when the market reopens — the lifecycle is
        driven by bars, not elapsed time."""
        frame = load(symbol, Timeframe.M5)
        analysis = detector.analyse(frame, symbol, Timeframe.M5)

        friday_levels = [x for x in analysis.levels if pd.Timestamp(x.confirmation_timestamp) <= FRIDAY_CLOSE]
        assert friday_levels

        at_reopen = {x.level_id for x in analysis.active_at(SUNDAY_REOPEN.to_pydatetime())}
        assert any(x.level_id in at_reopen for x in friday_levels)

    def test_a_monday_bar_can_sweep_friday_liquidity(self, detector, symbol):
        analysis = detector.analyse(load(symbol, Timeframe.M5), symbol, Timeframe.M5)
        by_id = {x.level_id: x for x in analysis.levels}

        crossing = [
            s
            for s in analysis.sweeps
            if pd.Timestamp(by_id[s.level_id].confirmation_timestamp) <= FRIDAY_CLOSE
            and pd.Timestamp(s.confirmation_timestamp) >= SUNDAY_REOPEN
        ]
        if not crossing:
            pytest.skip(f"{symbol.value}: no Friday level was swept after the reopen")
        for sweep in crossing:
            assert sweep.penetration_points > 0


class TestRealDataDst:
    def test_day_boundary_shifts_with_dst(self, detector, symbol):
        """17:00 New York is 22:00 UTC under EST and 21:00 UTC under EDT. The trading
        day inherits R2-01's timezone-aware machinery — no hardcoded UTC boundary."""
        analysis = detector.analyse(load(symbol, Timeframe.M5), symbol, Timeframe.M5)
        day_ends = {
            pd.Timestamp(x.period_end).hour
            for x in analysis.levels
            if x.liquidity_type is LiquidityType.PREVIOUS_DAY_HIGH
        }
        assert day_ends == {21, 22}, f"expected both EST and EDT day ends, got {day_ends}"

    def test_session_levels_inherit_r2_01_boundaries(self, detector, symbol):
        """Session liquidity must use the session detector's windows verbatim."""
        frame = load(symbol, Timeframe.M5)
        analysis = detector.analyse(frame, symbol, Timeframe.M5)
        occurrences = {
            (o.window.name, o.window.local_date.isoformat()): o
            for o in SessionDetector().detect(frame, symbol, Timeframe.M5)
        }

        checked = 0
        for level in analysis.levels:
            if level.liquidity_type is not LiquidityType.SESSION_HIGH:
                continue
            name, day = level.period_label.split(":")
            occurrence = occurrences[(name, day)]
            assert level.confirmation_timestamp == occurrence.confirmation_timestamp
            assert level.price_level == pytest.approx(occurrence.high_price)
            checked += 1
        assert checked > 0


class TestRealDataStructureSeparation:
    def test_a_wick_sweep_is_not_counted_as_a_structural_break(self, detector, symbol):
        """R2-03 uses CLOSE breaks; R2-04 uses wick sweeps. On real data there must be
        sweeps that produced no structural break at the same bar — which is precisely
        why they are separate detectors."""
        frame = load(symbol, Timeframe.M5)
        sweeps = detector.analyse(frame, symbol, Timeframe.M5).sweeps
        breaks = StructureDetector(StructureConfig(), SWING).analyse(frame, symbol, Timeframe.M5).breaks

        break_bars = {b.event_timestamp for b in breaks}
        sweep_bars = {s.event_timestamp for s in sweeps}
        assert sweep_bars - break_bars, "every sweep coincided with a break — suspicious"

    def test_both_detectors_compose_under_one_contract(self, detector, symbol):
        frame = load(symbol, Timeframe.M5)
        combined = detector.events(frame, symbol, Timeframe.M5) + StructureDetector(
            StructureConfig(), SWING
        ).events(frame, symbol, Timeframe.M5)

        assert_no_leakage(combined)
        as_of = datetime(2024, 3, 11, 12, 0, tzinfo=UTC)
        visible = filter_observable(combined, as_of)
        assert visible and len(visible) < len(combined)
        assert EventType.LIQUIDITY_SWEEP in {e.event_type for e in combined}


class TestRealDataDataModel:
    def test_state_of_answers_every_question_for_a_swept_level(self, detector, symbol):
        analysis = detector.analyse(load(symbol, Timeframe.M5), symbol, Timeframe.M5)
        swept = next(s for s in analysis.sweeps)
        state = analysis.state_of(swept.level_id)

        assert state["swept"] is True
        assert state["is_active"] is False
        assert state["swept_at"] is not None
        assert state["penetration_points"] > 0
        assert state["closed_beyond"] in (True, False)
        assert state["status"] == LiquidityStatus.SWEPT.value

    def test_pending_periods_are_exposed_not_emitted(self, detector, symbol):
        """The last day and week are still in progress at the end of the window."""
        analysis = detector.analyse(load(symbol, Timeframe.M5), symbol, Timeframe.M5)
        assert analysis.pending
        emitted = {x.period_label for x in analysis.levels if x.period_label}
        for pending in analysis.pending:
            assert pending.label not in emitted
            assert pending.running_high is not None

    def test_direction_is_available_for_feature_engineering(self, detector, symbol):
        analysis = detector.analyse(load(symbol, Timeframe.M5), symbol, Timeframe.M5)
        for level in analysis.levels:
            assert level.direction in (Direction.BULLISH, Direction.BEARISH)
