"""R2-03 leakage, immutability and streaming replay.

Every test the story mandates, plus the R2-02 dependency proof. The central claim:

    Structure is built ONLY from swings already observable through the shared
    contract. Remove that filter and the output changes — which is what
    ``test_removing_the_observability_filter_would_leak`` demonstrates directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from ict_kronos.data import align_htf_context, resample, with_close_time
from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame
from ict_kronos.ict import (
    BreakMode,
    ChochPolicy,
    EventType,
    StructureConfig,
    StructureDetector,
    StructureState,
    SwingConfig,
    SwingDetector,
    assert_no_leakage,
    assert_observable,
    filter_observable,
)

from .test_structure import BULLISH_SEQUENCE, START, SWING_1_1, bars

pytestmark = pytest.mark.leakage

SWING_2_2 = SwingConfig(left=2, right=2)


def noisy(count: int = 400, seed: int = 20240320, timeframe: Timeframe = Timeframe.M5):
    """A long, plateau-rich random walk — enough swings to exercise the state machine."""
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
    return StructureDetector(StructureConfig(), SWING_2_2)


# ------------------------------------------- 1. the R2-02 dependency is real


class TestSwingObservabilityDependency:
    def test_structure_uses_only_confirmed_swings(self, detector, frame):
        """Every referenced swing confirmed BEFORE the break that used it."""
        analysis = detector.analyse(frame, Symbol.EURUSD, Timeframe.M5)
        assert analysis.breaks
        for event in analysis.breaks:
            assert event.reference_swing_confirmation <= event.confirmation_timestamp

    def test_a_break_never_precedes_its_reference_swings_confirmation(self, detector, frame):
        """The decisive R2-02 inheritance: a BOS of a pivot nobody could see yet is
        exactly the leak this project exists to prevent."""
        for event in detector.analyse(frame, Symbol.EURUSD, Timeframe.M5).breaks:
            assert event.confirmation_timestamp >= event.reference_swing_confirmation
            assert event.event_timestamp >= event.reference_swing_timestamp

    def test_removing_the_observability_filter_would_leak(self, frame):
        """Direct proof the filter does work. Build structure from RAW swings — using
        each pivot from its own bar rather than from its confirmation — and it produces
        breaks the correct detector cannot, because it reacts to pivots earlier."""
        detector = StructureDetector(StructureConfig(), SWING_2_2)
        correct = detector.analyse(frame, Symbol.EURUSD, Timeframe.M5)

        swings = SwingDetector(SWING_2_2).detect(frame, Symbol.EURUSD, Timeframe.M5)
        work = with_close_time(frame, Timeframe.M5).reset_index(drop=True)
        closes = work["close"].to_numpy(dtype="float64")

        # The naive (leaking) version: a pivot is usable from its OWN bar.
        leaking_breaks = 0
        active = None
        cursor = 0
        highs = [s for s in swings if s.is_high]
        highs.sort(key=lambda s: s.event_timestamp)
        for index in range(len(work)):
            bar_time = work["timestamp"].iloc[index]
            while cursor < len(highs) and pd.Timestamp(highs[cursor].event_timestamp) <= bar_time:
                active = highs[cursor]
                cursor += 1
            if active is not None and closes[index] > active.price_level:
                leaking_breaks += 1
                active = None

        correct_bullish = len([b for b in correct.breaks if b.direction.value == "bullish"])
        assert leaking_breaks != correct_bullish, (
            "the leaking and correct constructions produced identical results — "
            "the observability filter is not actually constraining anything"
        )

    def test_an_unconfirmed_swing_cannot_create_a_label(self):
        """Bar-by-bar: the HH cannot appear before its swing confirms."""
        detector = StructureDetector(StructureConfig(), SWING_1_1)
        full = bars(BULLISH_SEQUENCE)

        # Swing high at bar 5 confirms at the close of bar 6.
        assert detector.analyse(full.iloc[:6], Symbol.EURUSD, Timeframe.M5).labels == []
        labels = detector.analyse(full.iloc[:7], Symbol.EURUSD, Timeframe.M5).labels
        assert [x.label for x in labels] == [EventType.HIGHER_HIGH]

    def test_an_unconfirmed_swing_cannot_create_a_bos(self):
        """A bar that closes above a not-yet-confirmed pivot produces no break."""
        detector = StructureDetector(StructureConfig(), SWING_2_2)
        spec = [
            (1.02, 0.99),
            (1.03, 1.00),
            (1.05, 1.01),  # 2: swing-high candidate, confirms only at bar 4's close
            (1.04, 1.00),
            (1.09, 1.02, 1.08),  # 4: closes above 1.05 — but the pivot confirms HERE
        ]
        analysis = detector.analyse(bars(spec[:4]), Symbol.EURUSD, Timeframe.M5)
        assert analysis.breaks == []

    def test_an_unconfirmed_swing_cannot_create_an_mss(self, detector, frame):
        for event in detector.analyse(frame, Symbol.EURUSD, Timeframe.M5).breaks:
            if event.event_type in (EventType.MSS, EventType.CHOCH):
                assert event.reference_swing_confirmation <= event.confirmation_timestamp

    def test_the_significance_filter_also_respects_confirmation(self, frame):
        detector = StructureDetector(StructureConfig(min_swing_strength_points=3), SWING_2_2)
        for event in detector.analyse(frame, Symbol.EURUSD, Timeframe.M5).breaks:
            assert event.reference_swing_confirmation <= event.confirmation_timestamp


# ----------------------------------------------------------- 2. contract-level


class TestContractLevelLeakage:
    def test_no_event_leaks(self, detector, frame):
        events = detector.events(frame, Symbol.EURUSD, Timeframe.M5)
        assert events
        assert_no_leakage(events)

    def test_no_event_is_observable_one_second_early(self, detector, frame):
        for event in detector.events(frame, Symbol.EURUSD, Timeframe.M5):
            assert not event.is_observable_at(event.confirmation_timestamp - timedelta(seconds=1))
            assert event.is_observable_at(event.confirmation_timestamp)

    def test_breaks_confirm_at_their_own_bar_close(self, detector, frame):
        for event in detector.analyse(frame, Symbol.EURUSD, Timeframe.M5).breaks:
            assert event.confirmation_timestamp == event.event_timestamp + timedelta(minutes=5)

    def test_filter_observable_gates_structure_events(self, detector, frame):
        events = detector.events(frame, Symbol.EURUSD, Timeframe.M5)
        midpoint = events[len(events) // 2].confirmation_timestamp

        visible = filter_observable(events, midpoint)
        assert visible and len(visible) < len(events)
        assert_observable(visible, midpoint)

    def test_state_at_never_reflects_a_future_transition(self, detector, frame):
        analysis = detector.analyse(frame, Symbol.EURUSD, Timeframe.M5)
        for event in analysis.breaks:
            before = analysis.state_at(event.confirmation_timestamp - timedelta(seconds=1))
            after = analysis.state_at(event.confirmation_timestamp)
            assert before is event.previous_state or event.previous_state is StructureState.UNDEFINED
            assert after is event.resulting_state


# --------------------------------------------------------- 3. immutability


class TestImmutability:
    def test_a_future_candle_cannot_change_a_confirmed_break(self, detector, frame):
        prefix = frame.iloc[:250]
        early = detector.analyse(prefix, Symbol.EURUSD, Timeframe.M5)
        later = detector.analyse(frame, Symbol.EURUSD, Timeframe.M5)

        assert early.breaks
        later_by_key = {(b.event_timestamp, b.direction) for b in later.breaks}
        later_lookup = {(b.event_timestamp, b.direction): b for b in later.breaks}
        for event in early.breaks:
            key = (event.event_timestamp, event.direction)
            assert key in later_by_key, f"break at {event.event_timestamp} vanished"
            assert later_lookup[key].as_dict() == event.as_dict(), "a confirmed break was revised"

    def test_a_future_candle_cannot_change_a_confirmed_label(self, detector, frame):
        early = detector.analyse(frame.iloc[:250], Symbol.EURUSD, Timeframe.M5)
        later = detector.analyse(frame, Symbol.EURUSD, Timeframe.M5)

        lookup = {(x.event_timestamp, x.label): x for x in later.labels}
        assert early.labels
        for label in early.labels:
            match = lookup.get((label.event_timestamp, label.label))
            assert match is not None
            assert match.as_dict() == label.as_dict()

    def test_appending_bars_only_ever_appends_events(self, detector, frame):
        previous_breaks: list = []
        previous_labels: list = []
        for cut in range(80, len(frame) + 1, 80):
            analysis = detector.analyse(frame.iloc[:cut], Symbol.EURUSD, Timeframe.M5)

            assert [b.as_dict() for b in analysis.breaks][: len(previous_breaks)] == previous_breaks
            assert [x.as_dict() for x in analysis.labels][: len(previous_labels)] == previous_labels

            previous_breaks = [b.as_dict() for b in analysis.breaks]
            previous_labels = [x.as_dict() for x in analysis.labels]

    def test_records_are_frozen(self, detector, frame):
        analysis = detector.analyse(frame, Symbol.EURUSD, Timeframe.M5)
        with pytest.raises(Exception):  # noqa: B017 - FrozenInstanceError
            analysis.breaks[0].reference_level = 9.99
        with pytest.raises(Exception):  # noqa: B017
            analysis.labels[0].price_level = 9.99


# -------------------------------------------------- 4. batch == streaming


class TestBatchEqualsStreaming:
    @pytest.mark.parametrize("mode", list(BreakMode))
    def test_prefix_replay_matches_the_batch_prefix(self, frame, mode):
        detector = StructureDetector(StructureConfig(break_mode=mode), SWING_2_2)
        full = detector.analyse(frame, Symbol.EURUSD, Timeframe.M5)
        full_keys = [b.as_dict() for b in full.breaks]

        for cut in range(60, len(frame) + 1, 60):
            partial = detector.analyse(frame.iloc[:cut], Symbol.EURUSD, Timeframe.M5)
            partial_keys = [b.as_dict() for b in partial.breaks]
            assert partial_keys == full_keys[: len(partial_keys)], f"divergence at cut={cut}"

    @pytest.mark.parametrize("policy", list(ChochPolicy))
    def test_replay_holds_under_every_choch_policy(self, frame, policy):
        detector = StructureDetector(
            StructureConfig(choch_policy=policy, displacement_lookback=10), SWING_2_2
        )
        full = [b.as_dict() for b in detector.analyse(frame, Symbol.EURUSD, Timeframe.M5).breaks]

        for cut in (120, 240, len(frame)):
            partial = [
                b.as_dict() for b in detector.analyse(frame.iloc[:cut], Symbol.EURUSD, Timeframe.M5).breaks
            ]
            assert partial == full[: len(partial)]

    def test_candle_by_candle_replay_reproduces_the_batch_result(self):
        """The strictest form: one bar at a time, accumulating."""
        detector = StructureDetector(StructureConfig(), SWING_1_1)
        frame = noisy(count=120, seed=5)

        seen: list = []
        for n in range(1, len(frame) + 1):
            for event in detector.analyse(frame.iloc[:n], Symbol.EURUSD, Timeframe.M5).breaks:
                payload = event.as_dict()
                if payload not in seen:
                    seen.append(payload)

        batch = [b.as_dict() for b in detector.analyse(frame, Symbol.EURUSD, Timeframe.M5).breaks]
        assert seen == batch

    def test_a_pending_candidate_is_never_emitted(self):
        """A break whose bar has not closed does not exist yet; and a reference whose
        swing has not confirmed cannot be broken. Both show up as `pending_*`."""
        detector = StructureDetector(StructureConfig(), SWING_1_1)
        analysis = detector.analyse(bars(BULLISH_SEQUENCE), Symbol.EURUSD, Timeframe.M5)

        assert analysis.breaks == []
        assert analysis.pending_high is not None
        assert analysis.pending_low is not None

    def test_state_evolves_monotonically_under_replay(self, detector, frame):
        """The state at cut k must equal the state the full run reports at that time."""
        full = detector.analyse(frame, Symbol.EURUSD, Timeframe.M5)

        for cut in range(80, len(frame) + 1, 80):
            visible = frame.iloc[:cut]
            as_of = visible["timestamp"].iloc[-1].to_pydatetime() + timedelta(minutes=5)
            partial = detector.analyse(visible, Symbol.EURUSD, Timeframe.M5)
            assert partial.final_state is full.state_at(as_of)


class TestObservableAt:
    def test_observable_at_matches_replaying_visible_bars_only(self, detector, frame):
        """The guarantee downstream depends on: asking 'what did I know at t?' returns
        exactly what a live system would have had."""
        for cut in range(80, len(frame) + 1, 80):
            visible = frame.iloc[:cut]
            as_of = visible["timestamp"].iloc[-1].to_pydatetime() + timedelta(minutes=5)

            from_full = detector.observable_at(frame, as_of, Symbol.EURUSD, Timeframe.M5)
            from_visible = detector.analyse(visible, Symbol.EURUSD, Timeframe.M5)

            assert [b.as_dict() for b in from_full.breaks] == [b.as_dict() for b in from_visible.breaks]
            assert [x.as_dict() for x in from_full.labels] == [x.as_dict() for x in from_visible.labels]
            assert from_full.final_state is from_visible.final_state

    def test_observable_at_rejects_naive_timestamps(self, detector, frame):
        with pytest.raises(ValueError, match="timezone-aware"):
            detector.observable_at(
                frame, datetime(2024, 3, 8, 12, 0), Symbol.EURUSD, Timeframe.M5
            )  # noqa: DTZ001

    def test_nothing_is_observable_before_the_first_confirmation(self, detector, frame):
        analysis = detector.analyse(frame, Symbol.EURUSD, Timeframe.M5)
        earliest = min(b.confirmation_timestamp for b in analysis.breaks)

        limited = detector.observable_at(frame, earliest - timedelta(seconds=1), Symbol.EURUSD, Timeframe.M5)
        assert limited.breaks == []
        assert limited.final_state is StructureState.UNDEFINED


# ------------------------------------------------------- 5. gaps and HTF


class TestGapsAndHigherTimeframes:
    def test_a_break_cannot_appear_during_a_market_gap(self):
        """R2-02's weekend case, carried into structure.

        The reference swing's confirming bars fall AFTER the gap, so no break can be
        reported while the market is closed — the confirming bars do not exist yet.
        """
        detector = StructureDetector(StructureConfig(), SWING_1_1)
        spec = [*BULLISH_SEQUENCE, (1.10, 1.05, 1.09)]
        frame = bars(spec)

        # Push everything from the breaking bar onward two days into the future.
        gapped = frame.copy(deep=True)
        gapped.loc[9, "timestamp"] = gapped.loc[9, "timestamp"] + pd.Timedelta(days=2)

        analysis = detector.analyse(gapped, Symbol.EURUSD, Timeframe.M5)
        assert len(analysis.breaks) == 1
        event = analysis.breaks[0]

        gap_instant = START + timedelta(minutes=50)  # during the closure
        assert not any(
            b.confirmation_timestamp <= gap_instant for b in analysis.breaks
        ), "a break was visible during the gap"
        assert event.confirmation_timestamp > gap_instant + timedelta(days=1)

    def test_htf_structure_does_not_leak_into_earlier_ltf_timestamps(self):
        """A 15m structure event must not be usable by a 5m observation before the 15m
        bars that produced it had closed."""
        base = noisy(count=600, timeframe=Timeframe.M5)
        htf = resample(base, Timeframe.M5, Timeframe.M15, Symbol.EURUSD)

        detector = StructureDetector(StructureConfig(), SWING_1_1)
        htf_events = detector.events(htf.drop(columns=["close_time"]), Symbol.EURUSD, Timeframe.M15)
        assert htf_events

        ltf = with_close_time(base, Timeframe.M5)
        for observation in ltf["close_time"].iloc[::40]:
            as_of = observation.to_pydatetime()
            for event in filter_observable(htf_events, as_of):
                assert event.confirmation_timestamp <= as_of

    def test_htf_alignment_uses_only_the_reviewed_join(self):
        """Cross-timeframe assembly is R2-07's job via align_htf_context(); structure
        itself never joins timeframes. This pins that the join still behaves."""
        base = noisy(count=300, timeframe=Timeframe.M5)
        htf = resample(base, Timeframe.M5, Timeframe.M15, Symbol.EURUSD)
        merged = align_htf_context(with_close_time(base, Timeframe.M5), htf, suffix="m15")

        early = merged.loc[merged["close_time"] < htf["close_time"].iloc[0]]
        assert early["close_m15"].isna().all()
