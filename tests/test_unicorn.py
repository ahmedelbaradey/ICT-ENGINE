"""R2-05.9 UnicornDetector — a Breaker overlapping a SAME-polarity Fair Value Gap.

Three properties carry this module, and every one of them is a way the concept can be
silently wrong rather than loudly broken:

* **Confirmation is the max of both components.** Stamping a Unicorn at the FVG's
  confirmation when the Breaker confirms later publishes it before its Breaker exists.
* **Cardinality is not collapsed.** Four gaps overlapping one Breaker are four
  Unicorns with four ids, all confirming on the same bar.
* **Invalidation is inherited, not recomputed.** A Unicorn's death is its Breaker's
  death, read out of the Breaker's own fill stream.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame
from ict_kronos.ict import (
    BreakerConfig,
    BreakerDetector,
    Direction,
    EventType,
    FvgDetector,
    OrderBlockDetector,
    UnicornConfig,
    UnicornDetector,
    UnicornStatus,
    ZoneStatus,
    assert_no_leakage,
    assert_provenance_resolves,
    assert_sources_observable_first,
    filter_observable,
)

START = datetime(2024, 3, 8, 9, 0, tzinfo=UTC)
M5 = Timeframe.M5
SYM = Symbol.EURUSD

#: Structure needs confirmed swings, which these deliberately small fixtures do not
#: produce. The gate itself is R2-05.4's concern and is asserted there; here it is
#: switched off so the Unicorn's own geometry is what the tests are reading.
NO_STRUCTURE = BreakerConfig(require_structure_break=False)


def bars(spec, *, start=START, timeframe=M5):
    return candles_to_frame(
        [
            MarketCandle(
                timestamp=start + timedelta(minutes=timeframe.minutes * i),
                symbol=SYM,
                timeframe=timeframe,
                open=o,
                high=h,
                low=low,
                close=c,
                volume=1.0,
            )
            for i, (o, h, low, c) in enumerate(spec)
        ]
    )


def detector(config: UnicornConfig | None = None) -> UnicornDetector:
    return UnicornDetector(config=config or UnicornConfig(), breaker_config=NO_STRUCTURE)


def detect(spec, config: UnicornConfig | None = None):
    return detector(config).detect(bars(spec), SYM, M5)


# ---------------------------------------------------------------------------
# Fixtures. Every price is chosen so the expected zone can be read off the table.
# ---------------------------------------------------------------------------

#: A bullish Order Block (1.0030-1.0060) fails at 09:20, flipping to a BEARISH Breaker
#: on the same zone. A bearish FVG [1.0040, 1.0055] then confirms at 09:30.
#: Overlap -> [1.0040, 1.0055]. The Breaker confirms FIRST here.
BREAKER_THEN_GAP = [
    (1.0050, 1.0055, 1.0045, 1.0050),  # doji — context, not a block
    (1.0052, 1.0060, 1.0030, 1.0035),  # the OB candidate
    (1.0035, 1.0075, 1.0034, 1.0070),  # confirms the OB (close > 1.0060)
    (1.0070, 1.0072, 1.0055, 1.0058),
    (1.0058, 1.0059, 1.0020, 1.0025),  # CLOSES below 1.0030 -> the block fails
    (1.0025, 1.0040, 1.0018, 1.0022),  # high 1.0040 < 1.0055 -> bearish FVG
]

#: The mirror ordering: the FVG [1.0050, 1.0055] confirms at 09:30, the Breaker only at
#: 09:35. This is the fixture the L4 naive implementation gets wrong.
GAP_THEN_BREAKER = [
    (1.0050, 1.0055, 1.0045, 1.0050),
    (1.0052, 1.0060, 1.0030, 1.0035),
    (1.0035, 1.0075, 1.0034, 1.0070),
    (1.0070, 1.0072, 1.0055, 1.0058),
    (1.0058, 1.0059, 1.0045, 1.0048),  # dips but does NOT close below 1.0030
    (1.0048, 1.0050, 1.0042, 1.0045),  # high 1.0050 < 1.0055 -> bearish FVG at 09:30
    (1.0045, 1.0046, 1.0020, 1.0025),  # the block finally fails at 09:35
]

#: FOUR bearish gaps all overlapping the same Breaker, all confirming by 09:50 — the
#: cardinality and identity fixture.
FOUR_GAPS_ONE_BREAKER = [
    (1.0050, 1.0055, 1.0045, 1.0050),
    (1.0052, 1.0060, 1.0030, 1.0035),
    (1.0035, 1.0075, 1.0034, 1.0070),
    (1.0070, 1.0072, 1.0058, 1.0060),
    (1.0060, 1.0061, 1.0050, 1.0052),
    (1.0052, 1.0056, 1.0048, 1.0050),  # gap [1.0056, 1.0058]  @ 09:30
    (1.0050, 1.0051, 1.0044, 1.0046),  # gap [1.0047, 1.0048]  @ 09:40
    (1.0046, 1.0047, 1.0040, 1.0042),  # gap [1.0043, 1.0044]  @ 09:45
    (1.0042, 1.0043, 1.0036, 1.0038),  # gap [1.0039, 1.0040]  @ 09:50
    (1.0038, 1.0039, 1.0020, 1.0025),  # the block fails at 09:50
]

#: One gap TOUCHES the Breaker's lower edge at exactly 1.0030 and one genuinely
#: overlaps at [1.0033, 1.0034]. Rejection and acceptance in a single fixture, so the
#: negative case cannot pass merely because the fixture produces nothing.
TOUCHING_AND_OVERLAPPING = [
    (1.0050, 1.0055, 1.0045, 1.0050),
    (1.0052, 1.0060, 1.0030, 1.0035),
    (1.0035, 1.0075, 1.0034, 1.0070),
    (1.0070, 1.0072, 1.0030, 1.0032),
    (1.0032, 1.0033, 1.0028, 1.0031),
    (1.0029, 1.0029, 1.0024, 1.0026),  # gap top == 1.0030 == the Breaker's bottom
]

#: The Breaker is later mitigated (price trades back through 1.0060), which the
#: Unicorn inherits as INVALIDATED. It also prints a BULLISH gap over the same band —
#: an opposite-polarity overlap that must produce nothing.
BREAKER_DIES = BREAKER_THEN_GAP + [
    (1.0022, 1.0058, 1.0021, 1.0056),
    (1.0056, 1.0065, 1.0055, 1.0062),  # high 1.0065 > 1.0060 -> the Breaker is filled
]


class TestTheOverlapIsTheUnicorn:
    def test_a_breaker_and_a_same_polarity_gap_produce_one_unicorn(self):
        unicorns = detect(BREAKER_THEN_GAP)
        assert len(unicorns) == 1
        assert unicorns[0].direction is Direction.BEARISH

    def test_the_zone_is_the_intersection_not_either_component(self):
        unicorn = detect(BREAKER_THEN_GAP)[0]
        breaker = BreakerDetector(NO_STRUCTURE).detect(bars(BREAKER_THEN_GAP), SYM, M5)[0]
        gap = FvgDetector().detect(bars(BREAKER_THEN_GAP), SYM, M5)[0]

        assert unicorn.zone_top == pytest.approx(min(breaker.zone_top, gap.top))
        assert unicorn.zone_bottom == pytest.approx(max(breaker.zone_bottom, gap.bottom))
        assert unicorn.zone_top == pytest.approx(1.0055)
        assert unicorn.zone_bottom == pytest.approx(1.0040)

    def test_the_overlap_is_reported_in_instrument_points(self):
        """EURUSD's point is 1e-5, so 0.0015 is 150 points — not 15."""
        assert detect(BREAKER_THEN_GAP)[0].overlap_points == pytest.approx(150.0)

    def test_the_zone_is_never_degenerate(self):
        for unicorn in detect(FOUR_GAPS_ONE_BREAKER):
            assert unicorn.zone_top > unicorn.zone_bottom

    def test_a_touching_gap_is_not_an_overlap(self):
        """Gap top == Breaker bottom shares no range. The fixture's OTHER gap does."""
        frame = bars(TOUCHING_AND_OVERLAPPING)
        gaps = [(z.bottom, z.top) for z in FvgDetector().detect(frame, SYM, M5)]
        assert any(
            b == pytest.approx(1.0029) and t == pytest.approx(1.0030) for b, t in gaps
        ), "the touching gap must exist, or this test proves nothing"

        unicorns = detect(TOUCHING_AND_OVERLAPPING)
        assert len(unicorns) == 1
        assert unicorns[0].zone_bottom == pytest.approx(1.0033)

    def test_opposite_polarity_overlap_produces_nothing(self):
        """The bullish gap in this fixture covers the bearish Breaker's band exactly."""
        frame = bars(BREAKER_DIES)
        bullish_gaps = [z for z in FvgDetector().detect(frame, SYM, M5) if z.is_bullish]
        assert bullish_gaps, "the fixture must contain a bullish gap"

        paired = {u.source_fvg_id for u in detect(BREAKER_DIES)}
        assert paired.isdisjoint({z.zone_id for z in bullish_gaps})

    def test_no_breakers_means_no_unicorns(self):
        """Bars that never fail a block: the composite has nothing to compose."""
        flat = [(1.0050, 1.0051, 1.0049, 1.0050)] * 8
        assert detect(flat) == []


