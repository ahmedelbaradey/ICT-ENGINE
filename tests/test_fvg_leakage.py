"""R2-05 leakage, immutability and streaming replay.

The centrepiece is ``TestTheLegacyOffByOne``, which reconstructs the exact failure
mode of ``ForexQuant``'s FVG detector — one timestamp, set to candle 3's *open* — and
proves our two-field design cannot express it.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from ict_kronos.data import resample, with_close_time
from ict_kronos.domain import Symbol, Timeframe
from ict_kronos.ict import (
    ContractViolation,
    FvgConfig,
    FvgDetector,
    FvgStatus,
    GapMeasure,
    assert_no_leakage,
    assert_observable,
    filter_observable,
)

from .test_fvg import BEARISH, BULLISH, M5, START, bars

pytestmark = pytest.mark.leakage


def noisy(count: int = 400, seed: int = 20240505, timeframe: Timeframe = Timeframe.M5):
    """A random walk with wide bars, so FVGs and partial fills both occur."""
    rng = np.random.default_rng(seed)
    price = 1.0800
    spec = []
    for _ in range(count):
        price += rng.normal(0, 0.0012)
        high = round(price + abs(rng.normal(0, 0.0004)), 5)
        low = round(price - abs(rng.normal(0, 0.0004)), 5)
        spec.append((high, low))
    return bars(spec, timeframe=timeframe)


@pytest.fixture
def frame():
    return noisy()


@pytest.fixture
def detector():
    return FvgDetector(FvgConfig())


# ------------------------------------------------- 1. THE legacy off-by-one


class TestTheLegacyOffByOne:
    """The specific failure this story exists to prevent.

    ``ForexQuant`` sets a single ``StartTime = candle3.Timestamp`` — candle 3's OPEN —
    while the detection condition reads ``candle3.Low``, which is not final until
    candle 3 CLOSES. Every consumer asking "which FVGs existed at time t?" therefore
    receives each one a full bar early.
    """

    def test_the_fvg_cannot_be_returned_at_candle_n(self):
        """Candle N (index 2) completes the pattern, but the information confirming it
        is not observable until N has closed. Detecting over bars [0, 1] must be
        silent."""
        frame = bars(BULLISH)

        assert FvgDetector().detect(frame.iloc[:2], Symbol.EURUSD, M5) == []
        assert len(FvgDetector().detect(frame.iloc[:3], Symbol.EURUSD, M5)) == 1

    def test_not_observable_at_candle_3_open(self):
        """The exact instant the legacy field would report."""
        frame = bars(BULLISH)
        zone = FvgDetector().detect(frame, Symbol.EURUSD, M5)[0]

        legacy_timestamp = zone.formation_timestamp  # what ForexQuant stores
        assert not zone.is_observable_at(legacy_timestamp)
        assert zone.is_observable_at(zone.confirmation_timestamp)

    def test_the_legacy_single_timestamp_filter_would_leak(self):
        """Run the legacy filter beside ours and show they disagree by one bar."""
        frame = bars(BULLISH)
        detector = FvgDetector()
        zone = detector.detect(frame, Symbol.EURUSD, M5)[0]

        at_c3_open = zone.formation_timestamp

        # Legacy: a single StartTime = C3's open, filtered as `StartTime <= now`.
        legacy_visible = [zone] if zone.formation_timestamp <= at_c3_open else []
        # Ours: the shared observability gate.
        correct_visible = filter_observable([zone], at_c3_open)

        assert legacy_visible, "sanity: the legacy filter does admit it at C3's open"
        assert correct_visible == [], "our gate must NOT admit it at C3's open"

    def test_the_two_timestamps_can_never_be_equal(self):
        """confirmation is derived from close_time, so the legacy value is not even
        representable as a confirmation."""
        for spec in (BULLISH, BEARISH):
            zone = FvgDetector().detect(bars(spec), Symbol.EURUSD, M5)[0]
            assert zone.confirmation_timestamp != zone.formation_timestamp
            assert zone.confirmation_timestamp > zone.formation_timestamp

    def test_the_contract_refuses_the_legacy_assignment(self):
        """Even deliberately, an event cannot be confirmed at or before its own bar's
        open while claiming a later event timestamp — the invariant blocks the whole
        class of error."""
        from ict_kronos.ict import Direction, EventType, IctEvent

        with pytest.raises(ContractViolation, match="precedes"):
            IctEvent(
                symbol="EURUSD",
                timeframe="5m",
                event_type=EventType.FVG_BULLISH,
                direction=Direction.BULLISH,
                event_timestamp=START + timedelta(minutes=10),
                confirmation_timestamp=START + timedelta(minutes=5),
                price_level=1.015,
            )


# ------------------------------------------------------- 2. adversarial set


class TestAdversarialLeakage:
    def test_removing_future_candles_removes_dependent_fvgs(self, detector, frame):
        """Every zone must vanish when the bars it depends on are withheld."""
        full = detector.detect(frame, Symbol.EURUSD, M5)
        assert full

        for zone in full[:20]:
            truncated = frame.iloc[: zone.index]  # C3 withheld
            visible = detector.detect(truncated, Symbol.EURUSD, M5)
            assert zone.zone_id not in {z.zone_id for z in visible}

    def test_an_unconfirmed_fvg_cannot_affect_earlier_state(self, detector, frame):
        """Asking what was known at t must never include a zone confirming after t."""
        analysis = detector.analyse(frame, Symbol.EURUSD, M5)
        for zone in analysis.zones[:20]:
            just_before = zone.confirmation_timestamp - timedelta(seconds=1)
            limited = detector.observable_at(frame, just_before, Symbol.EURUSD, M5)
            assert zone.zone_id not in {z.zone_id for z in limited.zones}
            assert limited.fill_at(zone.zone_id, just_before) == 0.0

    def test_extending_history_cannot_move_confirmation_backward(self, detector, frame):
        """A confirmation timestamp is a fact about a bar, not about how much data
        happens to follow it."""
        early = {
            z.zone_id: z.confirmation_timestamp for z in detector.detect(frame.iloc[:200], Symbol.EURUSD, M5)
        }
        later = {z.zone_id: z.confirmation_timestamp for z in detector.detect(frame, Symbol.EURUSD, M5)}

        assert early
        for zone_id, confirmation in early.items():
            assert later[zone_id] == confirmation

    def test_future_candles_may_add_but_never_rewrite(self, detector, frame):
        early = detector.detect(frame.iloc[:200], Symbol.EURUSD, M5)
        later = {z.zone_id: z for z in detector.detect(frame, Symbol.EURUSD, M5)}

        assert early
        for zone in early:
            assert zone.zone_id in later, "a confirmed zone vanished"
            assert later[zone.zone_id].as_dict() == zone.as_dict(), "a confirmed zone was rewritten"

    def test_fill_updates_are_never_rewritten(self, detector, frame):
        early = detector.analyse(frame.iloc[:200], Symbol.EURUSD, M5)
        later = detector.analyse(frame, Symbol.EURUSD, M5)

        lookup = {(u.zone_id, u.bar_index): u for u in later.fills}
        assert early.fills
        for update in early.fills:
            key = (update.zone_id, update.bar_index)
            assert key in lookup
            assert lookup[key].as_dict() == update.as_dict()

    def test_no_fvg_is_observable_during_a_market_gap(self):
        """The confirming bar does not exist yet, so nothing can be knowable."""
        frame = bars([*BULLISH, (1.0450, 1.0350)])
        gapped = frame.copy(deep=True)
        # Push C3 and everything after it two days out, and drop contiguity so a zone
        # still forms — the point here is WHEN it becomes observable, not whether.
        for i in (2, 3):
            gapped.loc[i, "timestamp"] = gapped.loc[i, "timestamp"] + pd.Timedelta(days=2)

        detector = FvgDetector(FvgConfig(require_contiguous_bars=False))
        zones = detector.detect(gapped, Symbol.EURUSD, M5)
        assert zones

        inside_gap = START + timedelta(minutes=30)  # market shut, no bar has closed
        assert filter_observable(zones, inside_gap) == []

    def test_weekend_spanning_windows_create_no_phantom_fvg(self):
        """The default contiguity rule: positionally adjacent bars separated by a
        closure did not trade through the 'gap', so it is not an imbalance."""
        frame = bars(BULLISH)
        gapped = frame.copy(deep=True)
        gapped.loc[2, "timestamp"] = gapped.loc[2, "timestamp"] + pd.Timedelta(days=2)

        assert FvgDetector(FvgConfig()).detect(gapped, Symbol.EURUSD, M5) == []
        # And the naive variant proves the guard is what suppresses it.
        assert FvgDetector(FvgConfig(require_contiguous_bars=False)).detect(gapped, Symbol.EURUSD, M5)

    def test_fill_never_uses_a_bar_that_has_not_closed(self, detector, frame):
        analysis = detector.analyse(frame, Symbol.EURUSD, M5)
        assert analysis.fills
        for update in analysis.fills:
            assert update.confirmation_timestamp > update.event_timestamp
            zone = analysis.zone_by_id(update.zone_id)
            assert update.confirmation_timestamp > zone.confirmation_timestamp


# ----------------------------------------------------- 3. contract-level


class TestContractLevelLeakage:
    def test_no_event_leaks(self, detector, frame):
        events = detector.events(frame, Symbol.EURUSD, M5)
        assert events
        assert_no_leakage(events)

    def test_no_event_is_observable_one_second_early(self, detector, frame):
        for event in detector.events(frame, Symbol.EURUSD, M5):
            assert not event.is_observable_at(event.confirmation_timestamp - timedelta(seconds=1))
            assert event.is_observable_at(event.confirmation_timestamp)

    def test_filter_observable_gates_zones_and_fills(self, detector, frame):
        analysis = detector.analyse(frame, Symbol.EURUSD, M5)
        midpoint = analysis.zones[len(analysis.zones) // 2].confirmation_timestamp

        zones = filter_observable(analysis.zones, midpoint)
        assert zones and len(zones) < len(analysis.zones)
        assert_observable(zones, midpoint)
        assert_observable(filter_observable(analysis.fills, midpoint), midpoint)

    def test_the_module_hand_rolls_no_observability_comparison(self):
        """Source-level guard: the shared gate must remain the single path."""
        from pathlib import Path

        source = Path("ict_kronos/ict/fvg.py").read_text(encoding="utf-8")
        offenders = [
            line.strip()
            for line in source.splitlines()
            if "confirmation_timestamp <=" in line or "confirmation_timestamp >=" in line
        ]
        assert offenders == [], f"fvg.py re-implements the observability rule: {offenders}"

    def test_zones_and_fills_expose_the_shared_predicate(self, detector, frame):
        analysis = detector.analyse(frame, Symbol.EURUSD, M5)
        zone, update = analysis.zones[0], analysis.fills[0]

        assert not zone.is_observable_at(zone.confirmation_timestamp - timedelta(seconds=1))
        assert zone.is_observable_at(zone.confirmation_timestamp)
        assert not update.is_observable_at(update.confirmation_timestamp - timedelta(seconds=1))

    def test_naive_timestamps_are_rejected(self, detector, frame):
        naive = datetime(2024, 3, 8, 12, 0)  # noqa: DTZ001
        with pytest.raises(ContractViolation, match="timezone-aware"):
            detector.observable_at(frame, naive, Symbol.EURUSD, M5)


# ---------------------------------------------- 4. batch == streaming


class TestBatchEqualsStreaming:
    @pytest.mark.parametrize("measure", list(GapMeasure))
    def test_prefix_replay_matches_the_batch_prefix(self, frame, measure):
        detector = FvgDetector(FvgConfig(measure=measure))
        full = detector.analyse(frame, Symbol.EURUSD, M5)
        full_zones = [z.as_dict() for z in full.zones]
        full_fills = [u.as_dict() for u in full.fills]

        for cut in range(60, len(frame) + 1, 60):
            partial = detector.analyse(frame.iloc[:cut], Symbol.EURUSD, M5)
            assert [z.as_dict() for z in partial.zones] == full_zones[: len(partial.zones)]
            assert [u.as_dict() for u in partial.fills] == full_fills[: len(partial.fills)]

    def test_true_bar_by_bar_replay(self):
        """One bar at a time, accumulating — the strictest form."""
        detector = FvgDetector(FvgConfig())
        frame = noisy(count=140, seed=3)

        seen_zones: list = []
        seen_fills: list = []
        for n in range(1, len(frame) + 1):
            analysis = detector.analyse(frame.iloc[:n], Symbol.EURUSD, M5)
            for zone in analysis.zones:
                payload = zone.as_dict()
                if payload not in seen_zones:
                    seen_zones.append(payload)
            for update in analysis.fills:
                payload = update.as_dict()
                if payload not in seen_fills:
                    seen_fills.append(payload)

        batch = detector.analyse(frame, Symbol.EURUSD, M5)
        assert seen_zones == [z.as_dict() for z in batch.zones]
        assert seen_fills == [u.as_dict() for u in batch.fills]

    def test_observable_at_matches_replaying_visible_bars_only(self, detector, frame):
        for cut in range(80, len(frame) + 1, 80):
            visible = frame.iloc[:cut]
            as_of = visible["timestamp"].iloc[-1].to_pydatetime() + timedelta(minutes=5)

            from_full = detector.observable_at(frame, as_of, Symbol.EURUSD, M5)
            from_visible = detector.analyse(visible, Symbol.EURUSD, M5)

            assert [z.as_dict() for z in from_full.zones] == [z.as_dict() for z in from_visible.zones]
            assert [u.as_dict() for u in from_full.fills] == [u.as_dict() for u in from_visible.fills]

    def test_a_zone_appears_exactly_when_its_c3_closes(self):
        frame = bars([*BULLISH, (1.0450, 1.0350)])
        detector = FvgDetector()

        assert detector.detect(frame.iloc[:2], Symbol.EURUSD, M5) == []
        assert len(detector.detect(frame.iloc[:3], Symbol.EURUSD, M5)) == 1


# ------------------------------------------------ 5. cross-detector sanity


class TestHigherTimeframeAndComposition:
    def test_htf_fvgs_do_not_leak_into_earlier_ltf_timestamps(self):
        base = noisy(count=600)
        htf = resample(base, Timeframe.M5, Timeframe.M15, Symbol.EURUSD)

        detector = FvgDetector(FvgConfig())
        htf_events = detector.events(htf.drop(columns=["close_time"]), Symbol.EURUSD, Timeframe.M15)
        assert htf_events

        ltf = with_close_time(base, Timeframe.M5)
        for moment in ltf["close_time"].iloc[::60]:
            as_of = moment.to_pydatetime()
            for event in filter_observable(htf_events, as_of):
                assert event.confirmation_timestamp <= as_of

    def test_composes_with_the_other_detectors_under_one_contract(self):
        from ict_kronos.ict import (
            LiquidityConfig,
            LiquidityDetector,
            StructureConfig,
            StructureDetector,
            SwingConfig,
        )

        frame = noisy(count=300)
        swing = SwingConfig(left=2, right=2)
        combined = (
            FvgDetector(FvgConfig()).events(frame, Symbol.EURUSD, M5)
            + StructureDetector(StructureConfig(), swing).events(frame, Symbol.EURUSD, M5)
            + LiquidityDetector(LiquidityConfig(), swing).events(frame, Symbol.EURUSD, M5)
        )
        assert_no_leakage(combined)

        as_of = frame["timestamp"].iloc[len(frame) // 2].to_pydatetime()
        visible = filter_observable(combined, as_of)
        assert visible and len(visible) < len(combined)

    def test_fvg_is_independent_of_structure_and_liquidity(self):
        """The detector reads bars and nothing else — no coupling to R2-03 or R2-04."""
        from pathlib import Path

        source = Path("ict_kronos/ict/fvg.py").read_text(encoding="utf-8")
        assert "from .structure import" not in source
        assert "from .liquidity import" not in source
        assert "from .sessions import" not in source
        assert "from .swings import" not in source


class TestStatusConsistency:
    def test_status_matches_the_fill_stream(self, detector, frame):
        analysis = detector.analyse(frame, Symbol.EURUSD, M5)
        for zone in analysis.zones:
            updates = [u for u in analysis.fills if u.zone_id == zone.zone_id]
            expected = updates[-1].status_after if updates else FvgStatus.ACTIVE
            assert analysis.status[zone.zone_id] is expected

    def test_a_mitigated_zone_receives_no_further_updates(self, detector, frame):
        analysis = detector.analyse(frame, Symbol.EURUSD, M5)
        for zone_id, moment in analysis.mitigated_at.items():
            after = [u for u in analysis.fills if u.zone_id == zone_id and u.confirmation_timestamp > moment]
            assert after == [], f"{zone_id} kept filling after mitigation"

    def test_no_zone_is_mitigated_twice(self, detector, frame):
        analysis = detector.analyse(frame, Symbol.EURUSD, M5)
        terminal = [u for u in analysis.fills if u.status_after is FvgStatus.MITIGATED]
        assert len({u.zone_id for u in terminal}) == len(terminal)
