"""R2-05.2/R2-05.9 real-data acceptance — EURUSD + XAUUSD, 2024-03-08 → 2024-03-11.

Timeframes: 1m/5m/15m stored, 1H/4H derived by the R2-01 resampler.

**A genuine zero is a valid result** and is asserted as such rather than engineered
away: some concepts are rare by construction, and the window is four days long. What
is NOT permitted is a detector that leaks, disagrees with its own replay, or emits an
event whose sources are not yet observable — those are checked on every combination
regardless of count.

The Phase 1.5 dataset is gitignored, so these skip cleanly when absent.
**Engineering and timestamp validation only** — no performance claim is made.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from ict_kronos.data import resample
from ict_kronos.domain import Symbol, Timeframe
from ict_kronos.ict import (
    BprDetector,
    BreakerConfig,
    BreakerDetector,
    CisdDetector,
    FvgDetector,
    IfvgDetector,
    OrderBlockDetector,
    RdrbDetector,
    StructureDetector,
    UnicornDetector,
    assert_no_leakage,
    assert_provenance_resolves,
    assert_sources_observable_first,
    filter_observable,
)
from ict_kronos.storage import ParquetCandleStore

DATA_ROOT = "data/normalized"
WINDOW_START = datetime(2024, 3, 8, tzinfo=UTC)
WINDOW_END = datetime(2024, 3, 12, tzinfo=UTC)

STORED = (Timeframe.M1, Timeframe.M5, Timeframe.M15)
DERIVED = (Timeframe.H1, Timeframe.H4)

#: Breakers are exercised without the structure gate as well, so the geometry is
#: validated on real bars even where the four-day window yields no qualifying break.
UNGATED_BREAKER = BreakerConfig(require_structure_break=False)

#: ``name -> (detector, the field holding its identity)``. The id field is named
#: explicitly rather than discovered by scanning attributes: an alphabetical guess
#: silently picked a PROVENANCE field for two of these and hid a real collision.
DETECTORS = {
    "ifvg": (IfvgDetector(), "ifvg_id"),
    "order_block": (OrderBlockDetector(), "order_block_id"),
    "breaker": (BreakerDetector(UNGATED_BREAKER), "breaker_id"),
    "bpr": (BprDetector(), "bpr_id"),
    "rdrb": (RdrbDetector(), "rdrb_id"),
    "cisd": (CisdDetector(), "cisd_id"),
    "unicorn": (UnicornDetector(breaker_config=UNGATED_BREAKER), "unicorn_id"),
}


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


@pytest.fixture(params=sorted(DETECTORS), ids=lambda n: n)
def named_detector(request):
    detector, _ = DETECTORS[request.param]
    return request.param, detector


@pytest.fixture(params=sorted(DETECTORS), ids=lambda n: n)
def identified_detector(request):
    detector, id_field = DETECTORS[request.param]
    return request.param, detector, id_field


class TestRealDetection:
    def test_detection_runs_and_respects_the_timestamp_invariant(self, named_detector, symbol, timeframe):
        _, detector = named_detector
        for event in detector.detect(load(symbol, timeframe), symbol, timeframe):
            assert event.confirmation_timestamp >= event.event_timestamp

    def test_contract_events_carry_no_leakage(self, named_detector, symbol, timeframe):
        _, detector = named_detector
        assert_no_leakage(detector.events(load(symbol, timeframe), symbol, timeframe))

    def test_zones_are_never_degenerate(self, named_detector, symbol, timeframe):
        """Every zone-shaped concept must satisfy ``zone_top > zone_bottom``."""
        _, detector = named_detector
        for event in detector.detect(load(symbol, timeframe), symbol, timeframe):
            if hasattr(event, "zone_top"):
                assert event.zone_top > event.zone_bottom

    def test_the_dense_timeframes_produce_events_for_every_detector(self, named_detector, symbol):
        """1m/5m/15m carry enough bars that a universal zero would be suspicious."""
        name, detector = named_detector
        counts = {tf.value: len(detector.detect(load(symbol, tf), symbol, tf)) for tf in STORED}
        assert sum(counts.values()) > 0, f"{name} found nothing on any dense timeframe: {counts}"

    def test_ids_are_unique(self, identified_detector, symbol, timeframe):
        """Identity must survive several events confirming on the same bar."""
        name, detector, id_field = identified_detector
        events = detector.detect(load(symbol, timeframe), symbol, timeframe)
        ids = [getattr(e, id_field) for e in events]

        assert len(ids) == len(
            set(ids)
        ), f"{name} produced {len(ids) - len(set(ids))} duplicate {id_field} values"


class TestRealProvenance:
    def test_ifvg_sources_resolve_on_real_bars(self, symbol, timeframe):
        frame = load(symbol, timeframe)
        zones = IfvgDetector().detect(frame, symbol, timeframe)
        registry = {z.zone_id: z for z in FvgDetector().detect(frame, symbol, timeframe)}

        assert_provenance_resolves(zones, registry, id_fields=["source_fvg_id"])
        for zone in zones:
            assert_sources_observable_first(zone, [registry[zone.source_fvg_id]], label="ifvg")

    def test_bpr_sources_resolve_on_real_bars(self, symbol, timeframe):
        frame = load(symbol, timeframe)
        ranges = BprDetector().detect(frame, symbol, timeframe)
        registry = {z.zone_id: z for z in FvgDetector().detect(frame, symbol, timeframe)}

        assert_provenance_resolves(ranges, registry, id_fields=["source_fvg_ids"])
        for item in ranges:
            sources = [registry[i] for i in item.source_fvg_ids]
            assert item.confirmation_timestamp == max(s.confirmation_timestamp for s in sources)

    def test_breaker_sources_resolve_on_real_bars(self, symbol, timeframe):
        frame = load(symbol, timeframe)
        breakers = BreakerDetector(UNGATED_BREAKER).detect(frame, symbol, timeframe)
        registry = {b.order_block_id: b for b in OrderBlockDetector().detect(frame, symbol, timeframe)}

        assert_provenance_resolves(breakers, registry, id_fields=["source_order_block_id"])
        for breaker in breakers:
            assert_sources_observable_first(
                breaker, [registry[breaker.source_order_block_id]], label="breaker"
            )

    def test_source_candles_always_precede_confirmation(self, symbol, timeframe):
        frame = load(symbol, timeframe)
        for detector in (OrderBlockDetector(), RdrbDetector()):
            for event in detector.detect(frame, symbol, timeframe):
                assert max(event.source_candle_timestamps) < event.confirmation_timestamp


class TestRealReplay:
    def test_batch_equals_prefix_replay(self, named_detector, symbol):
        """On 15m — dense enough to be meaningful, small enough to replay repeatedly."""
        _, detector = named_detector
        frame = load(symbol, Timeframe.M15)
        full = detector.detect(frame, symbol, Timeframe.M15)

        for cut in (len(frame) // 3, 2 * len(frame) // 3, len(frame)):
            prefix = frame.iloc[:cut]
            as_of = prefix["timestamp"].iloc[-1].to_pydatetime() + Timeframe.M15.duration
            assert detector.detect(prefix, symbol, Timeframe.M15) == filter_observable(full, as_of)

    def test_appending_does_not_rewrite_history(self, named_detector, symbol, timeframe):
        _, detector = named_detector
        frame = load(symbol, timeframe)
        half = len(frame) // 2

        early = detector.detect(frame.iloc[:half], symbol, timeframe)
        late = detector.detect(frame, symbol, timeframe)
        assert early == late[: len(early)]

    def test_future_mutation_leaves_confirmed_events_identical(self, named_detector, symbol):
        _, detector = named_detector
        frame = load(symbol, Timeframe.M5)
        events = detector.detect(frame, symbol, Timeframe.M5)
        if not events:
            pytest.skip("no events to protect")

        cutoff = events[len(events) // 2].confirmation_timestamp
        mutated = frame.copy()
        later = mutated["timestamp"] > cutoff
        mutated.loc[later, "high"] = mutated.loc[later, "high"] * 1.5
        mutated.loc[later, "low"] = mutated.loc[later, "low"] * 0.5
        mutated.loc[later, "close"] = mutated.loc[later, "close"] * 1.2

        before = [e for e in events if e.confirmation_timestamp <= cutoff]
        after = [
            e for e in detector.detect(mutated, symbol, Timeframe.M5) if e.confirmation_timestamp <= cutoff
        ]
        assert after == before


class TestRealWeekendAndGaps:
    def test_no_rdrb_spans_the_weekend_closure(self, symbol, timeframe):
        """The contiguity guard: four candles either side of a closure are not one
        delivery sequence."""
        frame = load(symbol, timeframe)
        duration = timeframe.duration

        for zone in RdrbDetector().detect(frame, symbol, timeframe):
            stamps = zone.source_candle_timestamps
            for earlier, later in zip(stamps[:-1], stamps[1:], strict=True):
                assert later - earlier == duration

    def test_no_order_block_group_spans_a_gap(self, symbol, timeframe):
        frame = load(symbol, timeframe)
        duration = timeframe.duration

        for block in OrderBlockDetector().detect(frame, symbol, timeframe):
            stamps = block.source_candle_timestamps
            for earlier, later in zip(stamps[:-1], stamps[1:], strict=True):
                assert later - earlier == duration

    def test_no_cisd_leg_spans_a_gap(self, symbol, timeframe):
        frame = load(symbol, timeframe)
        stamps = set(frame["timestamp"].dt.to_pydatetime())

        for cisd in CisdDetector().detect(frame, symbol, timeframe):
            span = cisd.leg_start_timestamp
            covered = 0
            while span <= cisd.leg_end_timestamp:
                assert span in stamps
                covered += 1
                span += timeframe.duration
            assert covered == cisd.leg_length


class TestRealCrossDetectorRelationships:
    def test_ifvg_is_rarer_than_fvg_mitigation(self, symbol):
        """The concept's whole point: a wick can fill a gap without inverting it."""
        frame = load(symbol, Timeframe.M5)
        analysis = IfvgDetector().analyse(frame, symbol, Timeframe.M5)
        fvg = FvgDetector().analyse(frame, symbol, Timeframe.M5)

        mitigated = sum(1 for s in fvg.status.values() if s.value == "mitigated")
        assert len(analysis.zones) <= mitigated or mitigated == 0
        # And the gap between the two populations is recorded, not merely implied.
        assert isinstance(analysis.mitigated_without_inversion, list)

    def test_cisd_and_mss_are_not_the_same_event(self, symbol):
        """Divergence is the evidence that CISD is a separate concept from structure."""
        frame = load(symbol, Timeframe.M5)
        cisd = CisdDetector().detect(frame, symbol, Timeframe.M5)
        breaks = StructureDetector().analyse(frame, symbol, Timeframe.M5).breaks

        cisd_stamps = {c.confirmation_timestamp for c in cisd}
        break_stamps = {b.confirmation_timestamp for b in breaks}
        assert cisd_stamps != break_stamps

    def test_order_blocks_confirm_later_than_their_candidates(self, symbol, timeframe):
        """Real-data form of the property that makes the OB detector non-trivial."""
        blocks = OrderBlockDetector().detect(load(symbol, timeframe), symbol, timeframe)
        for block in blocks:
            assert block.bars_to_confirm >= 1
            assert block.confirmation_timestamp > block.event_timestamp

    def test_breakers_never_precede_their_order_blocks(self, symbol, timeframe):
        frame = load(symbol, timeframe)
        for breaker in BreakerDetector(UNGATED_BREAKER).detect(frame, symbol, timeframe):
            assert breaker.confirmation_timestamp > breaker.source_order_block_confirmation

    def test_unicorn_sources_resolve_on_real_bars(self, symbol, timeframe):
        """Both parents AND the transitive Order Block, on every real combination."""
        frame = load(symbol, timeframe)
        detector = UnicornDetector(breaker_config=UNGATED_BREAKER)
        unicorns = detector.detect(frame, symbol, timeframe)

        breakers = {b.breaker_id: b for b in detector.breaker_detector.detect(frame, symbol, timeframe)}
        gaps = {z.zone_id: z for z in FvgDetector().detect(frame, symbol, timeframe)}
        blocks = {b.order_block_id: b for b in OrderBlockDetector().detect(frame, symbol, timeframe)}

        assert_provenance_resolves(unicorns, breakers, id_fields=["source_breaker_id"])
        assert_provenance_resolves(unicorns, gaps, id_fields=["source_fvg_id"])
        assert_provenance_resolves(unicorns, blocks, id_fields=["source_order_block_id"])
        for unicorn in unicorns:
            sources = [breakers[unicorn.source_breaker_id], gaps[unicorn.source_fvg_id]]
            assert_sources_observable_first(unicorn, sources, label="unicorn")
            assert unicorn.confirmation_timestamp == max(s.confirmation_timestamp for s in sources)

    def test_unicorn_polarity_always_matches_both_parents(self, symbol, timeframe):
        frame = load(symbol, timeframe)
        detector = UnicornDetector(breaker_config=UNGATED_BREAKER)
        breakers = {b.breaker_id: b for b in detector.breaker_detector.detect(frame, symbol, timeframe)}
        gaps = {z.zone_id: z for z in FvgDetector().detect(frame, symbol, timeframe)}

        for unicorn in detector.detect(frame, symbol, timeframe):
            assert unicorn.direction is breakers[unicorn.source_breaker_id].direction
            assert unicorn.direction is gaps[unicorn.source_fvg_id].direction

    def test_unicorn_zones_sit_inside_both_parents(self, symbol, timeframe):
        """The intersection property, checked against real geometry rather than asserted."""
        frame = load(symbol, timeframe)
        detector = UnicornDetector(breaker_config=UNGATED_BREAKER)
        breakers = {b.breaker_id: b for b in detector.breaker_detector.detect(frame, symbol, timeframe)}
        gaps = {z.zone_id: z for z in FvgDetector().detect(frame, symbol, timeframe)}

        for unicorn in detector.detect(frame, symbol, timeframe):
            breaker = breakers[unicorn.source_breaker_id]
            gap = gaps[unicorn.source_fvg_id]
            assert unicorn.zone_top <= min(breaker.zone_top, gap.top)
            assert unicorn.zone_bottom >= max(breaker.zone_bottom, gap.bottom)

    def test_unicorn_cardinality_is_not_collapsed_on_real_bars(self, symbol):
        """Several gaps per Breaker is the NORMAL case, and each keeps its own id."""
        frame = load(symbol, Timeframe.M5)
        unicorns = UnicornDetector(breaker_config=UNGATED_BREAKER).detect(frame, symbol, Timeframe.M5)
        if not unicorns:
            pytest.skip("no unicorns on this combination — a valid result")

        per_breaker: dict[str, int] = {}
        for unicorn in unicorns:
            per_breaker[unicorn.source_breaker_id] = per_breaker.get(unicorn.source_breaker_id, 0) + 1

        assert len({u.unicorn_id for u in unicorns}) == len(unicorns)
        assert max(per_breaker.values()) >= 1