class TestCardinalityIsNotCollapsed:
    def test_four_gaps_over_one_breaker_produce_four_unicorns(self):
        unicorns = detect(FOUR_GAPS_ONE_BREAKER)
        assert len(unicorns) == 4
        assert len({u.source_breaker_id for u in unicorns}) == 1
        assert len({u.source_fvg_id for u in unicorns}) == 4

    def test_simultaneous_unicorns_remain_independently_addressable(self):
        """All four confirm on the SAME bar — the id collision the audit found twice."""
        unicorns = detect(FOUR_GAPS_ONE_BREAKER)
        assert len({u.confirmation_timestamp for u in unicorns}) == 1
        assert len({u.unicorn_id for u in unicorns}) == 4

    def test_the_pair_is_part_of_the_identity(self):
        for unicorn in detect(FOUR_GAPS_ONE_BREAKER):
            assert unicorn.source_breaker_id in unicorn.unicorn_id
            assert unicorn.source_fvg_id in unicorn.unicorn_id

    def test_zones_are_not_merged(self):
        zones = {(u.zone_bottom, u.zone_top) for u in detect(FOUR_GAPS_ONE_BREAKER)}
        assert len(zones) == 4


class TestConfirmationTiming:
    def test_confirmation_is_the_max_of_both_components(self):
        for spec in (BREAKER_THEN_GAP, GAP_THEN_BREAKER):
            for unicorn in detect(spec):
                assert unicorn.confirmation_timestamp == max(
                    unicorn.source_breaker_confirmation, unicorn.source_fvg_confirmation
                )

    def test_a_late_breaker_delays_the_unicorn(self):
        unicorn = detect(GAP_THEN_BREAKER)[0]
        assert unicorn.source_fvg_confirmation == datetime(2024, 3, 8, 9, 30, tzinfo=UTC)
        assert unicorn.source_breaker_confirmation == datetime(2024, 3, 8, 9, 35, tzinfo=UTC)
        assert unicorn.confirmation_timestamp == datetime(2024, 3, 8, 9, 35, tzinfo=UTC)

    def test_a_late_gap_delays_the_unicorn(self):
        unicorn = detect(BREAKER_THEN_GAP)[0]
        assert unicorn.source_breaker_confirmation == datetime(2024, 3, 8, 9, 25, tzinfo=UTC)
        assert unicorn.confirmation_timestamp == datetime(2024, 3, 8, 9, 30, tzinfo=UTC)

    def test_the_event_timestamp_is_the_later_component_formation(self):
        unicorn = detect(BREAKER_THEN_GAP)[0]
        breaker = BreakerDetector(NO_STRUCTURE).detect(bars(BREAKER_THEN_GAP), SYM, M5)[0]
        gap = FvgDetector().detect(bars(BREAKER_THEN_GAP), SYM, M5)[0]
        assert unicorn.event_timestamp == max(breaker.event_timestamp, gap.formation_timestamp)

    def test_confirmation_never_precedes_the_event(self):
        for spec in (BREAKER_THEN_GAP, GAP_THEN_BREAKER, FOUR_GAPS_ONE_BREAKER, BREAKER_DIES):
            for unicorn in detect(spec):
                assert unicorn.confirmation_timestamp >= unicorn.event_timestamp


