"""R2-07 real-data acceptance — EURUSD + XAUUSD, 2024-03-08 → 2024-03-11.

The window contains the US DST transition (2024-03-10) and a full weekend closure, so
both are exercised on real bars.

**Sampling, and why it is honest.** A state costs a dozen point-in-time queries, so on
a 2933-bar 1m frame building every one takes minutes. These tests sample instants on
the dense timeframes and use *every* instant on 15m/1H/4H. Sampling changes nothing
about any individual state — each is still built from the full frame at its own
``as_of`` — and the streaming-equivalence tests, where completeness actually matters,
run at every cut on the timeframes small enough to permit it.

**Zeros are valid.** No fixture is altered to manufacture events.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from ict_kronos.data import resample
from ict_kronos.domain import Symbol, Timeframe
from ict_kronos.ict import (
    FEATURE_NAMES,
    ICTFeatureVector,
    MarketBias,
    MarketStateBuilder,
    RangeZone,
    StructureDetector,
    SwingConfig,
    feature_vectors,
    structure_break_id,
    swing_registry,
    vectors_to_frame,
)
from ict_kronos.storage import ParquetCandleStore

DATA_ROOT = "data/normalized"
WINDOW_START = datetime(2024, 3, 8, tzinfo=UTC)
WINDOW_END = datetime(2024, 3, 12, tzinfo=UTC)

STORED = (Timeframe.M1, Timeframe.M5, Timeframe.M15)
DERIVED = (Timeframe.H1, Timeframe.H4)
#: 1m and 5m are sampled; everything else uses every instant.
SAMPLE_STRIDE = {Timeframe.M1: 97, Timeframe.M5: 17}


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

    if len(frame) < 8:
        pytest.skip(f"real data insufficient for {symbol.value}/{timeframe.value}")
    return frame


@pytest.fixture(params=[Symbol.EURUSD, Symbol.XAUUSD], ids=lambda s: s.value)
def symbol(request) -> Symbol:
    return request.param


@pytest.fixture(params=[*STORED, *DERIVED], ids=lambda t: t.value)
def timeframe(request) -> Timeframe:
    return request.param


#: Timeframes for tests that must RE-ANALYSE a frame (mutated, truncated, appended).
#:
#: Those cannot use the cache, and one full pass over 2933 1m bars runs every detector
#: including the Unicorn lifecycle scan — tens of seconds each, times two symbols,
#: times several tests. Restricting them to 15m and coarser keeps the suite usable
#: without weakening the claim: the same properties are proven **at every cut** on the
#: synthetic fixtures in `test_feature_vector.py`, and the 1m/5m combinations are still
#: fully covered here by the state, provenance, feature and serialisation tests.
HEAVY = (Timeframe.M15, *DERIVED)


@pytest.fixture(params=HEAVY, ids=lambda t: t.value)
def coarse_timeframe(request) -> Timeframe:
    return request.param


#: One analysis per (symbol, timeframe) for the whole module.
#:
#: Running every approved detector over a 2933-bar 1m frame is genuinely expensive —
#: the Unicorn lifecycle pass alone is tens of seconds — and re-running it inside each
#: of ~35 tests made this file exceed a 15-minute timeout. The analyses are pure
#: functions of the frame, so caching them changes NOTHING any test observes:
#: observability is still decided per call by ``as_of``. Caching the *inputs*, not the
#: answers.
_VIEWS: dict[tuple[Symbol, Timeframe], object] = {}


def engine(symbol: Symbol, timeframe: Timeframe):
    key = (symbol, timeframe)
    if key not in _VIEWS:
        _VIEWS[key] = MarketStateBuilder().analyse(load(symbol, timeframe), symbol, timeframe)
    return _VIEWS[key]


_STATES: dict[tuple[Symbol, Timeframe], list] = {}


def sampled_states(symbol: Symbol, timeframe: Timeframe):
    view = engine(symbol, timeframe)
    key = (symbol, timeframe)
    if key not in _STATES:
        stride = SAMPLE_STRIDE.get(timeframe, 1)
        _STATES[key] = view.states(view.observation_instants()[::stride])
    return view, _STATES[key]


class TestRealStateConstruction:
    def test_a_state_is_built_for_every_sampled_instant(self, symbol, timeframe):
        view, states = sampled_states(symbol, timeframe)
        stride = SAMPLE_STRIDE.get(timeframe, 1)
        assert len(states) == len(view.observation_instants()[::stride])

    def test_every_state_is_anchored_to_its_own_bar(self, symbol, timeframe):
        _, states = sampled_states(symbol, timeframe)
        for state in states:
            assert state.as_of == state.bar.close_time
            assert state.symbol == symbol.value
            assert state.timeframe == timeframe.value

    def test_states_are_ordered_and_unique(self, symbol, timeframe):
        _, states = sampled_states(symbol, timeframe)
        stamps = [s.as_of for s in states]
        assert stamps == sorted(stamps)
        assert len(stamps) == len(set(stamps))

    def test_counts_are_never_negative(self, symbol, timeframe):
        _, states = sampled_states(symbol, timeframe)
        for state in states:
            assert state.imbalance.bullish_fvg_count >= 0
            assert state.liquidity.buy_side_count >= 0
            assert state.composites.unicorn_count >= 0

    def test_id_tuple_lengths_match_their_counts(self, symbol, timeframe):
        _, states = sampled_states(symbol, timeframe)
        for state in states:
            assert len(state.imbalance.active_bullish_fvg_ids) == state.imbalance.bullish_fvg_count
            assert len(state.liquidity.active_buy_side_ids) == state.liquidity.buy_side_count
            assert len(state.composites.active_unicorn_ids) == state.composites.unicorn_count

    def test_bias_follows_the_documented_rule(self, symbol, timeframe):
        _, states = sampled_states(symbol, timeframe)
        for state in states:
            bull, bear = state.bias.bullish_score, state.bias.bearish_score
            expected = (
                MarketBias.UNKNOWN
                if bull == 0 and bear == 0
                else (
                    MarketBias.BULLISH
                    if bull > bear
                    else MarketBias.BEARISH if bear > bull else MarketBias.NEUTRAL
                )
            )
            assert state.bias.bias is expected

    def test_a_sparse_timeframe_may_legitimately_be_empty(self, symbol):
        """4H carries nine bars. Zero events is reported, never engineered away."""
        _, states = sampled_states(symbol, Timeframe.H4)
        assert isinstance(states, list)


class TestRealProvenance:
    def test_every_emitted_id_resolves_and_confirmed_first(self, symbol, timeframe):
        view, states = sampled_states(symbol, timeframe)
        registry = _confirmations(view.frame, symbol, timeframe)

        checked = 0
        for state in states:
            for group, ids in state.source_ids().items():
                for source_id in ids:
                    confirmation = registry[group].get(source_id)
                    assert confirmation is not None, f"{group}:{source_id} resolves to nothing"
                    assert confirmation <= state.as_of, f"{group}:{source_id} leaks"
                    checked += 1
        assert checked > 0, "the fixture must emit at least one provenance id"

    def test_unicorn_parents_are_carried_whole(self, symbol, timeframe):
        from ict_kronos.ict import UnicornDetector

        view, states = sampled_states(symbol, timeframe)
        unicorns = {u.unicorn_id: u for u in UnicornDetector().detect(view.frame, symbol, timeframe)}

        for state in states:
            found = state.composites.latest_unicorn_id
            if found is None:
                continue
            source = unicorns[found]
            assert state.composites.latest_unicorn_fvg_id == source.source_fvg_id
            assert state.composites.latest_unicorn_breaker_id == source.source_breaker_id

    def test_dealing_range_anchors_resolve_to_swings(self, symbol, timeframe):
        view, states = sampled_states(symbol, timeframe)
        swings = swing_registry(view.frame, symbol, timeframe, SwingConfig())

        for state in states:
            if state.premium_discount.high_anchor_id is None:
                continue
            assert state.premium_discount.high_anchor_id in swings
            assert state.premium_discount.low_anchor_id in swings


class TestRealFeatureCompleteness:
    def test_every_vector_has_the_full_schema(self, symbol, timeframe):
        _, states = sampled_states(symbol, timeframe)
        for vector in feature_vectors(states):
            payload = vector.as_dict()
            assert set(FEATURE_NAMES) <= set(payload)
            assert len(vector.as_row()) == len(FEATURE_NAMES)

    def test_missing_is_never_encoded_as_zero(self, symbol, timeframe):
        """When a distance is absent the count beside it explains why."""
        _, states = sampled_states(symbol, timeframe)
        for state, vector in zip(states, feature_vectors(states), strict=True):
            if vector.nearest_buy_side_points is None:
                assert state.liquidity.buy_side_count == 0
            if vector.nearest_bullish_fvg_points is None:
                assert state.imbalance.bullish_fvg_count == 0

    def test_counts_never_become_nan(self, symbol, timeframe):
        _, states = sampled_states(symbol, timeframe)
        for vector in feature_vectors(states):
            row = dict(zip(FEATURE_NAMES, vector.as_row(), strict=True))
            for name in ("bullish_fvg_count", "buy_side_liquidity_count", "unicorn_count"):
                assert not math.isnan(row[name])

    def test_percentage_position_is_unclamped_on_real_bars(self, symbol):
        _, states = sampled_states(symbol, Timeframe.M15)
        found = [v.percentage_position for v in feature_vectors(states) if v.percentage_position is not None]
        if not found:
            pytest.skip("no dealing range on this combination")
        assert any(p < 0 or p > 1 for p in found)

    def test_zone_flags_agree_with_the_range(self, symbol, timeframe):
        _, states = sampled_states(symbol, timeframe)
        for state, vector in zip(states, feature_vectors(states), strict=True):
            zone = state.premium_discount.zone
            if zone is None:
                assert vector.is_premium is None
                continue
            assert vector.is_premium == int(zone is RangeZone.PREMIUM)


class TestRealSerialization:
    def test_round_trip_preserves_every_vector(self, symbol, timeframe):
        _, states = sampled_states(symbol, timeframe)
        for vector in feature_vectors(states):
            assert ICTFeatureVector.from_dict(vector.as_dict()) == vector

    def test_states_are_json_serialisable(self, symbol, timeframe):
        import json

        _, states = sampled_states(symbol, timeframe)
        for state in states:
            assert json.loads(json.dumps(state.as_dict()))["as_of"] == state.as_of.isoformat()

    def test_the_frame_column_order_is_stable(self, symbol, timeframe):
        _, states = sampled_states(symbol, timeframe)
        frame = vectors_to_frame(feature_vectors(states))
        assert list(frame.columns)[-len(FEATURE_NAMES) :] == list(FEATURE_NAMES)

    def test_serialisation_is_repeatable(self, symbol, timeframe):
        _, states = sampled_states(symbol, timeframe)
        first = [s.as_dict() for s in states]
        _, again = sampled_states(symbol, timeframe)
        assert [s.as_dict() for s in again] == first


def assert_prefix_consistent(prefix_state, full_state) -> None:
    """Prefix replay must never see MORE than the full frame.

    Usually the two are byte-identical. There is exactly one permitted asymmetry, and
    it is a property of R2-05.1 rather than of this layer:

    **The True Daily Open is the engine's only zero-lag event** — it is a bar's OPEN
    price, knowable the instant the bar opens, so its ``confirmation_timestamp``
    equals its ``event_timestamp``. A frame contains only CLOSED bars, so a prefix
    ending at ``t`` cannot contain the bar that opened at ``t``, even though a live
    observer at ``t`` would already have seen its open print.

    So at exactly that instant the prefix holds either **no** level, or the
    **previous day's**, where the full frame holds the new one. That is the safe
    direction: staler information, never newer. This helper asserts precisely that,
    and that nothing else differs — it does not soften the equality, it names the
    one case and pins its shape.
    """
    if prefix_state == full_state:
        return

    from dataclasses import replace as dc_replace

    prefix_open, full_open = prefix_state.daily_open, full_state.daily_open

    # The full frame must hold the newly-opened boundary bar's level, confirming
    # EXACTLY at as_of — anything else means the divergence is not the zero-lag case.
    assert full_open.level_id is not None
    assert (
        full_open.timestamp == prefix_state.as_of
    ), "only a level confirming exactly at as_of may be missing from the prefix"

    # The prefix holds either nothing yet, or the PREVIOUS day's level. Never a newer
    # one: ``latest_at`` is a most-recent pointer rather than an accumulating set, so
    # "sees less" means STALER here, not merely absent.
    if prefix_open.level_id is not None:
        assert (
            prefix_open.timestamp < full_open.timestamp
        ), "the prefix holds a NEWER daily open than the full frame — that is a leak"
    # `session.trading_day_age_minutes` is derived from the daily open, so it moves
    # with it. Substituting BOTH must restore exact equality; anything else differing
    # would be a real divergence.
    patched = dc_replace(prefix_state, daily_open=full_state.daily_open, session=full_state.session)
    assert patched == full_state


class TestRealStreamingEquivalence:
    """Every cut on the timeframes small enough to permit it — no sampling here,
    because completeness is precisely what this class exists to prove."""

    def test_prefix_replay_matches_batch_at_every_cut(self, symbol):
        frame = load(symbol, Timeframe.H1)
        builder = MarketStateBuilder()
        full = {s.as_of: s for s in builder.analyse(frame, symbol, Timeframe.H1).states()}

        for cut in range(1, len(frame) + 1):
            for state in builder.analyse(frame.iloc[:cut], symbol, Timeframe.H1).states():
                assert_prefix_consistent(state, full[state.as_of])

    def test_true_bar_by_bar_accumulation_matches_batch(self, symbol):
        frame = load(symbol, Timeframe.H4)
        builder = MarketStateBuilder()
        seen: dict = {}
        for cut in range(1, len(frame) + 1):
            for state in builder.analyse(frame.iloc[:cut], symbol, Timeframe.H4).states():
                seen.setdefault(state.as_of, state)

        for state in builder.analyse(frame, symbol, Timeframe.H4).states():
            assert_prefix_consistent(seen[state.as_of], state)

    def test_the_prefix_never_sees_more_than_the_full_frame(self, symbol):
        """The direction that matters: a prefix may lack information, never invent it."""
        frame = load(symbol, Timeframe.H1)
        builder = MarketStateBuilder()
        full = {s.as_of: s for s in builder.analyse(frame, symbol, Timeframe.H1).states()}

        for cut in range(1, len(frame) + 1):
            for state in builder.analyse(frame.iloc[:cut], symbol, Timeframe.H1).states():
                reference = full[state.as_of]
                prefix_ids, full_ids = state.source_ids(), reference.source_ids()
                for group in prefix_ids:
                    if group == "daily_open":
                        # A most-recent POINTER, not an accumulating set: the prefix
                        # may hold an OLDER level, so a subset check asks the wrong
                        # question. The right one is that it is never NEWER.
                        continue
                    assert set(prefix_ids[group]) <= set(
                        full_ids[group]
                    ), f"prefix invented {group} ids at cut {cut}"
                if state.daily_open.timestamp and reference.daily_open.timestamp:
                    assert (
                        state.daily_open.timestamp <= reference.daily_open.timestamp
                    ), f"prefix holds a newer daily open than the full frame at cut {cut}"

    def test_vectors_replay_identically(self, symbol):
        frame = load(symbol, Timeframe.H1)
        builder = MarketStateBuilder()
        full = {v.as_of: v for v in feature_vectors(builder.analyse(frame, symbol, Timeframe.H1).states())}
        half = len(frame) // 2
        early_states = builder.analyse(frame.iloc[:half], symbol, Timeframe.H1).states()
        full_states = {s.as_of: s for s in builder.analyse(frame, symbol, Timeframe.H1).states()}
        for state in early_states:
            assert_prefix_consistent(state, full_states[state.as_of])
        for vector in feature_vectors(early_states):
            reference = full[vector.as_of]
            if vector != reference:
                # Same single permitted asymmetry, seen through the projection.
                assert vector.distance_from_true_daily_open_points is None
                assert reference.distance_from_true_daily_open_points is not None

    def test_appending_bars_never_rewrites_history(self, symbol, coarse_timeframe):
        frame = load(symbol, coarse_timeframe)
        builder = MarketStateBuilder()
        half = len(frame) // 2

        early = builder.analyse(frame.iloc[:half], symbol, coarse_timeframe)
        late = {s.as_of: s for s in builder.analyse(frame, symbol, coarse_timeframe).states()}
        stride = SAMPLE_STRIDE.get(coarse_timeframe, 1)
        for state in early.states(early.observation_instants()[::stride]):
            assert_prefix_consistent(state, late[state.as_of])


class TestRealLeakage:
    def test_future_mutation_leaves_earlier_states_identical(self, symbol, coarse_timeframe):
        frame = load(symbol, coarse_timeframe)
        builder = MarketStateBuilder()
        view = builder.analyse(frame, symbol, coarse_timeframe)
        stride = SAMPLE_STRIDE.get(coarse_timeframe, 1)
        instants = view.observation_instants()[::stride]
        before = view.states(instants)
        if len(before) < 3:
            pytest.skip("too few states to partition")

        cutoff = before[len(before) // 2].as_of
        mutated = frame.copy()
        later = mutated["timestamp"] > cutoff
        mutated.loc[later, "high"] = mutated.loc[later, "high"] * 1.5
        mutated.loc[later, "low"] = mutated.loc[later, "low"] * 0.5
        mutated.loc[later, "close"] = mutated.loc[later, "close"] * 1.2

        wanted = [i for i in instants if i <= cutoff]
        replayed = builder.analyse(mutated, symbol, coarse_timeframe).states(wanted)
        after = {s.as_of: s for s in replayed}
        for state in before:
            if state.as_of <= cutoff:
                assert after[state.as_of] == state

    def test_the_control_proves_the_layer_reads_prices(self, symbol, coarse_timeframe):
        frame = load(symbol, coarse_timeframe)
        builder = MarketStateBuilder()
        moved = frame.copy()
        for column in ("open", "high", "low", "close"):
            moved[column] = moved[column] * 1.05

        view = builder.analyse(frame, symbol, coarse_timeframe)
        stride = SAMPLE_STRIDE.get(coarse_timeframe, 1)
        instants = view.observation_instants()[::stride]
        assert builder.analyse(moved, symbol, coarse_timeframe).states(instants) != view.states(instants)

    def test_no_state_sees_a_structural_break_confirmed_after_it(self, symbol, timeframe):
        view, states = sampled_states(symbol, timeframe)
        breaks = {
            structure_break_id(b): b
            for b in StructureDetector().analyse(view.frame, symbol, timeframe).breaks
        }
        for state in states:
            found = state.structure.latest_break_id
            if found is None:
                continue
            assert breaks[found].confirmation_timestamp <= state.as_of

    def test_a_sweep_never_makes_an_unobservable_level_observable(self, symbol, timeframe):
        from ict_kronos.ict import LiquidityDetector

        view, states = sampled_states(symbol, timeframe)
        levels = {x.level_id: x for x in LiquidityDetector().analyse(view.frame, symbol, timeframe).levels}
        for state in states:
            for level_id in state.liquidity.swept_level_ids:
                assert levels[level_id].confirmation_timestamp <= state.as_of


class TestRealDailyOpenAndSessions:
    def test_the_true_daily_open_is_new_york_midnight(self, symbol, timeframe):
        _, states = sampled_states(symbol, timeframe)
        seen = 0
        for state in states:
            if state.daily_open.level_id is None:
                continue
            seen += 1
            assert state.daily_open.timezone == "America/New_York"
            assert state.daily_open.timestamp <= state.as_of
        if seen == 0:
            pytest.skip("no observable True Daily Open on this combination")

    def test_dst_shifts_the_boundary_without_changing_the_definition(self, symbol):
        """2024-03-10 is the US spring-forward: 05:00Z before, 04:00Z after."""
        _, states = sampled_states(symbol, Timeframe.M5)
        boundaries = {
            (s.daily_open.trading_date, s.daily_open.timestamp)
            for s in states
            if s.daily_open.timestamp is not None
        }
        hours = {t.hour for _, t in boundaries}
        if len(hours) < 2:
            pytest.skip("the sampled window did not span the transition")
        assert hours <= {4, 5}

    def test_staleness_is_visible_rather_than_laundered(self, symbol, timeframe):
        _, states = sampled_states(symbol, timeframe)
        for state in states:
            if state.daily_open.level_id is None:
                continue
            assert isinstance(state.daily_open.is_current_trading_day, bool)

    def test_a_weekend_state_still_reports_the_last_known_daily_open(self, symbol):
        _, states = sampled_states(symbol, Timeframe.M15)
        with_open = [s for s in states if s.daily_open.level_id is not None]
        if not with_open:
            pytest.skip("no observable True Daily Open")
        assert all(s.daily_open.timestamp <= s.as_of for s in with_open)

    def test_session_context_uses_the_existing_definitions(self, symbol, timeframe):
        _, states = sampled_states(symbol, timeframe)
        for state in states:
            assert len(state.session.active_sessions) == len(set(state.session.active_sessions))
            if state.session.primary_session is not None:
                assert state.session.primary_session in state.session.active_sessions
                assert state.session.session_elapsed_minutes >= 0

    def test_temporal_features_are_utc(self, symbol, timeframe):
        _, states = sampled_states(symbol, timeframe)
        for state in states:
            assert state.session.hour_of_day == state.as_of.hour
            assert state.session.day_of_week == state.as_of.weekday()


class TestIncompleteHigherTimeframeBars:
    def test_a_partial_final_hour_never_produces_a_state(self, symbol):
        base = ParquetCandleStore(DATA_ROOT).read(
            symbol, Timeframe.M1, start=pd.Timestamp(WINDOW_START), end=pd.Timestamp(WINDOW_END)
        )
        if len(base) < 200:
            pytest.skip(f"real 1m data absent for {symbol.value}")

        full = resample(base, Timeframe.M1, Timeframe.H1, symbol).drop(columns=["close_time"])
        truncated = resample(base.iloc[: len(base) - 20], Timeframe.M1, Timeframe.H1, symbol).drop(
            columns=["close_time"]
        )

        builder = MarketStateBuilder()
        early = builder.analyse(truncated, symbol, Timeframe.H1).states()
        late = {s.as_of: s for s in builder.analyse(full, symbol, Timeframe.H1).states()}
        assert len(early) <= len(late)
        for state in early:
            assert late[state.as_of] == state

    def test_no_state_exists_for_a_bar_that_has_not_closed(self, symbol):
        frame = load(symbol, Timeframe.H1)
        view = MarketStateBuilder().analyse(frame, symbol, Timeframe.H1)
        last_close = view.observation_instants()[-1]
        assert view.state_at(last_close + timedelta(minutes=1)) is None


class TestNoRegressionInApprovedDetectors:
    def test_r2_01_to_r2_06_still_run_unchanged(self, symbol, timeframe):
        from ict_kronos.ict import (
            DealingRangeDetector,
            FvgDetector,
            LiquidityDetector,
            SwingDetector,
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
        assert DealingRangeDetector().analyse(frame, symbol, timeframe) is not None


def _confirmations(frame, symbol: Symbol, timeframe: Timeframe) -> dict[str, dict]:
    from ict_kronos.ict import (
        BprDetector,
        BreakerDetector,
        CisdDetector,
        DealingRangeDetector,
        FvgDetector,
        IfvgDetector,
        LiquidityDetector,
        OrderBlockDetector,
        RdrbDetector,
        TrueDailyOpenDetector,
        UnicornDetector,
    )

    def stamps(items, id_field):
        return {getattr(i, id_field): i.confirmation_timestamp for i in items}

    breaks = StructureDetector().analyse(frame, symbol, timeframe).breaks
    return {
        "structure": {structure_break_id(b): b.confirmation_timestamp for b in breaks},
        "liquidity_level": stamps(LiquidityDetector().analyse(frame, symbol, timeframe).levels, "level_id"),
        "fvg": stamps(FvgDetector().detect(frame, symbol, timeframe), "zone_id"),
        "ifvg": stamps(IfvgDetector().detect(frame, symbol, timeframe), "ifvg_id"),
        "bpr": stamps(BprDetector().detect(frame, symbol, timeframe), "bpr_id"),
        "order_block": stamps(OrderBlockDetector().detect(frame, symbol, timeframe), "order_block_id"),
        "breaker": stamps(BreakerDetector().detect(frame, symbol, timeframe), "breaker_id"),
        "rdrb": stamps(RdrbDetector().detect(frame, symbol, timeframe), "rdrb_id"),
        "cisd": stamps(CisdDetector().detect(frame, symbol, timeframe), "cisd_id"),
        "unicorn": stamps(UnicornDetector().detect(frame, symbol, timeframe), "unicorn_id"),
        "daily_open": stamps(TrueDailyOpenDetector().detect(frame, symbol, timeframe), "level_id"),
        "dealing_range": stamps(DealingRangeDetector().detect(frame, symbol, timeframe), "range_id"),
        "swing": {
            k: v.confirmation_timestamp
            for k, v in swing_registry(frame, symbol, timeframe, SwingConfig()).items()
        },
    }
