"""R2-07 ICTMarketState — the point-in-time aggregation of every approved detector.

Three properties carry this layer, and each is a way it could be silently wrong:

* **It aggregates, it does not detect.** Nothing here may re-derive a pattern, and the
  guard tests below read the source to prove it.
* **``0`` and UNKNOWN are different.** Zero is a real distance; conflating them tells a
  model price is sitting on a level that does not exist.
* **Provenance is an id.** Every emitted id must resolve back to a real event in the
  same analysis — never be reconstructed from a price.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame
from ict_kronos.ict import (
    BprDetector,
    BreakerDetector,
    CisdDetector,
    FvgDetector,
    ICTMarketState,
    IfvgDetector,
    LiquidityDetector,
    MarketBias,
    MarketStateBuilder,
    MarketStateConfig,
    OrderBlockDetector,
    RdrbDetector,
    StructureDetector,
    StructureState,
    TrueDailyOpenDetector,
    UnicornDetector,
    assert_provenance_resolves,
    structure_break_id,
    swing_registry,
)

START = datetime(2024, 3, 8, 9, 0, tzinfo=UTC)
M5 = Timeframe.M5
SYM = Symbol.EURUSD


def bars(prices, *, wick=0.0005, start=START, timeframe=M5, symbol=SYM):
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


#: Long enough for swings, a structural break, a dealing range and some composites.
TREND = [
    1.0000, 1.0020, 1.0040, 1.0060, 1.0030, 1.0010, 1.0025, 1.0050,
    1.0080, 1.0100, 1.0070, 1.0120, 1.0090, 1.0140, 1.0110, 1.0160,
    1.0130, 1.0180, 1.0150, 1.0200, 1.0170, 1.0090, 1.0050, 1.0020,
]  # fmt: skip

#: Flat and overlapping, so NOTHING confirms — the "everything is UNKNOWN" fixture.
#: An earlier version used a gentle ramp and was wrong: three rising candles with
#: non-overlapping wicks form a perfectly valid bullish FVG, which is the detector
#: being right and the fixture being careless.
BARELY = [1.0000, 1.0000, 1.0000, 1.0000]


def builder(config: MarketStateConfig | None = None) -> MarketStateBuilder:
    return MarketStateBuilder(config=config or MarketStateConfig())


def view(prices=TREND):
    return builder().analyse(bars(prices), SYM, M5)


def last_state(prices=TREND) -> ICTMarketState:
    states = view(prices).states()
    assert states, "fixture must produce at least one state"
    return states[-1]


class TestObservationAndAssembly:
    def test_one_state_per_bar_close(self):
        engine = view()
        assert len(engine.states()) == len(bars(TREND))

    def test_the_state_is_anchored_to_its_bar(self):
        state = last_state()
        assert state.as_of == state.bar.close_time
        assert state.bar.close_time == state.bar.timestamp + M5.duration

    def test_state_at_returns_none_between_bar_closes(self):
        """An observation is anchored to a knowable close; inventing one between
        closes would be inventing a price."""
        engine = view()
        mid = engine.observation_instants()[3] + timedelta(seconds=1)
        assert engine.state_at(mid) is None

    def test_the_state_carries_the_bars_ohlcv(self):
        frame = bars(TREND)
        state = builder().analyse(frame, SYM, M5).states()[-1]
        row = frame.iloc[-1]
        assert state.bar.close == pytest.approx(float(row["close"]))
        assert state.bar.high == pytest.approx(float(row["high"]))
        assert state.bar.volume == pytest.approx(1.0)

    def test_the_state_is_immutable(self):
        with pytest.raises(FrozenInstanceError):
            last_state().symbol = "XAUUSD"

    def test_collections_are_tuples_not_lists(self):
        """No mutable detector internals leak into the state."""
        state = last_state()
        assert isinstance(state.liquidity.active_buy_side_ids, tuple)
        assert isinstance(state.imbalance.active_bullish_fvg_ids, tuple)
        assert isinstance(state.bias.bullish_evidence, tuple)

    def test_the_state_records_its_version(self):
        assert last_state().state_version == "r2-07.1"

    def test_states_can_be_sampled_without_changing_any_of_them(self):
        engine = view()
        every = {s.as_of: s for s in engine.states()}
        instants = engine.observation_instants()[::3]
        for sampled in engine.states(instants):
            assert sampled == every[sampled.as_of]

    def test_a_frame_too_short_for_anything_still_builds_a_state(self):
        states = view(BARELY).states()
        assert states
        state = states[-1]
        assert state.structure.latest_break_id is None
        assert state.premium_discount.range_id is None
        assert state.bias.bias in (MarketBias.UNKNOWN, MarketBias.NEUTRAL)


class TestItAggregatesRatherThanDetects:
    def test_structure_matches_the_structure_detector(self):
        frame = bars(TREND)
        engine = builder().analyse(frame, SYM, M5)
        expected = StructureDetector().analyse(frame, SYM, M5)
        for state in engine.states():
            assert state.structure.state is expected.state_at(state.as_of)

    def test_liquidity_counts_match_the_liquidity_detector(self):
        frame = bars(TREND)
        engine = builder().analyse(frame, SYM, M5)
        expected = LiquidityDetector().analyse(frame, SYM, M5)
        for state in engine.states():
            active = expected.active_at(state.as_of)
            total = state.liquidity.buy_side_count + state.liquidity.sell_side_count
            assert total == len(active)

    def test_fvg_counts_match_the_fvg_detector(self):
        frame = bars(TREND)
        engine = builder().analyse(frame, SYM, M5)
        expected = FvgDetector().analyse(frame, SYM, M5)
        for state in engine.states():
            active = expected.active_at(state.as_of)
            total = state.imbalance.bullish_fvg_count + state.imbalance.bearish_fvg_count
            assert total == len(active)

    def test_order_block_counts_match_the_order_block_detector(self):
        frame = bars(TREND)
        engine = builder().analyse(frame, SYM, M5)
        expected = OrderBlockDetector().analyse(frame, SYM, M5)
        for state in engine.states():
            active = expected.active_at(state.as_of)
            total = (
                state.institutional.bullish_order_block_count + state.institutional.bearish_order_block_count
            )
            assert total == len(active)

    def test_delivery_state_matches_the_cisd_detector(self):
        frame = bars(TREND)
        engine = builder().analyse(frame, SYM, M5)
        expected = CisdDetector().analyse(frame, SYM, M5)
        for state in engine.states():
            assert state.composites.delivery_state is expected.state_at(state.as_of)

    def test_the_dealing_range_is_r2_06s_own_range(self):
        from ict_kronos.ict import DealingRangeDetector

        frame = bars(TREND)
        engine = builder().analyse(frame, SYM, M5)
        expected = DealingRangeDetector().analyse(frame, SYM, M5)
        for state in engine.states():
            item = expected.range_at(state.as_of)
            if item is None:
                assert state.premium_discount.range_id is None
            else:
                assert state.premium_discount.range_id == item.range_id
                assert state.premium_discount.equilibrium_price == pytest.approx(item.equilibrium_price)

    def test_no_detector_is_reimplemented_in_source(self):
        code = _code_of("ict_kronos/ict/market_state.py")
        for banned in ("def _pivots", "def _fractal", "def _runs", "def _legs", "def _zone("):
            assert banned not in code, f"market_state.py appears to re-implement {banned!r}"

    def test_the_observability_gate_is_not_reimplemented(self):
        code = _code_of("ict_kronos/ict/market_state.py")
        for banned in (
            "confirmation_timestamp <=",
            "confirmation_timestamp >=",
            "confirmation_timestamp <",
        ):
            assert banned not in code, f"market_state.py re-implements observability: {banned!r}"

    def test_no_second_timezone_or_dst_implementation(self):
        code = _code_of("ict_kronos/ict/market_state.py")
        for banned in ("ZoneInfo(", "America/", "tz_localize", "tz_convert"):
            assert banned not in code, f"market_state.py defines a timezone: {banned!r}"


def _code_of(path: str) -> str:
    """Executable lines only — comments and docstrings stripped.

    The module docstrings deliberately name the things the guards ban, in order to warn
    against them, so a guard that scanned raw text would flag its own warning.
    """
    from pathlib import Path

    out: list[str] = []
    inside = False
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
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
    return "\n".join(out)


class TestTheGuardsReadCode:
    def test_the_stripper_returns_executable_lines(self):
        """A stripper returning nothing would make every guard above vacuous."""
        code = _code_of("ict_kronos/ict/market_state.py")
        assert "def state_at(" in code
        assert '"""' not in code