class TestTheNaiveImplementationDiverges:
    """L4 — the plausible leaky version, written out so it cannot be quietly skipped."""

    @staticmethod
    def naive_confirmation(unicorn) -> datetime:
        """The mistake: stamp the Unicorn when its GAP confirmed."""
        return unicorn.source_fvg_confirmation

    def test_the_naive_stamp_publishes_before_the_breaker_exists(self):
        unicorn = detect(GAP_THEN_BREAKER)[0]
        naive = self.naive_confirmation(unicorn)

        assert naive < unicorn.source_breaker_confirmation
        assert naive < unicorn.confirmation_timestamp

    def test_the_causal_implementation_hides_it_where_the_naive_one_would_not(self):
        unicorns = detect(GAP_THEN_BREAKER)
        as_of = self.naive_confirmation(unicorns[0])
        assert filter_observable(unicorns, as_of) == []


class TestProvenance:
    def test_both_source_ids_resolve(self):
        frame = bars(FOUR_GAPS_ONE_BREAKER)
        unicorns = detector().detect(frame, SYM, M5)

        breakers = {b.breaker_id: b for b in BreakerDetector(NO_STRUCTURE).detect(frame, SYM, M5)}
        gaps = {z.zone_id: z for z in FvgDetector().detect(frame, SYM, M5)}

        assert_provenance_resolves(unicorns, breakers, id_fields=["source_breaker_id"])
        assert_provenance_resolves(unicorns, gaps, id_fields=["source_fvg_id"])

    def test_order_block_provenance_is_carried_transitively(self):
        frame = bars(BREAKER_THEN_GAP)
        unicorn = detector().detect(frame, SYM, M5)[0]
        breaker = BreakerDetector(NO_STRUCTURE).detect(frame, SYM, M5)[0]
        blocks = {b.order_block_id: b for b in OrderBlockDetector().detect(frame, SYM, M5)}

        assert unicorn.source_order_block_id == breaker.source_order_block_id
        assert unicorn.source_order_block_id in blocks

    def test_every_source_is_observable_before_the_unicorn(self):
        frame = bars(FOUR_GAPS_ONE_BREAKER)
        breakers = {b.breaker_id: b for b in BreakerDetector(NO_STRUCTURE).detect(frame, SYM, M5)}
        gaps = {z.zone_id: z for z in FvgDetector().detect(frame, SYM, M5)}

        for unicorn in detector().detect(frame, SYM, M5):
            assert_sources_observable_first(
                unicorn,
                [breakers[unicorn.source_breaker_id], gaps[unicorn.source_fvg_id]],
                label="unicorn",
            )

    def test_the_sources_are_not_mutated(self):
        frame = bars(BREAKER_DIES)
        before = FvgDetector().detect(frame, SYM, M5)
        detector().analyse(frame, SYM, M5)
        assert FvgDetector().detect(frame, SYM, M5) == before

    def test_records_are_immutable(self):
        unicorn = detect(BREAKER_THEN_GAP)[0]
        with pytest.raises(FrozenInstanceError):
            unicorn.zone_top = 2.0


