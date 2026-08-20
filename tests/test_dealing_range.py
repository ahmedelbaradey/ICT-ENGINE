"""R2-06 DealingRangeDetector — premium / discount / equilibrium.

The arithmetic is trivial; **the selection of the two anchors is the whole story**.
Every test that matters here is really asking one of three questions:

* did the range use only information available when it confirmed?
* is the range immutable once confirmed?
* does a classification say something honest about a price, including when the price
  is outside the range and when the range is degenerate?
"""

from __future__ import annotations

import math
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame
from ict_kronos.ict import (
    ContractViolation,
    DealingRange,
    DealingRangeConfig,
    DealingRangeDetector,
    Direction,
    EventStatus,
    EventType,
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

START = datetime(2024, 3, 8, 9, 0, tzinfo=UTC)
M5 = Timeframe.M5
SYM = Symbol.EURUSD
#: EURUSD's point is 1e-5, so the default half-tick band is 5e-6 in price units.
BAND = 0.5 * Symbol.EURUSD.spec.point_value


def bars(prices, *, wick=0.0005, start=START, timeframe=M5, symbol=SYM):
    """Flat-bodied candles: ``close == open == p``, so structure reads exactly `prices`."""
    return candles_to_frame(
        [
            MarketCandle(
                timestamp=start + timedelta(minutes=timeframe.minutes * i),
                symbol=symbol,
                timeframe=timeframe,
                open=p,
                high=p + wick,
                low=p - wick,
                close=p,
                volume=1.0,
            )
            for i, p in enumerate(prices)
        ]
    )


# ---------------------------------------------------------------------------
# Fixtures. Anchors and equilibrium are readable straight off the tables.
# ---------------------------------------------------------------------------

#: Up, pullback, then a close through the prior swing high -> BULLISH BOS at 09:45.
#: Anchors: broken swing high 1.00650 (09:15) and pullback swing low 1.00050 (09:25).
#: Range [1.00050, 1.00650], equilibrium 1.00350.
BULLISH_LEG = [
    1.0000, 1.0020, 1.0040, 1.0060, 1.0030, 1.0010,
    1.0025, 1.0050, 1.0080, 1.0100, 1.0070, 1.0120,
]  # fmt: skip

#: The same leg, then price retraces back through the range: exactly equilibrium,
#: then discount, then below the low. Gives all three zones on real bars.
BULLISH_THEN_RETRACE = BULLISH_LEG + [1.0035, 1.0010, 1.0000]

#: Down, pullback, then a close through the prior swing low -> BEARISH BOS at 09:45.
#: Anchors: swing high 1.01150 and the broken swing low 1.00550.
BEARISH_LEG = [
    1.0120, 1.0100, 1.0080, 1.0060, 1.0090, 1.0110,
    1.0095, 1.0070, 1.0040, 1.0020, 1.0050, 1.0000,
]  # fmt: skip

#: Not enough bars for a swing to confirm, let alone a break.
TOO_SHORT = [1.0000, 1.0010, 1.0020, 1.0030]


def detector(config: DealingRangeConfig | None = None) -> DealingRangeDetector:
    return DealingRangeDetector(config=config or DealingRangeConfig())


def detect(prices, config: DealingRangeConfig | None = None):
    return detector(config).detect(bars(prices), SYM, M5)


def make_range(high=1.0100, low=1.0000, **overrides) -> DealingRange:
    payload = {
        "range_id": "range:test",
        "symbol": "EURUSD",
        "timeframe": "5m",
        "direction": Direction.BULLISH,
        "high_price": high,
        "low_price": low,
        "equilibrium_price": (high + low) / 2.0,
        "high_source_id": "swing:high",
        "low_source_id": "swing:low",
        "high_source_confirmation": START,
        "low_source_confirmation": START,
        "source_break_id": "break:test",
        "created_timestamp": START,
        "confirmation_timestamp": START,
    }
    payload.update(overrides)
    return DealingRange(**payload)


class TestRangeCreation:
    def test_a_bullish_break_creates_one_range(self):
        ranges = detect(BULLISH_LEG)
        assert len(ranges) == 1
        assert ranges[0].direction is Direction.BULLISH

    def test_the_anchors_are_the_broken_high_and_the_pullback_low(self):
        item = detect(BULLISH_LEG)[0]
        assert item.high_price == pytest.approx(1.0065)
        assert item.low_price == pytest.approx(1.0005)

    def test_equilibrium_is_the_midpoint(self):
        item = detect(BULLISH_LEG)[0]
        assert item.equilibrium_price == pytest.approx((item.high_price + item.low_price) / 2)
        assert item.equilibrium_price == pytest.approx(1.0035)

    def test_a_bearish_break_anchors_on_the_broken_low(self):
        ranges = detect(BEARISH_LEG)
        assert len(ranges) == 1
        item = ranges[0]
        assert item.direction is Direction.BEARISH
        assert item.low_price == pytest.approx(1.0055)
        assert item.high_price == pytest.approx(1.0115)

    def test_no_structure_means_no_range(self):
        """Before the first confirmed break there is no range — a correct answer."""
        assert detect(TOO_SHORT) == []

    def test_an_empty_frame_produces_nothing(self):
        assert detector().detect(bars([]), SYM, M5) == []

    def test_the_high_is_always_above_the_low(self):
        for prices in (BULLISH_LEG, BEARISH_LEG, BULLISH_THEN_RETRACE):
            for item in detect(prices):
                assert item.high_price > item.low_price
                assert item.width > 0

    def test_a_later_break_creates_a_second_range(self):
        ranges = detect(BULLISH_THEN_RETRACE)
        assert len(ranges) == 2
        assert ranges[0].confirmation_timestamp < ranges[1].confirmation_timestamp


class TestAnchorSelection:
    def test_the_high_anchor_is_the_swing_the_break_broke(self):
        frame = bars(BULLISH_LEG)
        brk = StructureDetector().analyse(frame, SYM, M5).breaks[0]
        swings = SwingDetector().detect(frame, SYM, M5)
        broken = next(s for s in swings if s.event_timestamp == brk.reference_swing_timestamp)

        item = detector().detect(frame, SYM, M5)[0]
        assert item.high_source_id == swing_point_id(broken)
        assert item.high_price == pytest.approx(brk.reference_level)

    def test_the_opposite_anchor_precedes_the_break_on_the_chart(self):
        frame = bars(BULLISH_LEG)
        brk = StructureDetector().analyse(frame, SYM, M5).breaks[0]
        swings = {swing_point_id(s): s for s in SwingDetector().detect(frame, SYM, M5)}

        item = detector().detect(frame, SYM, M5)[0]
        low = swings[item.low_source_id]
        assert low.event_timestamp < brk.event_timestamp

    def test_both_anchors_are_confirmed_before_the_range(self):
        for prices in (BULLISH_LEG, BEARISH_LEG, BULLISH_THEN_RETRACE):
            for item in detect(prices):
                assert item.high_source_confirmation <= item.confirmation_timestamp
                assert item.low_source_confirmation <= item.confirmation_timestamp

    def test_an_unconfirmed_pivot_is_never_an_anchor(self):
        """Every anchor must be observable at the range's own confirmation."""
        frame = bars(BULLISH_THEN_RETRACE)
        swings = {swing_point_id(s): s for s in SwingDetector().detect(frame, SYM, M5)}

        for item in detector().detect(frame, SYM, M5):
            sources = [swings[item.high_source_id], swings[item.low_source_id]]
            assert_sources_observable_first(item, sources, label="dealing range")

    def test_a_break_with_no_opposite_anchor_yet_is_counted_not_invented(self):
        analysis = detector().analyse(bars(BULLISH_LEG), SYM, M5)
        assert analysis.rejected_missing_anchor >= 0
        assert analysis.rejected_inverted == 0


class TestConfirmationTimestamp:
    def test_confirmation_is_the_max_of_anchors_and_the_break(self):
        frame = bars(BULLISH_THEN_RETRACE)
        breaks = {structure_break_id(b): b for b in StructureDetector().analyse(frame, SYM, M5).breaks}

        for item in detector().detect(frame, SYM, M5):
            brk = breaks[item.source_break_id]
            assert item.confirmation_timestamp == max(
                item.high_source_confirmation,
                item.low_source_confirmation,
                brk.confirmation_timestamp,
            )

    def test_the_break_dominates_on_this_fixture(self):
        item = detect(BULLISH_LEG)[0]
        assert item.confirmation_timestamp == datetime(2024, 3, 8, 9, 45, tzinfo=UTC)

    def test_created_timestamp_is_the_breaks_event_timestamp(self):
        frame = bars(BULLISH_LEG)
        brk = StructureDetector().analyse(frame, SYM, M5).breaks[0]
        assert detector().detect(frame, SYM, M5)[0].created_timestamp == brk.event_timestamp

    def test_confirmation_never_precedes_creation(self):
        for prices in (BULLISH_LEG, BEARISH_LEG, BULLISH_THEN_RETRACE):
            for item in detect(prices):
                assert item.confirmation_timestamp >= item.created_timestamp

    def test_a_range_is_invisible_one_second_early(self):
        item = detect(BULLISH_LEG)[0]
        assert not item.is_observable_at(item.confirmation_timestamp - timedelta(seconds=1))
        assert item.is_observable_at(item.confirmation_timestamp)


class TestProvenanceAndIdentity:
    def test_both_anchor_ids_resolve_to_real_swings(self):
        frame = bars(BULLISH_THEN_RETRACE)
        registry = {swing_point_id(s): s for s in SwingDetector().detect(frame, SYM, M5)}
        ranges = detector().detect(frame, SYM, M5)

        assert_provenance_resolves(ranges, registry, id_fields=["high_source_id", "low_source_id"])

    def test_the_break_id_resolves(self):
        frame = bars(BULLISH_THEN_RETRACE)
        registry = {structure_break_id(b): b for b in StructureDetector().analyse(frame, SYM, M5).breaks}
        ranges = detector().detect(frame, SYM, M5)

        assert_provenance_resolves(ranges, registry, id_fields=["source_break_id"])

    def test_identity_carries_the_source_anchors(self):
        for item in detect(BULLISH_THEN_RETRACE):
            assert item.high_source_id in item.range_id
            assert item.low_source_id in item.range_id

    def test_ids_are_unique(self):
        ranges = detect(BULLISH_THEN_RETRACE)
        assert len({r.range_id for r in ranges}) == len(ranges)

    def test_identical_prices_with_different_anchors_stay_distinct(self):
        """The property that makes provenance resolvable rather than decorative."""
        one = make_range(range_id="range:a", high_source_id="swing:x")
        two = make_range(range_id="range:b", high_source_id="swing:y")
        assert one != two
        assert one.range_id != two.range_id

    def test_records_are_immutable(self):
        with pytest.raises(FrozenInstanceError):
            detect(BULLISH_LEG)[0].high_price = 2.0

    def test_the_sources_are_not_mutated(self):
        frame = bars(BULLISH_THEN_RETRACE)
        before = SwingDetector().detect(frame, SYM, M5)
        detector().analyse(frame, SYM, M5)
        assert SwingDetector().detect(frame, SYM, M5) == before


class TestDirection:
    def test_direction_is_inherited_from_the_break(self):
        frame = bars(BULLISH_THEN_RETRACE)
        breaks = {structure_break_id(b): b for b in StructureDetector().analyse(frame, SYM, M5).breaks}
        for item in detector().detect(frame, SYM, M5):
            assert item.direction is breaks[item.source_break_id].direction

    def test_direction_is_not_re_derived_from_price(self):
        """A bullish range with price in discount is an ordinary, meaningful state."""
        analysis = detector().analyse(bars(BULLISH_THEN_RETRACE), SYM, M5)
        first = analysis.ranges[0]
        discounted = [
            o for o in analysis.observations if o.range_id == first.range_id and o.zone is RangeZone.DISCOUNT
        ]
        assert discounted, "the fixture must retrace into discount for this to mean anything"
        assert first.direction is Direction.BULLISH

    def test_neutral_is_a_representable_direction(self):
        assert make_range(direction=Direction.NEUTRAL).direction is Direction.NEUTRAL


class TestClassification:
    """Boundary arithmetic, driven directly so the expected values are readable."""

    def test_price_at_the_low_is_discount(self):
        item = make_range()
        assert detector().zone_of(item, 1.0000, SYM) is RangeZone.DISCOUNT
        assert item.position_of(1.0000) == pytest.approx(0.0)

    def test_price_at_the_high_is_premium(self):
        item = make_range()
        assert detector().zone_of(item, 1.0100, SYM) is RangeZone.PREMIUM
        assert item.position_of(1.0100) == pytest.approx(1.0)

    def test_price_exactly_at_equilibrium(self):
        item = make_range()
        assert detector().zone_of(item, 1.0050, SYM) is RangeZone.EQUILIBRIUM
        assert item.position_of(1.0050) == pytest.approx(0.5)

    def test_the_equilibrium_band_is_half_a_tick_by_default(self):
        """Inside the band is equilibrium; outside it is not.

        Asserted just inside and just outside rather than *exactly* on the edge:
        ``(x + b) - x == b`` is not reliable in binary floating point, which is the
        very reason equilibrium is a band and not an equality in the first place.
        """
        item = make_range()
        d = detector()
        assert d.zone_of(item, 1.0050 + BAND * 0.5, SYM) is RangeZone.EQUILIBRIUM
        assert d.zone_of(item, 1.0050 - BAND * 0.5, SYM) is RangeZone.EQUILIBRIUM
        assert d.zone_of(item, 1.0050 + BAND * 2, SYM) is RangeZone.PREMIUM
        assert d.zone_of(item, 1.0050 - BAND * 2, SYM) is RangeZone.DISCOUNT

    def test_a_wider_tolerance_makes_equilibrium_a_real_zone(self):
        item = make_range()
        wide = detector(DealingRangeConfig(equilibrium_tolerance_points=100.0))
        assert wide.zone_of(item, 1.0055, SYM) is RangeZone.EQUILIBRIUM
        assert detector().zone_of(item, 1.0055, SYM) is RangeZone.PREMIUM

    def test_a_zero_tolerance_is_permitted(self):
        item = make_range()
        exact = detector(DealingRangeConfig(equilibrium_tolerance_points=0.0))
        assert exact.zone_of(item, 1.0050, SYM) is RangeZone.EQUILIBRIUM
        assert exact.zone_of(item, 1.0050 + 1e-9, SYM) is RangeZone.PREMIUM

    def test_a_negative_tolerance_is_rejected(self):
        with pytest.raises(ValueError, match="equilibrium_tolerance_points"):
            DealingRangeConfig(equilibrium_tolerance_points=-1.0)

    def test_position_above_one_is_legal_and_unclamped(self):
        item = make_range()
        assert item.position_of(1.0200) == pytest.approx(2.0)

    def test_position_below_zero_is_legal_and_unclamped(self):
        item = make_range()
        assert item.position_of(0.9900) == pytest.approx(-1.0)

    def test_the_zone_of_a_price_outside_the_range_is_still_meaningful(self):
        item = make_range()
        assert detector().zone_of(item, 1.0500, SYM) is RangeZone.PREMIUM
        assert detector().zone_of(item, 0.9500, SYM) is RangeZone.DISCOUNT

    def test_xauusd_uses_its_own_point_size(self):
        """The band is scale-aware: gold's point is 1e-3, a hundred times EURUSD's."""
        item = make_range(high=2100.0, low=2000.0)
        gold_band = 0.5 * Symbol.XAUUSD.spec.point_value
        assert detector().zone_of(item, 2050.0 + gold_band * 0.5, Symbol.XAUUSD) is RangeZone.EQUILIBRIUM
        assert detector().zone_of(item, 2050.0 + gold_band * 4, Symbol.XAUUSD) is RangeZone.PREMIUM
        # The same offset is decisively PREMIUM on EURUSD, whose point is 100x smaller.
        assert detector().zone_of(item, 2050.0 + gold_band * 0.5, SYM) is RangeZone.PREMIUM


class TestDegenerateRange:
    def test_a_zero_width_range_is_representable(self):
        item = make_range(high=1.0050, low=1.0050)
        assert item.is_degenerate
        assert item.width == 0.0
        assert item.equilibrium_price == pytest.approx(1.0050)

    def test_position_is_nan_not_a_crash_and_not_a_lie(self):
        item = make_range(high=1.0050, low=1.0050)
        assert math.isnan(item.position_of(1.0050))
        assert math.isnan(item.position_of(1.0100))

    def test_the_zone_is_still_defined_for_a_degenerate_range(self):
        """Classification compares against equilibrium, so it never divides by width."""
        item = make_range(high=1.0050, low=1.0050)
        d = detector()
        assert d.zone_of(item, 1.0050, SYM) is RangeZone.EQUILIBRIUM
        assert d.zone_of(item, 1.0060, SYM) is RangeZone.PREMIUM
        assert d.zone_of(item, 1.0040, SYM) is RangeZone.DISCOUNT

    def test_an_inverted_range_is_refused_at_construction(self):
        with pytest.raises(ContractViolation, match="inverted range is not a degenerate range"):
            make_range(high=1.0000, low=1.0100)

    def test_the_detector_never_emits_a_degenerate_range(self):
        for prices in (BULLISH_LEG, BEARISH_LEG, BULLISH_THEN_RETRACE):
            assert all(not r.is_degenerate for r in detect(prices))


class TestObservations:
    def test_all_three_zones_appear_on_the_retrace_fixture(self):
        analysis = detector().analyse(bars(BULLISH_THEN_RETRACE), SYM, M5)
        zones = {o.zone for o in analysis.observations}
        assert zones == {RangeZone.PREMIUM, RangeZone.DISCOUNT, RangeZone.EQUILIBRIUM}

    def test_an_observation_confirms_at_its_bars_close(self):
        analysis = detector().analyse(bars(BULLISH_LEG), SYM, M5)
        for o in analysis.observations:
            assert o.confirmation_timestamp == o.observation_timestamp + M5.duration

    def test_no_observation_precedes_its_range(self):
        analysis = detector().analyse(bars(BULLISH_THEN_RETRACE), SYM, M5)
        ranges = {r.range_id: r for r in analysis.ranges}
        for o in analysis.observations:
            assert o.confirmation_timestamp >= ranges[o.range_id].confirmation_timestamp

    def test_observations_are_attributed_to_the_active_range(self):
        analysis = detector().analyse(bars(BULLISH_THEN_RETRACE), SYM, M5)
        for o in analysis.observations:
            active = analysis.range_at(o.confirmation_timestamp)
            assert active is not None
            assert o.range_id == active.range_id

    def test_the_classified_price_is_the_bars_close(self):
        frame = bars(BULLISH_LEG)
        closes = dict(zip(frame["timestamp"], frame["close"], strict=True))
        for o in detector().analyse(frame, SYM, M5).observations:
            assert o.price == pytest.approx(closes[o.observation_timestamp])

    def test_distance_and_position_agree_with_the_range(self):
        analysis = detector().analyse(bars(BULLISH_THEN_RETRACE), SYM, M5)
        ranges = {r.range_id: r for r in analysis.ranges}
        for o in analysis.observations:
            item = ranges[o.range_id]
            assert o.distance_from_equilibrium == pytest.approx(o.price - item.equilibrium_price)
            assert o.percentage_position == pytest.approx(item.position_of(o.price))

    def test_classification_can_be_switched_off(self):
        quiet = detector(DealingRangeConfig(classify_bars=False)).analyse(bars(BULLISH_LEG), SYM, M5)
        assert quiet.ranges
        assert quiet.observations == []

    def test_observations_are_immutable(self):
        o = detector().analyse(bars(BULLISH_LEG), SYM, M5).observations[0]
        with pytest.raises(FrozenInstanceError):
            o.zone = RangeZone.PREMIUM


class TestSupersessionAndPointInTime:
    def test_a_later_range_supersedes_the_earlier_one(self):
        analysis = detector().analyse(bars(BULLISH_THEN_RETRACE), SYM, M5)
        first, second = analysis.ranges
        assert analysis.superseded_at[first.range_id] == second.confirmation_timestamp
        assert second.range_id not in analysis.superseded_at

    def test_supersession_does_not_mutate_the_superseded_record(self):
        frame = bars(BULLISH_THEN_RETRACE)
        early = detector().detect(frame.iloc[:12], SYM, M5)
        late = detector().detect(frame, SYM, M5)
        assert early[0] == late[0]

    def test_range_at_returns_the_active_range(self):
        analysis = detector().analyse(bars(BULLISH_THEN_RETRACE), SYM, M5)
        first, second = analysis.ranges

        assert analysis.range_at(first.confirmation_timestamp) == first
        assert analysis.range_at(second.confirmation_timestamp - timedelta(seconds=1)) == first
        assert analysis.range_at(second.confirmation_timestamp) == second

    def test_range_at_is_none_before_the_first_range(self):
        analysis = detector().analyse(bars(BULLISH_LEG), SYM, M5)
        early = analysis.ranges[0].confirmation_timestamp - timedelta(seconds=1)
        assert analysis.range_at(early) is None

    def test_is_active_at_tracks_supersession(self):
        analysis = detector().analyse(bars(BULLISH_THEN_RETRACE), SYM, M5)
        first, second = analysis.ranges
        assert analysis.is_active_at(first.range_id, first.confirmation_timestamp)
        assert not analysis.is_active_at(first.range_id, second.confirmation_timestamp)

    def test_classify_at_returns_none_before_any_range(self):
        frame = bars(BULLISH_LEG)
        early = detector().detect(frame, SYM, M5)[0].confirmation_timestamp - timedelta(seconds=1)
        assert detector().classify_at(frame, early, SYM, M5) is None

    def test_observable_at_hides_later_ranges(self):
        frame = bars(BULLISH_THEN_RETRACE)
        full = detector().analyse(frame, SYM, M5)
        cut = full.ranges[1].confirmation_timestamp - timedelta(seconds=1)

        limited = detector().observable_at(frame, cut, SYM, M5)
        assert len(limited.ranges) == 1
        assert limited.superseded_at == {}
        assert all(o.confirmation_timestamp <= cut for o in limited.observations)


class TestContractEvents:
    def test_ranges_are_emitted_as_dealing_range_events(self):
        events = detector().events(bars(BULLISH_LEG), SYM, M5)
        assert [e.event_type for e in events] == [EventType.DEALING_RANGE]

    def test_events_pass_the_leakage_helper(self):
        for prices in (BULLISH_LEG, BEARISH_LEG, BULLISH_THEN_RETRACE):
            assert_no_leakage(detector().events(bars(prices), SYM, M5))

    def test_a_superseded_range_reports_invalidated(self):
        events = detector().events(bars(BULLISH_THEN_RETRACE), SYM, M5)
        assert events[0].status is EventStatus.INVALIDATED
        assert events[-1].status is EventStatus.ACTIVE

    def test_metadata_carries_the_provenance_chain(self):
        event = detector().events(bars(BULLISH_LEG), SYM, M5)[0]
        for key in ("range_id", "high_source_id", "low_source_id", "source_break_id"):
            assert event.metadata[key]

    def test_the_event_price_level_is_equilibrium(self):
        item = detect(BULLISH_LEG)[0]
        event = detector().events(bars(BULLISH_LEG), SYM, M5)[0]
        assert event.price_level == pytest.approx(item.equilibrium_price)

    def test_no_event_is_emitted_per_observation(self):
        """One event per structural range, not one per bar — the stream stays readable."""
        frame = bars(BULLISH_THEN_RETRACE)
        analysis = detector().analyse(frame, SYM, M5)
        assert len(detector().events(frame, SYM, M5)) == len(analysis.ranges)
        assert len(analysis.observations) > len(analysis.ranges)


class TestBatchEqualsStreaming:
    def test_prefix_replay_matches_batch_at_every_cut(self):
        for prices in (BULLISH_LEG, BEARISH_LEG, BULLISH_THEN_RETRACE):
            frame = bars(prices)
            full = detector().detect(frame, SYM, M5)
            for cut in range(1, len(frame) + 1):
                prefix = frame.iloc[:cut]
                as_of = prefix["timestamp"].iloc[-1].to_pydatetime() + M5.duration
                assert detector().detect(prefix, SYM, M5) == filter_observable(
                    full, as_of
                ), f"prefix replay diverged at cut {cut}"

    def test_true_bar_by_bar_accumulation_matches_batch(self):
        frame = bars(BULLISH_THEN_RETRACE)
        seen: list = []
        for cut in range(1, len(frame) + 1):
            for item in detector().detect(frame.iloc[:cut], SYM, M5):
                if item not in seen:
                    seen.append(item)
        assert seen == detector().detect(frame, SYM, M5)

    def test_observations_replay_identically(self):
        frame = bars(BULLISH_THEN_RETRACE)
        full = detector().analyse(frame, SYM, M5).observations
        for cut in range(1, len(frame) + 1):
            prefix = frame.iloc[:cut]
            as_of = prefix["timestamp"].iloc[-1].to_pydatetime() + M5.duration
            replayed = detector().analyse(prefix, SYM, M5).observations
            assert replayed == filter_observable(full, as_of), f"observations diverged at cut {cut}"

    def test_appending_bars_never_rewrites_history(self):
        frame = bars(BULLISH_THEN_RETRACE)
        early = detector().detect(frame.iloc[:12], SYM, M5)
        assert early == detector().detect(frame, SYM, M5)[: len(early)]

    def test_a_pending_candidate_is_not_emitted_as_confirmed(self):
        """Truncating just before the break's confirming bar must yield no range."""
        frame = bars(BULLISH_LEG)
        item = detector().detect(frame, SYM, M5)[0]
        confirming = frame["timestamp"] + M5.duration == item.confirmation_timestamp
        cut = int(confirming.to_numpy().nonzero()[0][0])
        assert detector().detect(frame.iloc[:cut], SYM, M5) == []


class TestLeakage:
    def test_future_bars_cannot_change_a_confirmed_range(self):
        """L1 — wreck everything after confirmation; history must be byte-identical."""
        frame = bars(BULLISH_THEN_RETRACE)
        before = detector().detect(frame, SYM, M5)
        cutoff = before[0].confirmation_timestamp

        mutated = frame.copy()
        later = mutated["timestamp"] > cutoff
        mutated.loc[later, "high"] = mutated.loc[later, "high"] * 1.5
        mutated.loc[later, "low"] = mutated.loc[later, "low"] * 0.5
        mutated.loc[later, "close"] = mutated.loc[later, "close"] * 1.2

        after = [r for r in detector().detect(mutated, SYM, M5) if r.confirmation_timestamp <= cutoff]
        assert after == [r for r in before if r.confirmation_timestamp <= cutoff]

    def test_the_control_proves_the_detector_reads_prices_at_all(self):
        """L2's control — without it, the inertness tests above prove nothing."""
        frame = bars(BULLISH_THEN_RETRACE)
        moved = bars([p * 1.05 for p in BULLISH_THEN_RETRACE])
        assert detector().detect(moved, SYM, M5) != detector().detect(frame, SYM, M5)

    def test_mutating_the_confirming_bars_extremes_does_not_move_an_anchor(self):
        """L2 — the anchor's information was already known; its bar's wicks are not it."""
        frame = bars(BULLISH_LEG)
        item = detector().detect(frame, SYM, M5)[0]

        widened = frame.copy()
        at = widened["timestamp"] + M5.duration == item.confirmation_timestamp
        widened.loc[at, "high"] = widened.loc[at, "high"] + 0.0009
        widened.loc[at, "low"] = widened.loc[at, "low"] - 0.0009

        moved = detector().detect(widened, SYM, M5)[0]
        assert moved.high_price == pytest.approx(item.high_price)
        assert moved.low_price == pytest.approx(item.low_price)
        assert moved.confirmation_timestamp == item.confirmation_timestamp

    def test_the_range_never_uses_the_dataset_extrema(self):
        """L4 — the named naive implementation, written out so it cannot be skipped."""
        frame = bars(BULLISH_LEG)
        item = detector().detect(frame, SYM, M5)[0]

        naive_high = float(frame["high"].max())
        naive_low = float(frame["low"].min())

        assert item.high_price != pytest.approx(naive_high)
        assert item.low_price != pytest.approx(naive_low)
        assert naive_high > item.high_price, "the dataset high is beyond the causal anchor"

    def test_the_naive_hindsight_range_disagrees_with_the_causal_one(self):
        """L4 continued: the leak is invisible in the output, so it is proven by value."""
        frame = bars(BULLISH_LEG)
        item = detector().detect(frame, SYM, M5)[0]

        naive_equilibrium = (float(frame["high"].max()) + float(frame["low"].min())) / 2
        assert naive_equilibrium != pytest.approx(item.equilibrium_price)

        # And the disagreement is not cosmetic: it flips a real classification.
        price = item.equilibrium_price + 0.0002
        assert detector().zone_of(item, price, SYM) is RangeZone.PREMIUM
        naive = make_range(high=float(frame["high"].max()), low=float(frame["low"].min()))
        assert detector().zone_of(naive, price, SYM) is RangeZone.DISCOUNT

    def test_an_anchor_is_never_a_pivot_confirmed_after_the_range(self):
        frame = bars(BULLISH_THEN_RETRACE)
        swings = {swing_point_id(s): s for s in SwingDetector().detect(frame, SYM, M5)}
        for item in detector().detect(frame, SYM, M5):
            for source_id in (item.high_source_id, item.low_source_id):
                assert swings[source_id].confirmation_timestamp <= item.confirmation_timestamp

    def test_observations_cannot_see_a_range_that_has_not_confirmed(self):
        frame = bars(BULLISH_THEN_RETRACE)
        analysis = detector().analyse(frame, SYM, M5)
        second = analysis.ranges[1]
        early = [
            o
            for o in analysis.observations
            if o.range_id == second.range_id and o.confirmation_timestamp < second.confirmation_timestamp
        ]
        assert early == []


class TestTheObservabilityGateIsTheOnlyPath:
    """Source-level guard, matching the engine-wide rule R2-05.2 established."""

    SOURCE = "ict_kronos/ict/dealing_range.py"

    def _code(self) -> list[str]:
        """Executable lines only — comments AND docstrings stripped.

        The module docstring names the leaky implementation (``frame["high"].max()``)
        in order to warn against it, so a guard that scans raw text would flag the
        warning itself. Stripping docstrings is what makes this a guard on CODE.
        """
        from pathlib import Path

        out: list[str] = []
        inside = False
        for raw in Path(self.SOURCE).read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            fences = line.count('"""')
            if inside:
                if fences:
                    inside = fences % 2 == 0
                continue
            if fences:
                inside = fences % 2 == 1
                continue
            if line.startswith("#") or not line:
                continue
            out.append(line)
        return out

    def test_the_guard_actually_reads_code(self):
        """A stripper that returned nothing would make every guard below vacuous."""
        code = self._code()
        assert any("def detect(" in line for line in code)
        assert not any(line.startswith('"""') for line in code)

    def test_no_hand_rolled_confirmation_comparison(self):
        hits = [
            line
            for line in self._code()
            if "confirmation_timestamp <=" in line
            or "confirmation_timestamp >=" in line
            or "confirmation_timestamp <" in line
        ]
        assert hits == [], f"dealing_range.py re-implements the observability rule: {hits}"

    def test_no_comparison_of_a_confirmation_against_an_as_of(self):
        hits = [
            line
            for line in self._code()
            if "confirmation_timestamp" in line and "as_of" in line and "is_observable_at" not in line
        ]
        assert hits == [], f"the observability rule is re-implemented: {hits}"

    def test_it_routes_through_the_shared_gate(self):
        code = "\n".join(self._code())
        assert "filter_observable" in code
        assert "is_observable_at" in code

    def test_it_does_not_reimplement_swings_or_structure(self):
        code = "\n".join(self._code())
        for banned in ("def _pivots", "def _fractal", "def _breaks", "rolling(", ".max()", ".min()"):
            assert banned not in code, f"dealing_range.py appears to re-implement {banned!r}"

    def test_it_consumes_the_approved_detectors(self):
        code = "\n".join(self._code())
        assert "from .swings import" in code
        assert "from .structure import" in code
        assert "composite_confirmation" in code


class TestConfigurationSurface:
    def test_the_defaults_are_documented_values(self):
        config = DealingRangeConfig()
        assert config.equilibrium_tolerance_points == 0.5
        assert config.classify_bars is True

    def test_config_is_frozen(self):
        with pytest.raises(FrozenInstanceError):
            DealingRangeConfig().equilibrium_tolerance_points = 3.0

    def test_with_config_returns_a_new_detector(self):
        base = detector()
        tuned = base.with_config(DealingRangeConfig(equilibrium_tolerance_points=10.0))
        assert tuned is not base
        assert base.config.equilibrium_tolerance_points == 0.5

    def test_the_config_is_recorded_on_every_event(self):
        event = detector().events(bars(BULLISH_LEG), SYM, M5)[0]
        assert event.metadata["equilibrium_tolerance_points"] == 0.5

    def test_there_is_no_range_definition_knob(self):
        """Two live range definitions would make every downstream result ambiguous."""
        fields = set(DealingRangeConfig().as_dict())
        assert fields == {"equilibrium_tolerance_points", "classify_bars"}


class TestSerialization:
    def test_a_range_serialises_every_field(self):
        payload = detect(BULLISH_LEG)[0].as_dict()
        for key in (
            "range_id",
            "direction",
            "high_price",
            "low_price",
            "equilibrium_price",
            "high_source_id",
            "low_source_id",
            "source_break_id",
            "created_timestamp",
            "confirmation_timestamp",
        ):
            assert key in payload

    def test_an_observation_serialises_every_field(self):
        payload = detector().analyse(bars(BULLISH_LEG), SYM, M5).observations[0].as_dict()
        for key in (
            "range_id",
            "observation_timestamp",
            "price",
            "zone",
            "distance_from_equilibrium",
            "percentage_position",
        ):
            assert key in payload

    def test_timestamps_serialise_as_iso_utc(self):
        payload = detect(BULLISH_LEG)[0].as_dict()
        assert payload["confirmation_timestamp"].endswith("+00:00")