class TestProvenance:
    def test_every_emitted_id_resolves_to_a_real_event(self):
        frame = bars(TREND)
        engine = builder().analyse(frame, SYM, M5)
        registries = _registries(frame)

        for state in engine.states():
            for group, ids in state.source_ids().items():
                if not ids:
                    continue
                registry = registries[group]
                missing = [i for i in ids if i not in registry]
                assert missing == [], f"{group} ids do not resolve: {missing}"

    def test_provenance_resolves_through_the_shared_helper(self):
        frame = bars(TREND)
        engine = builder().analyse(frame, SYM, M5)
        gaps = {z.zone_id: z for z in FvgDetector().detect(frame, SYM, M5)}

        holders = [
            _IdHolder(s.imbalance.latest_bullish_fvg_id)
            for s in engine.states()
            if s.imbalance.latest_bullish_fvg_id is not None
        ]
        assert_provenance_resolves(holders, gaps, id_fields=["source_id"])

    def test_unicorn_provenance_is_inherited_whole(self):
        frame = bars(TREND)
        engine = builder().analyse(frame, SYM, M5)
        unicorns = {u.unicorn_id: u for u in UnicornDetector().detect(frame, SYM, M5)}

        seen = 0
        for state in engine.states():
            found = state.composites.latest_unicorn_id
            if found is None:
                continue
            seen += 1
            source = unicorns[found]
            assert state.composites.latest_unicorn_fvg_id == source.source_fvg_id
            assert state.composites.latest_unicorn_breaker_id == source.source_breaker_id
            assert state.composites.latest_unicorn_order_block_id == source.source_order_block_id
        if seen == 0:
            pytest.skip("no Unicorn on this fixture — a valid result")

    def test_dealing_range_anchors_resolve_to_swings(self):
        from ict_kronos.ict import SwingConfig

        frame = bars(TREND)
        engine = builder().analyse(frame, SYM, M5)
        swings = swing_registry(frame, SYM, M5, SwingConfig())

        seen = 0
        for state in engine.states():
            if state.premium_discount.high_anchor_id is None:
                continue
            seen += 1
            assert state.premium_discount.high_anchor_id in swings
            assert state.premium_discount.low_anchor_id in swings
        assert seen > 0, "the fixture must produce a dealing range"

    def test_no_id_is_reconstructed_from_a_price(self):
        """Ids must be opaque strings from the detectors, not derived from numbers."""
        state = last_state()
        for ids in state.source_ids().values():
            for value in ids:
                assert isinstance(value, str)
                assert ":" in value