class TestAdjacencyWindow:
    def test_a_tight_window_drops_the_distant_gaps(self):
        wide = detect(FOUR_GAPS_ONE_BREAKER)
        tight = detect(FOUR_GAPS_ONE_BREAKER, UnicornConfig(max_bars_from_breaker=1))

        assert len(wide) == 4
        assert len(tight) == 2
        assert all(u.bars_between <= 1 for u in tight)

    def test_bars_between_is_measured_between_confirmations(self):
        unicorn = detect(BREAKER_THEN_GAP)[0]
        span = unicorn.source_fvg_confirmation - unicorn.source_breaker_confirmation
        assert unicorn.bars_between == int(abs(span) / M5.duration)

    def test_a_window_below_one_bar_is_rejected(self):
        with pytest.raises(ValueError, match="max_bars_from_breaker"):
            UnicornConfig(max_bars_from_breaker=0)


class TestOptionalQualifiers:
    def test_containment_is_recorded_but_not_required_by_default(self):
        unicorn = detect(BREAKER_THEN_GAP)[0]
        assert unicorn.fully_contained is True
        assert UnicornConfig().require_full_containment is False

    def test_requiring_containment_keeps_the_contained_pairs(self):
        strict = detect(FOUR_GAPS_ONE_BREAKER, UnicornConfig(require_full_containment=True))
        assert strict
        assert all(u.fully_contained for u in strict)

    def test_a_minimum_overlap_filters_thin_intersections(self):
        """The fixture's four overlaps are 20, 10, 10 and 10 points."""
        assert len(detect(FOUR_GAPS_ONE_BREAKER, UnicornConfig(min_overlap_points=15.0))) == 1
        assert len(detect(FOUR_GAPS_ONE_BREAKER, UnicornConfig(min_overlap_points=500.0))) == 0

    def test_a_negative_minimum_overlap_is_rejected(self):
        with pytest.raises(ValueError, match="min_overlap_points"):
            UnicornConfig(min_overlap_points=-1.0)


