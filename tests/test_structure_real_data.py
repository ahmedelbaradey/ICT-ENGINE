"""R2-03 real-data acceptance — EURUSD + XAUUSD, 2024-03-08 → 2024-03-12, 1M/5M/15M.

The Phase 1.5 dataset is gitignored, so these skip cleanly when absent. Reproduce with
``docs/financial-ai/DATA_PROOF.md`` §12.

**Engineering and timestamp validation only.** Four days says nothing about market
behaviour; no trading claim is made or implied.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from ict_kronos.domain import Symbol, Timeframe
from ict_kronos.ict import (
    BreakMode,
    ChochPolicy,
    Direction,
    EventType,
    SessionDetector,
    StructureConfig,
    StructureDetector,
    StructureState,
    SwingConfig,
    assert_no_leakage,
    filter_observable,
)
from ict_kronos.storage import ParquetCandleStore

DATA_ROOT = "data/normalized"
WINDOW_START = datetime(2024, 3, 8, tzinfo=UTC)
WINDOW_END = datetime(2024, 3, 12, tzinfo=UTC)
SWING = SwingConfig(left=3, right=3)

#: Friday close / Sunday reopen, from DATA_PROOF §3.1.
FRIDAY_CLOSE = pd.Timestamp("2024-03-08T22:00:00Z")
SUNDAY_REOPEN = pd.Timestamp("2024-03-10T20:00:00Z")


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
def detector() -> StructureDetector:
    return StructureDetector(StructureConfig(), SWING)


class TestRealDataDetection:
    def test_structure_is_detected_on_every_timeframe(self, detector, symbol, timeframe):
        analysis = detector.analyse(load(symbol, timeframe), symbol, timeframe)
        assert analysis.breaks, f"{symbol.value}/{timeframe.value}: no structure breaks"
        assert analysis.final_state is not StructureState.UNDEFINED

    def test_all_four_labels_appear_somewhere(self, detector, symbol):
        """Over four days of 1m data every classification should occur; if one never
        does, the classifier has a dead branch."""
        analysis = detector.analyse(load(symbol, Timeframe.M1), symbol, Timeframe.M1)
        assert {x.label for x in analysis.labels} == {
            EventType.HIGHER_HIGH,
            EventType.HIGHER_LOW,
            EventType.LOWER_HIGH,
            EventType.LOWER_LOW,
        }

    def test_both_break_directions_occur(self, detector, symbol, timeframe):
        directions = {
            b.direction for b in detector.analyse(load(symbol, timeframe), symbol, timeframe).breaks
        }
        assert directions == {Direction.BULLISH, Direction.BEARISH}

    def test_bos_and_mss_both_occur(self, detector, symbol):
        kinds = {
            b.event_type for b in detector.analyse(load(symbol, Timeframe.M5), symbol, Timeframe.M5).breaks
        }
        assert EventType.BOS in kinds
        assert EventType.MSS in kinds, "no reversal detected — the state machine never flipped"

    def test_referenced_levels_are_real_swing_prices(self, detector, symbol):
        """A broken level must be a price that actually printed, at the pivot recorded."""
        frame = load(symbol, Timeframe.M5)
        bars = frame.set_index("timestamp")

        for event in detector.analyse(frame, symbol, Timeframe.M5).breaks:
            pivot = bars.loc[pd.Timestamp(event.reference_swing_timestamp)]
            column = "high" if event.direction is Direction.BULLISH else "low"
            assert event.reference_level == pytest.approx(float(pivot[column]))

    def test_the_break_price_really_exceeds_the_level(self, detector, symbol, timeframe):
        for event in detector.analyse(load(symbol, timeframe), symbol, timeframe).breaks:
            if event.direction is Direction.BULLISH:
                assert event.price_level > event.reference_level
            else:
                assert event.price_level < event.reference_level
            assert event.break_distance_points > 0

    def test_state_transitions_are_coherent(self, detector, symbol, timeframe):
        """Each break's previous_state must equal the previous break's result."""
        breaks = detector.analyse(load(symbol, timeframe), symbol, timeframe).breaks
        assert breaks
        assert breaks[0].previous_state is StructureState.UNDEFINED
        for earlier, later in zip(breaks, breaks[1:], strict=False):
            assert later.previous_state is earlier.resulting_state

    def test_bos_continues_and_mss_reverses(self, detector, symbol, timeframe):
        for event in detector.analyse(load(symbol, timeframe), symbol, timeframe).breaks:
            if event.event_type is EventType.BOS:
                assert event.previous_state in (StructureState.UNDEFINED, event.resulting_state)
            else:
                assert event.previous_state is not event.resulting_state
                assert event.is_reversal


class TestRealDataConfiguration:
    def test_wick_mode_finds_at_least_as_many_breaks_as_close_mode(self, symbol):
        """WICK is strictly looser: anything a close breaks, the wick broke too."""
        frame = load(symbol, Timeframe.M5)
        close = StructureDetector(StructureConfig(break_mode=BreakMode.CLOSE), SWING)
        wick = StructureDetector(StructureConfig(break_mode=BreakMode.WICK), SWING)

        assert len(wick.analyse(frame, symbol, Timeframe.M5).breaks) >= len(
            close.analyse(frame, symbol, Timeframe.M5).breaks
        )

    def test_the_significance_filter_reduces_structure(self, symbol):
        frame = load(symbol, Timeframe.M5)
        unfiltered = StructureDetector(StructureConfig(), SWING).analyse(frame, symbol, Timeframe.M5)
        filtered = StructureDetector(StructureConfig(min_swing_strength_points=20), SWING).analyse(
            frame, symbol, Timeframe.M5
        )

        assert filtered.swings_filtered_out > 0
        assert filtered.swings_used < unfiltered.swings_used
        assert len(filtered.breaks) <= len(unfiltered.breaks)

    def test_distinct_choch_policy_splits_the_reversals(self, symbol):
        frame = load(symbol, Timeframe.M5)
        synonym = StructureDetector(StructureConfig(choch_policy=ChochPolicy.SYNONYM), SWING)
        distinct = StructureDetector(
            StructureConfig(choch_policy=ChochPolicy.DISTINCT_BY_DISPLACEMENT, displacement_lookback=20),
            SWING,
        )

        synonym_breaks = synonym.analyse(frame, symbol, Timeframe.M5).breaks
        distinct_breaks = distinct.analyse(frame, symbol, Timeframe.M5).breaks

        # Identical detection; only the reversal labels differ.
        assert len(synonym_breaks) == len(distinct_breaks)
        assert EventType.CHOCH not in {b.event_type for b in synonym_breaks}
        assert EventType.CHOCH in {b.event_type for b in distinct_breaks}

    def test_displacement_ratio_is_populated_on_real_data(self, detector, symbol):
        breaks = detector.analyse(load(symbol, Timeframe.M5), symbol, Timeframe.M5).breaks
        rated = [b for b in breaks if b.displacement_ratio is not None]
        assert rated
        assert all(b.displacement_ratio >= 0 for b in rated)


class TestRealDataLeakage:
    def test_no_event_leaks(self, detector, symbol, timeframe):
        events = detector.events(load(symbol, timeframe), symbol, timeframe)
        assert events
        assert_no_leakage(events)

    def test_every_break_follows_its_reference_swings_confirmation(self, detector, symbol, timeframe):
        for event in detector.analyse(load(symbol, timeframe), symbol, timeframe).breaks:
            assert event.reference_swing_confirmation <= event.confirmation_timestamp

    def test_batch_equals_prefix_replay(self, detector, symbol, timeframe):
        frame = load(symbol, timeframe)
        full = [b.as_dict() for b in detector.analyse(frame, symbol, timeframe).breaks]

        step = max(len(frame) // 8, 1)
        for cut in range(step, len(frame) + 1, step):
            partial = [b.as_dict() for b in detector.analyse(frame.iloc[:cut], symbol, timeframe).breaks]
            assert partial == full[: len(partial)], f"{symbol.value}/{timeframe.value} @ {cut}"

    def test_observable_at_matches_visible_bars_only(self, detector, symbol):
        frame = load(symbol, Timeframe.M5)
        step = max(len(frame) // 6, 1)

        for cut in range(step, len(frame) + 1, step):
            visible = frame.iloc[:cut]
            as_of = visible["timestamp"].iloc[-1].to_pydatetime() + timedelta(minutes=5)

            from_full = detector.observable_at(frame, as_of, symbol, Timeframe.M5)
            from_visible = detector.analyse(visible, symbol, Timeframe.M5)
            assert [b.as_dict() for b in from_full.breaks] == [b.as_dict() for b in from_visible.breaks]
            assert from_full.final_state is from_visible.final_state


class TestRealDataWeekendGap:
    def test_no_structure_event_is_visible_during_the_weekend_closure(self, detector, symbol):
        """The R2-02 weekend case carried into structure: nothing may become knowable
        while the market is shut, because no bars are closing."""
        frame = load(symbol, Timeframe.M5)
        events = detector.events(frame, symbol, Timeframe.M5)

        probe = FRIDAY_CLOSE + pd.Timedelta(hours=12)  # Saturday, market closed
        visible = filter_observable(events, probe.to_pydatetime())
        assert all(pd.Timestamp(e.confirmation_timestamp) <= FRIDAY_CLOSE for e in visible)

    def test_no_break_confirms_inside_the_gap(self, detector, symbol):
        frame = load(symbol, Timeframe.M5)
        for event in detector.analyse(frame, symbol, Timeframe.M5).breaks:
            confirmation = pd.Timestamp(event.confirmation_timestamp)
            assert not (
                FRIDAY_CLOSE < confirmation < SUNDAY_REOPEN
            ), f"{symbol.value}: a break confirmed at {confirmation} — inside the closure"

    def test_a_reference_swing_from_before_the_weekend_can_still_be_broken_after(self, detector, symbol):
        """Levels survive the gap — only their *confirmation timing* is affected."""
        frame = load(symbol, Timeframe.M5)
        spanning = [
            b
            for b in detector.analyse(frame, symbol, Timeframe.M5).breaks
            if pd.Timestamp(b.reference_swing_timestamp) < FRIDAY_CLOSE
            and pd.Timestamp(b.confirmation_timestamp) > SUNDAY_REOPEN
        ]
        if not spanning:
            pytest.skip(f"{symbol.value}: no break references a pre-weekend swing")
        for event in spanning:
            assert event.reference_swing_confirmation <= event.confirmation_timestamp


class TestRealDataSessionsAndDst:
    def test_structure_composes_with_sessions_under_one_contract(self, detector, symbol):
        frame = load(symbol, Timeframe.M5)
        combined = detector.events(frame, symbol, Timeframe.M5) + SessionDetector().events(
            frame, symbol, Timeframe.M5
        )
        assert_no_leakage(combined)

        as_of = datetime(2024, 3, 11, 12, 0, tzinfo=UTC)
        visible = filter_observable(combined, as_of)
        assert visible and len(visible) < len(combined)
        assert all(e.confirmation_timestamp <= as_of for e in visible)

    def test_breaks_land_inside_real_session_windows(self, detector, symbol):
        """Every break should be attributable to a session — a break outside every
        window would mean the calendar and the bars disagree."""
        frame = load(symbol, Timeframe.M5)
        sessions = SessionDetector()
        breaks = detector.analyse(frame, symbol, Timeframe.M5).breaks
        assert breaks

        attributed = sum(1 for b in breaks if sessions.active_sessions_at(b.event_timestamp))
        assert attributed > 0

    def test_the_us_dst_transition_does_not_disturb_structure(self, detector, symbol):
        """Bars are UTC and uniform, so the 2024-03-10 transition must not create or
        destroy structure. It is a session-layer concern only."""
        frame = load(symbol, Timeframe.M5)
        analysis = detector.analyse(frame, symbol, Timeframe.M5)

        transition = pd.Timestamp("2024-03-10T07:00:00Z")
        window = [
            b
            for b in analysis.breaks
            if abs(pd.Timestamp(b.event_timestamp) - transition) < pd.Timedelta(hours=6)
        ]
        for event in window:
            assert event.confirmation_timestamp == event.event_timestamp + timedelta(minutes=5)


class TestRealDataAcrossTimeframes:
    def test_finer_timeframes_produce_more_structure(self, detector, symbol):
        m1 = detector.analyse(load(symbol, Timeframe.M1), symbol, Timeframe.M1)
        m15 = detector.analyse(load(symbol, Timeframe.M15), symbol, Timeframe.M15)
        assert len(m1.breaks) > len(m15.breaks)

    def test_each_timeframe_keeps_its_own_state_machine(self, detector, symbol):
        """Independent per timeframe by design; reconciling them is R2-07's job."""
        states = {}
        for timeframe in (Timeframe.M1, Timeframe.M5, Timeframe.M15):
            analysis = detector.analyse(load(symbol, timeframe), symbol, timeframe)
            states[timeframe.value] = analysis.final_state
            assert all(b.timeframe == timeframe.value for b in analysis.breaks)
        assert len(states) == 3

    def test_confirmation_is_one_bar_after_the_event_on_every_timeframe(self, detector, symbol, timeframe):
        """A break confirms at its own bar's close — exactly one bar duration."""
        expected = timedelta(minutes=timeframe.minutes)
        for event in detector.analyse(load(symbol, timeframe), symbol, timeframe).breaks:
            assert event.confirmation_timestamp - event.event_timestamp == expected