class _IdHolder:
    """Minimal shim so the shared provenance helper can check a bare id."""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id


def _registries(frame) -> dict[str, dict]:
    from ict_kronos.ict import SwingConfig

    return {
        "structure": {structure_break_id(b): b for b in StructureDetector().analyse(frame, SYM, M5).breaks},
        "liquidity_level": {x.level_id: x for x in LiquidityDetector().analyse(frame, SYM, M5).levels},
        "fvg": {z.zone_id: z for z in FvgDetector().detect(frame, SYM, M5)},
        "ifvg": {z.ifvg_id: z for z in IfvgDetector().detect(frame, SYM, M5)},
        "bpr": {r.bpr_id: r for r in BprDetector().detect(frame, SYM, M5)},
        "order_block": {b.order_block_id: b for b in OrderBlockDetector().detect(frame, SYM, M5)},
        "breaker": {b.breaker_id: b for b in BreakerDetector().detect(frame, SYM, M5)},
        "rdrb": {z.rdrb_id: z for z in RdrbDetector().detect(frame, SYM, M5)},
        "cisd": {c.cisd_id: c for c in CisdDetector().detect(frame, SYM, M5)},
        "unicorn": {u.unicorn_id: u for u in UnicornDetector().detect(frame, SYM, M5)},
        "daily_open": {x.level_id: x for x in TrueDailyOpenDetector().detect(frame, SYM, M5)},
        "dealing_range": _range_registry(frame),
        "swing": swing_registry(frame, SYM, M5, SwingConfig()),
    }