class TestLifecycle:
    def test_an_untouched_unicorn_stays_active(self):
        analysis = detector().analyse(bars(FOUR_GAPS_ONE_BREAKER), SYM, M5)
        assert set(analysis.status.values()) == {UnicornStatus.ACTIVE}

    def test_the_retest_is_recorded_in_the_update_stream(self):
        analysis = detector().analyse(bars(BREAKER_DIES), SYM, M5)
        unicorn = analysis.unicorns[0]

        assert unicorn.unicorn_id in analysis.retested_at
        assert analysis.retested_at[unicorn.unicorn_id] == analysis.fills[0].confirmation_timestamp

    def test_the_retest_does_not_change_the_confirmation(self):
        """The event exists when both components do; the retest is a trade concern."""
        analysis = detector().analyse(bars(BREAKER_DIES), SYM, M5)
        unicorn = analysis.unicorns[0]
        assert unicorn.confirmation_timestamp < analysis.retested_at[unicorn.unicorn_id]

    def test_invalidation_is_inherited_from_the_source_breaker(self):
        frame = bars(BREAKER_DIES)
        analysis = detector().analyse(frame, SYM, M5)
        unicorn = analysis.unicorns[0]

        breaker_fills = BreakerDetector(NO_STRUCTURE).analyse(frame, SYM, M5).fills
        death = next(u for u in breaker_fills if u.status_after is ZoneStatus.MITIGATED)

        assert analysis.inherited_invalidation_at[unicorn.unicorn_id] == death.confirmation_timestamp
        assert analysis.status[unicorn.unicorn_id] is UnicornStatus.INVALIDATED

    def test_the_unicorn_is_not_invalidated_before_its_breaker_dies(self):
        frame = bars(BREAKER_DIES)
        analysis = detector().analyse(frame, SYM, M5)
        unicorn = analysis.unicorns[0]
        death = analysis.inherited_invalidation_at[unicorn.unicorn_id]

        early = analysis.status_at(unicorn.unicorn_id, death - timedelta(seconds=1))
        assert early is not None
        assert early is not UnicornStatus.INVALIDATED
        assert analysis.status_at(unicorn.unicorn_id, death) is UnicornStatus.INVALIDATED

    def test_status_is_none_before_the_unicorn_is_observable(self):
        analysis = detector().analyse(bars(BREAKER_DIES), SYM, M5)
        unicorn = analysis.unicorns[0]
        early = unicorn.confirmation_timestamp - timedelta(seconds=1)
        assert analysis.status_at(unicorn.unicorn_id, early) is None

    def test_active_at_excludes_the_dead(self):
        frame = bars(BREAKER_DIES)
        analysis = detector().analyse(frame, SYM, M5)
        unicorn = analysis.unicorns[0]
        death = analysis.inherited_invalidation_at[unicorn.unicorn_id]

        assert analysis.active_at(unicorn.confirmation_timestamp) == [unicorn]
        assert analysis.active_at(death) == []

    def test_an_inherited_death_outranks_the_unicorns_own_mitigation(self):
        """This fixture fills the intersection BEFORE the Breaker itself dies.

        Reporting order follows ``OrderBlockAnalysis``: the structural end of a zone
        outranks its fill, so the same instant reads MITIGATED and then INVALIDATED.
        Both are terminal; the second says *why* more precisely.
        """
        frame = bars(BREAKER_DIES)
        analysis = detector().analyse(frame, SYM, M5)
        unicorn = analysis.unicorns[0]
        death = analysis.inherited_invalidation_at[unicorn.unicorn_id]

        before = analysis.status_at(unicorn.unicorn_id, death - timedelta(seconds=1))
        assert before is UnicornStatus.MITIGATED
        assert analysis.status_at(unicorn.unicorn_id, death) is UnicornStatus.INVALIDATED