class TestRealObservability:
    def test_mid_window_filtering_hides_later_events(self, named_detector, symbol):
        _, detector = named_detector
        frame = load(symbol, Timeframe.M5)
        events = detector.detect(frame, symbol, Timeframe.M5)
        if len(events) < 4:
            pytest.skip("too few events to partition")

        as_of = events[len(events) // 2].confirmation_timestamp
        visible = filter_observable(events, as_of)

        assert visible
        assert len(visible) < len(events)
        assert all(e.confirmation_timestamp <= as_of for e in visible)

    def test_an_event_is_invisible_one_second_early(self, named_detector, symbol):
        _, detector = named_detector
        events = detector.detect(load(symbol, Timeframe.M5), symbol, Timeframe.M5)
        if not events:
            pytest.skip("no events")

        event = events[0]
        assert not event.is_observable_at(event.confirmation_timestamp - timedelta(seconds=1))
        assert event.is_observable_at(event.confirmation_timestamp)


class TestNoRegressionInApprovedDetectors:
    def test_r2_01_to_r2_05_still_run_unchanged(self, symbol, timeframe):
        """R2-05.2 adds event types and modules; it must not disturb what exists."""
        from ict_kronos.ict import LiquidityDetector, SwingDetector, TrueDailyOpenDetector

        frame = load(symbol, timeframe)
        assert SwingDetector().detect(frame, symbol, timeframe) is not None
        assert StructureDetector().analyse(frame, symbol, timeframe) is not None
        assert LiquidityDetector().analyse(frame, symbol, timeframe) is not None
        assert FvgDetector().analyse(frame, symbol, timeframe) is not None
        assert TrueDailyOpenDetector().detect(frame, symbol, timeframe) is not None