def _range_registry(frame) -> dict:
    from ict_kronos.ict import DealingRangeDetector

    return {r.range_id: r for r in DealingRangeDetector().detect(frame, SYM, M5)}


class TestMissingValues:
    def test_zero_and_unknown_are_different_for_liquidity(self):
        """A count of 0 means no resting liquidity; a missing distance means there is
        nothing to measure to. Telling a model both are 0 puts price ON a level that
        does not exist."""
        state = view(BARELY).states()[-1]
        assert state.liquidity.buy_side_count == 0
        assert state.liquidity.nearest_buy_side_points is None

    def test_zero_and_unknown_are_different_for_imbalance(self):
        state = view(BARELY).states()[-1]
        assert state.imbalance.bullish_fvg_count == 0
        assert state.imbalance.nearest_bullish_fvg_points is None

    def test_no_dealing_range_leaves_every_zone_field_none(self):
        state = view(BARELY).states()[-1]
        pd_ctx = state.premium_discount
        assert pd_ctx.range_id is None
        assert pd_ctx.zone is None
        assert pd_ctx.percentage_position is None
        assert pd_ctx.equilibrium_price is None

    def test_no_structural_break_leaves_break_fields_none_but_state_defined(self):
        state = view(BARELY).states()[-1]
        assert state.structure.latest_break_id is None
        assert state.structure.bars_since_break is None
        assert state.structure.state is StructureState.UNDEFINED
        assert state.structure.bos_count == 0

    def test_counts_are_never_none(self):
        for state in (view(BARELY).states()[-1], last_state()):
            assert isinstance(state.imbalance.bullish_fvg_count, int)
            assert isinstance(state.institutional.bullish_order_block_count, int)
            assert isinstance(state.composites.unicorn_count, int)

    def test_empty_id_tuples_rather_than_none(self):
        state = view(BARELY).states()[-1]
        assert state.liquidity.active_buy_side_ids == ()
        assert state.imbalance.active_bullish_fvg_ids == ()


class TestEventSelection:
    def test_latest_is_chosen_by_confirmation_not_position(self):
        frame = bars(TREND)
        engine = builder().analyse(frame, SYM, M5)
        breaks = {structure_break_id(b): b for b in StructureDetector().analyse(frame, SYM, M5).breaks}

        for state in engine.states():
            found = state.structure.latest_break_id
            if found is None:
                continue
            chosen = breaks[found]
            observable = [b for b in breaks.values() if b.confirmation_timestamp <= state.as_of]
            assert chosen.confirmation_timestamp == max(b.confirmation_timestamp for b in observable)

    def test_id_tuples_are_sorted_and_deduplicated(self):
        state = last_state()
        for ids in state.source_ids().values():
            assert list(ids) == sorted(set(ids))

    def test_active_id_tuples_are_sorted(self):
        state = last_state()
        for ids in (
            state.liquidity.active_buy_side_ids,
            state.imbalance.active_bullish_fvg_ids,
            state.institutional.active_bullish_order_block_ids,
            state.composites.active_unicorn_ids,
        ):
            assert list(ids) == sorted(ids)

    def test_repeated_builds_are_identical(self):
        frame = bars(TREND)
        first = builder().analyse(frame, SYM, M5).states()
        second = builder().analyse(frame, SYM, M5).states()
        assert first == second

    def test_ties_are_counted_not_collapsed(self):
        """Several events sharing a confirmation bar all count; only 'latest' picks one."""
        state = last_state()
        counted = len(state.imbalance.active_bullish_fvg_ids) + len(state.imbalance.active_bearish_fvg_ids)
        assert counted == state.imbalance.bullish_fvg_count + state.imbalance.bearish_fvg_count