class TestContractEvents:
    def test_events_carry_the_unicorn_event_types(self):
        events = detector().events(bars(BREAKER_THEN_GAP), SYM, M5)
        assert [e.event_type for e in events] == [EventType.UNICORN_BEARISH]

    def test_events_pass_the_leakage_helper(self):
        for spec in (BREAKER_THEN_GAP, GAP_THEN_BREAKER, FOUR_GAPS_ONE_BREAKER, BREAKER_DIES):
            assert_no_leakage(detector().events(bars(spec), SYM, M5))

    def test_metadata_carries_the_whole_provenance_chain(self):
        event = detector().events(bars(BREAKER_THEN_GAP), SYM, M5)[0]
        for key in ("unicorn_id", "source_breaker_id", "source_fvg_id", "source_order_block_id"):
            assert event.metadata[key]

    def test_an_invalidated_unicorn_reports_invalidated(self):
        from ict_kronos.ict import EventStatus

        event = detector().events(bars(BREAKER_DIES), SYM, M5)[0]
        assert event.status is EventStatus.INVALIDATED

    def test_the_configuration_is_recorded_on_every_event(self):
        event = detector().events(bars(BREAKER_THEN_GAP), SYM, M5)[0]
        assert event.metadata["max_bars_from_breaker"] == 50
        assert event.metadata["require_full_containment"] is False


class TestObservabilityAndReplay:
    def test_batch_equals_prefix_replay_at_every_cut(self):
        for spec in (BREAKER_THEN_GAP, GAP_THEN_BREAKER, FOUR_GAPS_ONE_BREAKER, BREAKER_DIES):
            frame = bars(spec)
            full = detector().detect(frame, SYM, M5)
            for cut in range(1, len(frame) + 1):
                prefix = frame.iloc[:cut]
                as_of = prefix["timestamp"].iloc[-1].to_pydatetime() + M5.duration
                assert detector().detect(prefix, SYM, M5) == filter_observable(
                    full, as_of
                ), f"prefix replay diverged at cut {cut}"

    def test_bar_by_bar_streaming_matches_batch(self):
        frame = bars(FOUR_GAPS_ONE_BREAKER)
        seen: list = []
        for cut in range(1, len(frame) + 1):
            for unicorn in detector().detect(frame.iloc[:cut], SYM, M5):
                if unicorn not in seen:
                    seen.append(unicorn)
        assert seen == detector().detect(frame, SYM, M5)

    def test_appending_bars_never_rewrites_history(self):
        frame = bars(BREAKER_DIES)
        early = detector().detect(frame.iloc[:7], SYM, M5)
        assert early == detector().detect(frame, SYM, M5)[: len(early)]

    def test_observable_at_hides_the_unconfirmed(self):
        frame = bars(GAP_THEN_BREAKER)
        unicorn = detector().detect(frame, SYM, M5)[0]
        early = unicorn.confirmation_timestamp - timedelta(seconds=1)

        assert detector().observable_at(frame, early, SYM, M5).unicorns == []
        assert detector().observable_at(frame, unicorn.confirmation_timestamp, SYM, M5).unicorns

    def test_an_inherited_death_is_not_visible_before_it_happens(self):
        frame = bars(BREAKER_DIES)
        full = detector().analyse(frame, SYM, M5)
        unicorn = full.unicorns[0]
        death = full.inherited_invalidation_at[unicorn.unicorn_id]

        limited = detector().observable_at(frame, death - timedelta(seconds=1), SYM, M5)
        assert limited.inherited_invalidation_at == {}
        assert limited.status[unicorn.unicorn_id] is not UnicornStatus.INVALIDATED


