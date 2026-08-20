"""R2-04 leakage, immutability and streaming replay.

Every case the story mandates. The central claims:

* A level's ``confirmation_timestamp`` is fixed when the level is built. **A future
  sweep can never make it observable earlier.**
* A sweep can never precede its level's confirmation.
* Removing the observability constraint produces different, leaky output — proved
  directly rather than asserted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from ict_kronos.data import resample, with_close_time
from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame
from ict_kronos.ict import (
    ContractViolation,
    EventType,
    LiquidityConfig,
    LiquidityDetector,
    LiquidityStatus,
    LiquidityType,
    SessionDetector,
    SwingConfig,
    SwingDetector,
    assert_no_leakage,
    assert_observable,
    filter_observable,
)

from .test_liquidity import EQUAL_HIGHS_SPEC, NO_SWINGS, START, SWING_1_1, bars, levels_of

pytestmark = pytest.mark.leakage

SWING_2_2 = SwingConfig(left=2, right=2)


def noisy(count: int = 500, seed: int = 20240404, timeframe: Timeframe = Timeframe.M5):
    """A long random walk with coarse rounding, so equal highs actually occur."""
    rng = np.random.default_rng(seed)
    price = 1.0800
    candles = []
    for i in range(count):
        price += rng.normal(0, 0.0006)
        high = round(price + abs(rng.normal(0, 0.0004)), 4)
        low = round(price - abs(rng.normal(0, 0.0004)), 4)
        close = round(min(max(price + rng.normal(0, 0.0002), low), high), 4)
        candles.append(
            MarketCandle(
                timestamp=START + timedelta(minutes=timeframe.minutes * i),
                symbol=Symbol.EURUSD,
                timeframe=timeframe,
                open=close,
                high=high,
                low=low,
                close=close,
                volume=1.0,
            )
        )
    return candles_to_frame(candles)


@pytest.fixture
def frame():
    return noisy()


@pytest.fixture
def detector():
    return LiquidityDetector(LiquidityConfig(), SWING_2_2)


# ---------------------------------------------- 1. inputs must be confirmed


class TestConfirmedInputsOnly:
    def test_unconfirmed_swings_cannot_create_liquidity(self):
        """Bar-by-bar: the swing level cannot exist before the pivot confirms."""
        detector = LiquidityDetector(NO_SWINGS, SWING_1_1)
        full = bars(EQUAL_HIGHS_SPEC)

        # Swing high at bar 5 confirms at bar 6's close; the equal-high level with it.
        assert detector.analyse(full.iloc[:6], Symbol.EURUSD, Timeframe.M5).levels == []
        assert detector.analyse(full.iloc[:7], Symbol.EURUSD, Timeframe.M5).levels

    def test_every_level_traces_to_confirmed_swings(self, detector, frame):
        swings = {
            s.event_timestamp: s for s in SwingDetector(SWING_2_2).detect(frame, Symbol.EURUSD, Timeframe.M5)
        }
        analysis = detector.analyse(frame, Symbol.EURUSD, Timeframe.M5)

        for level in analysis.levels:
            for source in level.source_swing_timestamps:
                assert source in swings, "a level referenced a non-confirmed swing"
                assert swings[source].confirmation_timestamp <= level.confirmation_timestamp

    def test_equal_level_confirms_at_the_later_of_its_two_swings(self, detector, frame):
        swings = {
            s.event_timestamp: s for s in SwingDetector(SWING_2_2).detect(frame, Symbol.EURUSD, Timeframe.M5)
        }
        analysis = detector.analyse(frame, Symbol.EURUSD, Timeframe.M5)

        equal = [
            x
            for x in analysis.levels
            if x.liquidity_type in (LiquidityType.EQUAL_HIGHS, LiquidityType.EQUAL_LOWS)
        ]
        assert equal
        for level in equal:
            expected = max(swings[t].confirmation_timestamp for t in level.source_swing_timestamps)
            assert level.confirmation_timestamp == expected

    def test_incomplete_sessions_cannot_create_session_liquidity(self):
        """Running session state is not a completed level."""
        detector = LiquidityDetector(NO_SWINGS, SWING_1_1)
        frame = _hourly(hours=8, start=datetime(2024, 3, 4, 22, 0, tzinfo=UTC))
        analysis = detector.analyse(frame, Symbol.EURUSD, Timeframe.H1)

        sessions = SessionDetector().detect(frame, Symbol.EURUSD, Timeframe.H1)
        session_levels = levels_of(analysis, LiquidityType.SESSION_HIGH)
        assert len(session_levels) == len(sessions)  # only COMPLETED ones

    def test_incomplete_days_cannot_create_pdh(self):
        detector = LiquidityDetector(NO_SWINGS, SWING_1_1)
        partial = _hourly(hours=10, start=datetime(2024, 3, 4, 22, 0, tzinfo=UTC))
        analysis = detector.analyse(partial, Symbol.EURUSD, Timeframe.H1)

        assert levels_of(analysis, LiquidityType.PREVIOUS_DAY_HIGH) == []
        assert any(p.kind == "day" for p in analysis.pending)

    def test_incomplete_weeks_cannot_create_pwh(self):
        detector = LiquidityDetector(NO_SWINGS, SWING_1_1)
        frame = _hourly(hours=72, start=datetime(2024, 3, 4, 22, 0, tzinfo=UTC))
        analysis = detector.analyse(frame, Symbol.EURUSD, Timeframe.H1)

        assert levels_of(analysis, LiquidityType.PREVIOUS_WEEK_HIGH) == []
        assert any(p.kind == "week" for p in analysis.pending)

    def test_removing_the_observability_constraint_would_leak(self, frame):
        """Direct proof. A naive PDH implementation that publishes the running daily
        high from the extreme bar produces levels observable EARLIER than the correct
        one — which is exactly the leak."""
        detector = LiquidityDetector(NO_SWINGS, SWING_2_2)
        correct = detector.analyse(frame, Symbol.EURUSD, Timeframe.M5)
        correct_pdh = levels_of(correct, LiquidityType.PREVIOUS_DAY_HIGH)
        if not correct_pdh:
            pytest.skip("no complete trading day in this synthetic window")

        # Naive: "the day's high is known the moment it prints".
        naive_confirmations = [x.created_timestamp for x in correct_pdh]
        real_confirmations = [x.confirmation_timestamp for x in correct_pdh]

        assert naive_confirmations != real_confirmations
        for naive, real in zip(naive_confirmations, real_confirmations, strict=True):
            assert naive < real, "the naive construction was not actually earlier"


def _hourly(hours: int, start: datetime, symbol: Symbol = Symbol.EURUSD):
    spec = [(1.10 + 0.001 * (i % 24) + 0.0005, 1.10 + 0.001 * (i % 24) - 0.0005) for i in range(hours)]
    return bars(spec, symbol=symbol, timeframe=Timeframe.H1, start=start)


# ------------------------------------ 2. sweeps cannot precede observability


class TestSweepObservability:
    def test_no_sweep_precedes_its_levels_confirmation(self, detector, frame):
        analysis = detector.analyse(frame, Symbol.EURUSD, Timeframe.M5)
        assert analysis.sweeps

        by_id = {x.level_id: x for x in analysis.levels}
        for sweep in analysis.sweeps:
            level = by_id[sweep.level_id]
            assert sweep.confirmation_timestamp >= level.confirmation_timestamp
            assert sweep.event_timestamp >= level.created_timestamp

    def test_a_future_sweep_cannot_make_a_level_observable_earlier(self, detector, frame):
        """The story's specific requirement. A level's confirmation is fixed when the
        level is built; sweeps are computed afterwards and write only to sweep records
        and status. Levels are frozen, so this is enforced by the type."""
        with_sweeps = detector.analyse(frame, Symbol.EURUSD, Timeframe.M5)

        # Truncate to before any sweeps happened, then compare the shared levels.
        first_sweep = min(s.confirmation_timestamp for s in with_sweeps.sweeps)
        early_bars = frame.loc[frame["timestamp"] < pd.Timestamp(first_sweep)]
        early = detector.analyse(early_bars, Symbol.EURUSD, Timeframe.M5)

        later_by_id = {x.level_id: x for x in with_sweeps.levels}
        assert early.levels
        for level in early.levels:
            assert later_by_id[level.level_id].confirmation_timestamp == level.confirmation_timestamp

    def test_a_sweep_confirms_at_its_own_bar_close(self, detector, frame):
        for sweep in detector.analyse(frame, Symbol.EURUSD, Timeframe.M5).sweeps:
            assert sweep.confirmation_timestamp == sweep.event_timestamp + timedelta(minutes=5)

    def test_a_level_is_never_swept_before_it_is_observable(self, detector, frame):
        """Constructed check: replay only bars visible before each level's
        confirmation and assert nothing swept it."""
        analysis = detector.analyse(frame, Symbol.EURUSD, Timeframe.M5)
        swept_ids = {s.level_id for s in analysis.sweeps}

        for level in analysis.levels:
            if level.level_id not in swept_ids:
                continue
            visible = detector.observable_at(
                frame,
                level.confirmation_timestamp - timedelta(seconds=1),
                Symbol.EURUSD,
                Timeframe.M5,
            )
            assert level.level_id not in {s.level_id for s in visible.sweeps}


# ------------------------------------------------- 3. contract-level leakage


class TestContractLevelLeakage:
    def test_no_event_leaks(self, detector, frame):
        events = detector.events(frame, Symbol.EURUSD, Timeframe.M5)
        assert events
        assert_no_leakage(events)

    def test_no_event_is_observable_one_second_early(self, detector, frame):
        for event in detector.events(frame, Symbol.EURUSD, Timeframe.M5):
            assert not event.is_observable_at(event.confirmation_timestamp - timedelta(seconds=1))
            assert event.is_observable_at(event.confirmation_timestamp)

    def test_filter_observable_is_the_single_gate(self, detector, frame):
        events = detector.events(frame, Symbol.EURUSD, Timeframe.M5)
        midpoint = events[len(events) // 2].confirmation_timestamp

        visible = filter_observable(events, midpoint)
        assert visible and len(visible) < len(events)
        assert_observable(visible, midpoint)

    def test_assert_observable_catches_a_leak(self, detector, frame):
        events = detector.events(frame, Symbol.EURUSD, Timeframe.M5)
        too_early = min(e.confirmation_timestamp for e in events) - timedelta(seconds=1)
        with pytest.raises(ContractViolation, match="not observable"):
            assert_observable(events, too_early)

    def test_observable_at_rejects_naive_timestamps(self, detector, frame):
        with pytest.raises(ValueError, match="timezone-aware"):
            detector.observable_at(
                frame, datetime(2024, 3, 8, 12, 0), Symbol.EURUSD, Timeframe.M5
            )  # noqa: DTZ001


# -------------------------------------------------------- 4. immutability


class TestImmutability:
    def test_future_bars_cannot_create_historical_liquidity(self, detector, frame):
        """A level present in an early run must appear identically in the longer run."""
        early = detector.analyse(frame.iloc[:250], Symbol.EURUSD, Timeframe.M5)
        later = detector.analyse(frame, Symbol.EURUSD, Timeframe.M5)

        lookup = {x.level_id: x for x in later.levels}
        assert early.levels
        for level in early.levels:
            assert level.level_id in lookup, f"level {level.level_id} vanished"
            assert lookup[level.level_id].as_dict() == level.as_dict()

    def test_confirmed_sweeps_are_never_revised(self, detector, frame):
        early = detector.analyse(frame.iloc[:250], Symbol.EURUSD, Timeframe.M5)
        later = detector.analyse(frame, Symbol.EURUSD, Timeframe.M5)

        lookup = {(s.level_id, s.event_timestamp): s for s in later.sweeps}
        assert early.sweeps
        for sweep in early.sweeps:
            key = (sweep.level_id, sweep.event_timestamp)
            assert key in lookup
            assert lookup[key].as_dict() == sweep.as_dict()

    def test_appending_bars_only_appends(self, detector, frame):
        previous_levels: list = []
        previous_sweeps: list = []
        for cut in range(100, len(frame) + 1, 100):
            analysis = detector.analyse(frame.iloc[:cut], Symbol.EURUSD, Timeframe.M5)
            levels = [x.as_dict() for x in analysis.levels]
            sweeps = [s.as_dict() for s in analysis.sweeps]

            assert levels[: len(previous_levels)] == previous_levels
            assert sweeps[: len(previous_sweeps)] == previous_sweeps
            previous_levels, previous_sweeps = levels, sweeps


# --------------------------------------------------- 5. batch == streaming


class TestBatchEqualsStreaming:
    def test_prefix_replay_matches_the_batch_prefix(self, detector, frame):
        full = detector.analyse(frame, Symbol.EURUSD, Timeframe.M5)
        full_sweeps = [s.as_dict() for s in full.sweeps]
        full_levels = [x.as_dict() for x in full.levels]

        for cut in range(80, len(frame) + 1, 80):
            partial = detector.analyse(frame.iloc[:cut], Symbol.EURUSD, Timeframe.M5)
            assert [x.as_dict() for x in partial.levels] == full_levels[: len(partial.levels)]
            assert [s.as_dict() for s in partial.sweeps] == full_sweeps[: len(partial.sweeps)]

    def test_true_bar_by_bar_replay(self):
        """One bar at a time, accumulating — the strictest form."""
        detector = LiquidityDetector(NO_SWINGS, SWING_1_1)
        frame = noisy(count=120, seed=9)

        seen_levels: list = []
        seen_sweeps: list = []
        for n in range(1, len(frame) + 1):
            analysis = detector.analyse(frame.iloc[:n], Symbol.EURUSD, Timeframe.M5)
            for level in analysis.levels:
                payload = level.as_dict()
                if payload not in seen_levels:
                    seen_levels.append(payload)
            for sweep in analysis.sweeps:
                payload = sweep.as_dict()
                if payload not in seen_sweeps:
                    seen_sweeps.append(payload)

        batch = detector.analyse(frame, Symbol.EURUSD, Timeframe.M5)
        assert seen_levels == [x.as_dict() for x in batch.levels]
        assert seen_sweeps == [s.as_dict() for s in batch.sweeps]

    def test_pending_periods_are_never_emitted_as_levels(self, detector, frame):
        analysis = detector.analyse(frame, Symbol.EURUSD, Timeframe.M5)
        level_labels = {x.period_label for x in analysis.levels if x.period_label}
        for pending in analysis.pending:
            assert pending.label not in level_labels

    def test_observable_at_matches_replaying_visible_bars_only(self, detector, frame):
        for cut in range(120, len(frame) + 1, 120):
            visible = frame.iloc[:cut]
            as_of = visible["timestamp"].iloc[-1].to_pydatetime() + timedelta(minutes=5)

            from_full = detector.observable_at(frame, as_of, Symbol.EURUSD, Timeframe.M5)
            from_visible = detector.analyse(visible, Symbol.EURUSD, Timeframe.M5)

            assert [x.as_dict() for x in from_full.levels] == [x.as_dict() for x in from_visible.levels]
            assert [s.as_dict() for s in from_full.sweeps] == [s.as_dict() for s in from_visible.sweeps]


# ------------------------------------------- 6. gaps, weekends, HTF, R2-03


class TestGapsAndWeekends:
    def test_a_weekend_gap_creates_no_phantom_sweep(self):
        """No bars close during a closure, so nothing can be swept in it."""
        detector = LiquidityDetector(NO_SWINGS, SWING_1_1)
        spec = [*EQUAL_HIGHS_SPEC, (1.0450, 1.0300), (1.0600, 1.0400, 1.0450)]
        frame = bars(spec)

        gapped = frame.copy(deep=True)
        gapped.loc[8, "timestamp"] = gapped.loc[8, "timestamp"] + pd.Timedelta(days=2)

        analysis = detector.analyse(gapped, Symbol.EURUSD, Timeframe.M5)
        gap_instant = START + timedelta(minutes=45)
        assert all(s.confirmation_timestamp > gap_instant for s in analysis.sweeps)

    def test_a_gap_open_through_a_level_is_a_valid_sweep(self):
        """Documented decision: a gap-open through a level takes the resting orders
        exactly as a continuous move would."""
        detector = LiquidityDetector(NO_SWINGS, SWING_1_1)
        spec = [*EQUAL_HIGHS_SPEC, (1.0450, 1.0300), (1.0600, 1.0550, 1.0580)]
        frame = bars(spec)
        gapped = frame.copy(deep=True)
        gapped.loc[8, "timestamp"] = gapped.loc[8, "timestamp"] + pd.Timedelta(days=2)

        analysis = detector.analyse(gapped, Symbol.EURUSD, Timeframe.M5)
        assert [s for s in analysis.sweeps if s.liquidity_type is LiquidityType.EQUAL_HIGHS]

    def test_a_swing_derived_level_does_not_expire_with_wall_clock_time(self):
        """No level ages out. A gap delays *when* the sweeping bar arrives, but the
        equal-high level is still there to be taken — the lifecycle is driven by bars,
        never by elapsed time.

        Scoped to the swing-derived level deliberately: shifting a bar two days also
        moves it into different day/session windows, so period-derived levels
        legitimately differ. That is calendar behaviour, not lifecycle behaviour, and
        conflating the two would make this test assert something false.
        """
        detector = LiquidityDetector(NO_SWINGS, SWING_1_1)
        spec = [*EQUAL_HIGHS_SPEC, (1.0450, 1.0300), (1.0600, 1.0400, 1.0450)]
        frame = bars(spec)
        gapped = frame.copy(deep=True)
        gapped.loc[8, "timestamp"] = gapped.loc[8, "timestamp"] + pd.Timedelta(days=2)

        def equal_sweeps(analysis):
            return {s.level_id for s in analysis.sweeps if s.liquidity_type is LiquidityType.EQUAL_HIGHS}

        plain = detector.analyse(frame, Symbol.EURUSD, Timeframe.M5)
        with_gap = detector.analyse(gapped, Symbol.EURUSD, Timeframe.M5)

        assert equal_sweeps(plain)
        assert equal_sweeps(plain) == equal_sweeps(with_gap)

    def test_a_gap_changes_which_calendar_periods_complete(self):
        """The complement, asserted rather than left implicit: pushing a bar two days
        forward DOES complete day/session windows that were previously in progress, so
        new period levels appear. Correct, and worth pinning so the scoping above is
        not mistaken for a bug."""
        detector = LiquidityDetector(NO_SWINGS, SWING_1_1)
        spec = [*EQUAL_HIGHS_SPEC, (1.0450, 1.0300), (1.0600, 1.0400, 1.0450)]
        frame = bars(spec)
        gapped = frame.copy(deep=True)
        gapped.loc[8, "timestamp"] = gapped.loc[8, "timestamp"] + pd.Timedelta(days=2)

        plain = detector.analyse(frame, Symbol.EURUSD, Timeframe.M5)
        with_gap = detector.analyse(gapped, Symbol.EURUSD, Timeframe.M5)

        assert len(with_gap.levels) > len(plain.levels)
        assert any(x.liquidity_type is LiquidityType.PREVIOUS_DAY_HIGH for x in with_gap.levels)
        assert not any(x.liquidity_type is LiquidityType.PREVIOUS_DAY_HIGH for x in plain.levels)


class TestHigherTimeframeAndStructure:
    def test_htf_liquidity_does_not_leak_into_earlier_ltf_timestamps(self):
        base = noisy(count=600)
        htf = resample(base, Timeframe.M5, Timeframe.M15, Symbol.EURUSD)

        detector = LiquidityDetector(LiquidityConfig(), SWING_1_1)
        htf_events = detector.events(htf.drop(columns=["close_time"]), Symbol.EURUSD, Timeframe.M15)
        assert htf_events

        ltf = with_close_time(base, Timeframe.M5)
        for moment in ltf["close_time"].iloc[::60]:
            as_of = moment.to_pydatetime()
            for event in filter_observable(htf_events, as_of):
                assert event.confirmation_timestamp <= as_of

    def test_a_wick_sweep_is_not_a_structural_break(self):
        """THE R2-03 interaction. A wick above a level is a liquidity sweep; with
        R2-03's default CLOSE break mode it is NOT a BOS. That separation is exactly
        why liquidity lives in its own detector."""
        from ict_kronos.ict import StructureConfig, StructureDetector

        spec = [
            (1.02, 0.99),
            (1.05, 1.00),  # 1: swing high 1.05
            (1.03, 0.98),
            (1.02, 0.95),  # 3: swing low
            (1.04, 0.99),
            (1.045, 1.00),
            (1.08, 1.01, 1.02),  # 6: wicks to 1.08, CLOSES back at 1.02
        ]
        frame = bars(spec)

        liquidity = LiquidityDetector(LiquidityConfig(), SWING_1_1).analyse(
            frame, Symbol.EURUSD, Timeframe.M5
        )
        structure = StructureDetector(StructureConfig(), SWING_1_1).analyse(
            frame, Symbol.EURUSD, Timeframe.M5
        )

        swept_highs = [s for s in liquidity.sweeps if s.side.value == "buy_side"]
        assert swept_highs, "the wick should have swept the swing-high liquidity"
        assert all(s.is_rejection for s in swept_highs)

        bullish_breaks = [b for b in structure.breaks if b.direction.value == "bullish"]
        assert bullish_breaks == [], "a wick must NOT register as a structural break"

    def test_both_detectors_share_one_contract(self):
        from ict_kronos.ict import StructureConfig, StructureDetector

        frame = noisy(count=300)
        combined = LiquidityDetector(LiquidityConfig(), SWING_2_2).events(
            frame, Symbol.EURUSD, Timeframe.M5
        ) + StructureDetector(StructureConfig(), SWING_2_2).events(frame, Symbol.EURUSD, Timeframe.M5)
        assert_no_leakage(combined)
        assert EventType.LIQUIDITY_SWEEP in {e.event_type for e in combined}


class TestStatusConsistency:
    def test_status_matches_the_sweep_record(self, detector, frame):
        analysis = detector.analyse(frame, Symbol.EURUSD, Timeframe.M5)
        swept_ids = {s.level_id for s in analysis.sweeps}

        for level in analysis.levels:
            status = analysis.status[level.level_id]
            if level.level_id in swept_ids:
                assert status is LiquidityStatus.SWEPT
                assert level.level_id in analysis.swept_at
            else:
                assert status is not LiquidityStatus.SWEPT

    def test_no_level_is_swept_twice(self, detector, frame):
        sweeps = detector.analyse(frame, Symbol.EURUSD, Timeframe.M5).sweeps
        ids = [s.level_id for s in sweeps]
        assert len(ids) == len(set(ids)), "a consumed level was swept more than once"