class TestLifecycle:
    def test_active_means_each_detectors_own_definition(self):
        frame = bars(TREND)
        engine = builder().analyse(frame, SYM, M5)
        breakers = BreakerDetector().analyse(frame, SYM, M5)

        for state in engine.states():
            live = set(state.institutional.active_bullish_breaker_ids) | set(
                state.institutional.active_bearish_breaker_ids
            )
            for breaker_id in live:
                status = breakers.status_at(breaker_id, state.as_of)
                assert status is not None
                assert status.value != "mitigated"

    def test_a_sweep_never_makes_an_unobservable_level_observable(self):
        frame = bars(TREND)
        engine = builder().analyse(frame, SYM, M5)
        levels = {x.level_id: x for x in LiquidityDetector().analyse(frame, SYM, M5).levels}

        for state in engine.states():
            for level_id in state.liquidity.swept_level_ids:
                assert levels[level_id].confirmation_timestamp <= state.as_of

    def test_no_universal_lifecycle_enum_is_invented(self):
        """FVG mitigation and Order Block invalidation stay different facts."""
        code = _code_of("ict_kronos/ict/market_state.py")
        assert "class UniversalStatus" not in code
        assert "class LifecycleState" not in code


class TestBias:
    def test_evidence_is_always_exposed_independently(self):
        state = last_state()
        assert state.bias.bullish_score == len(state.bias.bullish_evidence)
        assert state.bias.bearish_score == len(state.bias.bearish_evidence)

    def test_no_evidence_is_unknown_not_neutral(self):
        """UNKNOWN and NEUTRAL are different answers and must not be collapsed."""
        state = view(BARELY).states()[0]
        if state.bias.bullish_score == 0 and state.bias.bearish_score == 0:
            assert state.bias.bias is MarketBias.UNKNOWN

    def test_conflicting_evidence_is_neutral_not_a_forced_direction(self):
        frame = bars(TREND)
        conflicted = [
            s
            for s in builder().analyse(frame, SYM, M5).states()
            if s.bias.bullish_score == s.bias.bearish_score and s.bias.bullish_score > 0
        ]
        for state in conflicted:
            assert state.bias.bias is MarketBias.NEUTRAL

    def test_the_verdict_follows_the_documented_counting_rule(self):
        for state in view().states():
            bull, bear = state.bias.bullish_score, state.bias.bearish_score
            if bull == 0 and bear == 0:
                assert state.bias.bias is MarketBias.UNKNOWN
            elif bull > bear:
                assert state.bias.bias is MarketBias.BULLISH
            elif bear > bull:
                assert state.bias.bias is MarketBias.BEARISH
            else:
                assert state.bias.bias is MarketBias.NEUTRAL

    def test_evidence_names_are_stable_strings(self):
        known = {
            "structure_bullish",
            "structure_bearish",
            "delivery_bullish",
            "delivery_bearish",
            "price_in_discount",
            "price_in_premium",
            "sell_side_liquidity_taken",
            "buy_side_liquidity_taken",
        }
        for state in view().states():
            assert set(state.bias.bullish_evidence) <= known
            assert set(state.bias.bearish_evidence) <= known

    def test_at_most_four_sources_contribute(self):
        for state in view().states():
            assert state.bias.bullish_score + state.bias.bearish_score <= 4


class TestDistancesDeclareTheirUnit:
    def test_point_distances_use_the_instrument_point(self):
        state = last_state()
        pd_ctx = state.premium_discount
        if pd_ctx.equilibrium_price is None:
            pytest.skip("no dealing range")
        raw = state.bar.close - pd_ctx.equilibrium_price
        assert pd_ctx.distance_from_equilibrium_points == pytest.approx(raw / SYM.spec.point_value)

    def test_gold_and_forex_use_different_point_sizes(self):
        gold = bars([p * 2000 for p in TREND], wick=0.5, symbol=Symbol.XAUUSD)
        states = builder().analyse(gold, Symbol.XAUUSD, M5).states()
        state = states[-1]
        if state.premium_discount.equilibrium_price is None:
            pytest.skip("no dealing range on the gold fixture")
        raw = state.bar.close - state.premium_discount.equilibrium_price
        assert state.premium_discount.distance_from_equilibrium_points == pytest.approx(
            raw / Symbol.XAUUSD.spec.point_value
        )

    def test_prices_and_points_are_never_the_same_field(self):
        names = {f for f in dir(last_state().liquidity) if not f.startswith("_")}
        assert "nearest_buy_side_price" in names
        assert "nearest_buy_side_points" in names