class TestLeakage:
    def test_future_bars_cannot_change_a_confirmed_unicorn(self):
        """L1 — wreck everything after confirmation; history must be byte-identical."""
        frame = bars(FOUR_GAPS_ONE_BREAKER)
        before = detector().detect(frame, SYM, M5)
        assert before, "the fixture must produce events for this test to mean anything"

        cutoff = before[0].confirmation_timestamp
        mutated = frame.copy()
        later = mutated["timestamp"] > cutoff
        mutated.loc[later, "high"] = mutated.loc[later, "high"] * 1.5
        mutated.loc[later, "low"] = mutated.loc[later, "low"] * 0.5
        mutated.loc[later, "close"] = mutated.loc[later, "close"] * 1.2

        after = [u for u in detector().detect(mutated, SYM, M5) if u.confirmation_timestamp <= cutoff]
        assert after == [u for u in before if u.confirmation_timestamp <= cutoff]

    def test_the_control_proves_the_detector_reads_prices_at_all(self):
        """L2's control — without it, L1 passing would prove nothing."""
        frame = bars(FOUR_GAPS_ONE_BREAKER)
        mutated = frame.copy()
        for column in ("open", "high", "low", "close"):
            mutated[column] = mutated[column] * 1.05

        assert detector().detect(mutated, SYM, M5) != detector().detect(frame, SYM, M5)

    def test_mutating_a_source_gap_changes_only_what_follows_it(self):
        """L6 — the composite tracks its sources rather than caching their geometry."""
        frame = bars(BREAKER_THEN_GAP)
        widened = frame.copy()
        widened.loc[widened.index[-1], "high"] = 1.0045

        original = detector().detect(frame, SYM, M5)[0]
        moved = detector().detect(widened, SYM, M5)[0]

        assert moved.zone_bottom == pytest.approx(1.0045)
        assert original.zone_bottom == pytest.approx(1.0040)
        assert moved.confirmation_timestamp == original.confirmation_timestamp

    def test_a_unicorn_is_invisible_one_second_early(self):
        for unicorn in detect(FOUR_GAPS_ONE_BREAKER):
            assert not unicorn.is_observable_at(unicorn.confirmation_timestamp - timedelta(seconds=1))
            assert unicorn.is_observable_at(unicorn.confirmation_timestamp)


class TestConsumesRatherThanReimplements:
    """The structural guard: this module must not grow a second copy of anything."""

    SOURCE = "ict_kronos/ict/unicorn.py"

    def _code(self) -> str:
        from pathlib import Path

        return Path(self.SOURCE).read_text(encoding="utf-8")

    def test_it_does_not_reimplement_gap_or_block_detection(self):
        code = self._code()
        for banned in ("def _runs", "def _legs", "def _zone(", "displacement"):
            assert banned not in code, f"unicorn.py appears to re-implement {banned!r}"

    def test_it_consumes_the_approved_detectors(self):
        code = self._code()
        assert "from .breakers import" in code
        assert "from .fvg import" in code

    def test_the_detectors_are_reachable_as_properties(self):
        d = detector()
        assert isinstance(d.breaker_detector, BreakerDetector)
        assert isinstance(d.fvg_detector, FvgDetector)

    def test_with_config_returns_a_new_detector(self):
        base = detector()
        tuned = base.with_config(UnicornConfig(max_bars_from_breaker=3))
        assert tuned is not base
        assert tuned.config.max_bars_from_breaker == 3
        assert base.config.max_bars_from_breaker == 50