class TestSerialization:
    def test_state_serialises_every_section(self):
        payload = last_state().as_dict()
        for key in (
            "symbol",
            "timeframe",
            "as_of",
            "state_version",
            "bar",
            "structure",
            "liquidity",
            "imbalance",
            "institutional",
            "composites",
            "daily_open",
            "premium_discount",
            "session",
            "bias",
        ):
            assert key in payload

    def test_field_order_is_stable(self):
        one = last_state().as_dict()
        two = last_state().as_dict()
        assert list(one) == list(two)
        assert list(one["structure"]) == list(two["structure"])

    def test_enums_serialise_as_values(self):
        payload = last_state().as_dict()
        assert isinstance(payload["structure"]["state"], str)
        assert isinstance(payload["bias"]["bias"], str)

    def test_timestamps_serialise_as_iso_utc(self):
        payload = last_state().as_dict()
        assert payload["as_of"].endswith("+00:00")
        assert payload["bar"]["close_time"].endswith("+00:00")

    def test_missing_serialises_as_null_never_zero(self):
        payload = view(BARELY).states()[-1].as_dict()
        assert payload["premium_discount"]["zone"] is None
        assert payload["liquidity"]["nearest_buy_side_points"] is None

    def test_tuples_serialise_as_lists(self):
        payload = last_state().as_dict()
        assert isinstance(payload["liquidity"]["active_buy_side_ids"], list)

    def test_serialisation_is_json_safe(self):
        import json

        text = json.dumps(last_state().as_dict())
        assert json.loads(text)["state_version"] == "r2-07.1"


class TestConfiguration:
    def test_the_config_surface_is_tiny(self):
        assert set(MarketStateConfig().as_dict()) == {"classify_bars"}

    def test_no_detector_semantic_knob_is_exposed(self):
        """A knob changing what a detector MEANS would make R2-07 a second place
        where ICT is defined."""
        surface = set(MarketStateConfig().as_dict())
        for banned in ("require_fvg", "break_mode", "trigger", "polarity", "tolerance"):
            assert not any(banned in name for name in surface)

    def test_config_is_frozen(self):
        with pytest.raises(FrozenInstanceError):
            MarketStateConfig().classify_bars = False

    def test_with_config_returns_a_new_builder(self):
        base = builder()
        tuned = base.with_config(MarketStateConfig(classify_bars=False))
        assert tuned is not base
        assert base.config.classify_bars is True

    def test_detector_configs_are_injectable_for_reproducibility(self):
        from ict_kronos.ict import SwingConfig

        tuned = MarketStateBuilder(swing_config=SwingConfig(left=3, right=3))
        assert tuned.swing_config.left == 3
        assert builder().swing_config.left == 2


class TestTimeframeLocality:
    def test_the_state_reports_the_timeframe_it_was_built_from(self):
        for timeframe in (Timeframe.M5, Timeframe.M15):
            frame = bars(TREND, timeframe=timeframe)
            state = builder().analyse(frame, SYM, timeframe).states()[-1]
            assert state.timeframe == timeframe.value

    def test_no_higher_timeframe_join_is_performed(self):
        code = _code_of("ict_kronos/ict/market_state.py")
        for banned in ("align_htf_context", "merge_asof", "resample("):
            assert banned not in code, f"market_state.py performs an HTF join: {banned!r}"

    def test_no_timeframe_is_fabricated(self):
        code = _code_of("ict_kronos/ict/market_state.py")
        for banned in ("Timeframe.D1", "Timeframe.W1", '"1d"', '"1w"'):
            assert banned not in code
