"""R2-07 ICTFeatureVector — the flat projection, its schema, and its leakage contract.

The vector reads a state that was already built point-in-time and never touches a
frame, a detector or a timestamp comparison — so it cannot leak on its own. What it
*can* do wrong is encode: silently turning "unknown" into `0`, renumbering a category,
or changing column order between runs. Those are what most of this file tests.

The leakage and streaming classes at the end test the layer as a whole, because that is
the only level at which "the vector at time t used only information available at t" is
a meaningful statement.
"""

from __future__ import annotations

import math
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame
from ict_kronos.ict import (
    FEATURE_NAMES,
    FEATURE_VERSION,
    ICTFeatureVector,
    MarketStateBuilder,
    RangeZone,
    feature_vectors,
    vectors_to_frame,
)
from ict_kronos.ict.feature_vector import (
    BIAS_CODES,
    BREAK_TYPE_CODES,
    DELIVERY_STATE_CODES,
    DIRECTION_CODES,
    STRUCTURE_STATE_CODES,
    ZONE_CODES,
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


TREND = [
    1.0000, 1.0020, 1.0040, 1.0060, 1.0030, 1.0010, 1.0025, 1.0050,
    1.0080, 1.0100, 1.0070, 1.0120, 1.0090, 1.0140, 1.0110, 1.0160,
    1.0130, 1.0180, 1.0150, 1.0200, 1.0170, 1.0090, 1.0050, 1.0020,
]  # fmt: skip

#: Flat and overlapping: nothing confirms, so every optional feature is missing.
FLAT = [1.0000] * 6


def states(prices=TREND):
    return MarketStateBuilder().analyse(bars(prices), SYM, M5).states()


def vectors(prices=TREND):
    return feature_vectors(states(prices))


class TestSchema:
    def test_the_schema_is_a_module_level_tuple(self):
        assert isinstance(FEATURE_NAMES, tuple)
        assert len(FEATURE_NAMES) == 56

    def test_every_schema_name_is_a_real_field(self):
        vector = vectors()[-1]
        for name in FEATURE_NAMES:
            assert hasattr(vector, name), name

    def test_the_schema_has_no_duplicates(self):
        assert len(FEATURE_NAMES) == len(set(FEATURE_NAMES))

    def test_column_names_are_the_schema(self):
        assert ICTFeatureVector.column_names() == FEATURE_NAMES

    def test_the_version_is_carried_on_every_vector(self):
        for vector in vectors():
            assert vector.feature_version == FEATURE_VERSION

    def test_column_order_is_identical_across_runs(self):
        one = list(vectors()[-1].as_dict())
        two = list(vectors()[-1].as_dict())
        assert one == two

    def test_the_row_matches_the_schema_length(self):
        assert len(vectors()[-1].as_row()) == len(FEATURE_NAMES)

    def test_identity_columns_are_not_features(self):
        """symbol/timeframe/as_of/feature_version are identity, not model inputs."""
        for name in ("symbol", "timeframe", "as_of", "feature_version"):
            assert name not in FEATURE_NAMES

    def test_the_schema_is_declared_not_derived(self):
        """A field reordering must not renumber an existing dataset's columns."""
        from pathlib import Path

        source = Path("ict_kronos/ict/feature_vector.py").read_text(encoding="utf-8")
        assert "FEATURE_NAMES: tuple[str, ...] = (" in source
        assert "FEATURE_NAMES = tuple(f.name for f in fields" not in source


class TestEncodings:
    def test_direction_codes_are_signed(self):
        assert DIRECTION_CODES == {"bearish": -1, "neutral": 0, "bullish": 1}

    def test_every_encoding_table_is_declared_not_fitted(self):
        for table in (
            DIRECTION_CODES,
            STRUCTURE_STATE_CODES,
            DELIVERY_STATE_CODES,
            ZONE_CODES,
            BIAS_CODES,
            BREAK_TYPE_CODES,
        ):
            assert table
            assert all(isinstance(k, str) and isinstance(v, int) for k, v in table.items())

    def test_bias_unknown_and_neutral_both_code_to_zero(self):
        """A documented lossy projection: the STATE keeps them apart."""
        assert BIAS_CODES["unknown"] == 0
        assert BIAS_CODES["neutral"] == 0

    def test_the_state_still_distinguishes_unknown_from_neutral(self):
        from ict_kronos.ict import MarketBias

        assert MarketBias.UNKNOWN is not MarketBias.NEUTRAL

    def test_zone_flags_agree_with_the_zone(self):
        for state, vector in zip(states(), vectors(), strict=True):
            zone = state.premium_discount.zone
            if zone is None:
                assert vector.is_premium is None
                continue
            assert vector.is_premium == int(zone is RangeZone.PREMIUM)
            assert vector.is_discount == int(zone is RangeZone.DISCOUNT)
            assert vector.is_equilibrium == int(zone is RangeZone.EQUILIBRIUM)

    def test_exactly_one_zone_flag_is_set_when_a_range_exists(self):
        for vector in vectors():
            if vector.is_premium is None:
                continue
            assert vector.is_premium + vector.is_discount + vector.is_equilibrium == 1

    def test_sweep_side_is_signed_by_consequence_not_by_side(self):
        """Taking sell-side liquidity is bullish-leaning, buy-side bearish-leaning."""
        from ict_kronos.ict.feature_vector import _sweep_side_code
        from ict_kronos.ict.liquidity import LiquiditySide

        assert _sweep_side_code(LiquiditySide.SELL_SIDE) == 1
        assert _sweep_side_code(LiquiditySide.BUY_SIDE) == -1
        assert _sweep_side_code(None) is None


class TestMissingValues:
    def test_missing_is_none_in_the_dict_never_zero(self):
        payload = vectors(FLAT)[-1].as_dict()
        assert payload["percentage_position"] is None
        assert payload["nearest_bullish_fvg_points"] is None
        assert payload["is_premium"] is None

    def test_missing_is_nan_in_the_row_never_zero(self):
        vector = vectors(FLAT)[-1]
        row = dict(zip(FEATURE_NAMES, vector.as_row(), strict=True))
        assert math.isnan(row["percentage_position"])
        assert math.isnan(row["nearest_bullish_fvg_points"])

    def test_counts_stay_real_zeros(self):
        """0 means 'none active' and must NOT become nan."""
        vector = vectors(FLAT)[-1]
        row = dict(zip(FEATURE_NAMES, vector.as_row(), strict=True))
        assert row["bullish_fvg_count"] == 0.0
        assert row["buy_side_liquidity_count"] == 0.0
        assert not math.isnan(row["bullish_fvg_count"])

    def test_zero_distance_and_missing_distance_are_distinguishable(self):
        """The whole point of §7: a model must not be told price sits on a level
        that does not exist."""
        vector = vectors(FLAT)[-1]
        assert vector.buy_side_liquidity_count == 0
        assert vector.nearest_buy_side_points is None

    def test_the_zone_flags_are_none_together(self):
        vector = vectors(FLAT)[-1]
        assert (vector.is_premium, vector.is_discount, vector.is_equilibrium) == (None, None, None)

    def test_no_feature_uses_a_numeric_sentinel_for_missing(self):
        payload = vectors(FLAT)[-1].as_dict()
        for name in FEATURE_NAMES:
            value = payload[name]
            assert value is None or not (isinstance(value, float) and math.isnan(value))


class TestFeatureValues:
    def test_close_is_the_bars_close(self):
        for state, vector in zip(states(), vectors(), strict=True):
            assert vector.close == pytest.approx(state.bar.close)

    def test_percentage_position_is_carried_through_unclamped(self):
        found = [v.percentage_position for v in vectors() if v.percentage_position is not None]
        assert found
        assert any(p < 0 or p > 1 for p in found), "the fixture must leave the range"

    def test_counts_mirror_the_state(self):
        for state, vector in zip(states(), vectors(), strict=True):
            assert vector.bullish_fvg_count == state.imbalance.bullish_fvg_count
            assert vector.unicorn_count == state.composites.unicorn_count
            assert vector.rdrb_count == state.composites.rdrb_count

    def test_bias_counts_mirror_the_state(self):
        for state, vector in zip(states(), vectors(), strict=True):
            assert vector.bullish_evidence_count == state.bias.bullish_score
            assert vector.bearish_evidence_count == state.bias.bearish_score

    def test_distance_from_structural_level_is_signed_and_in_points(self):
        for state, vector in zip(states(), vectors(), strict=True):
            level = state.structure.latest_break_level
            if level is None:
                assert vector.distance_from_structural_level_points is None
                continue
            expected = (state.bar.close - level) / SYM.spec.point_value
            assert vector.distance_from_structural_level_points == pytest.approx(expected)

    def test_temporal_features_are_utc(self):
        for state, vector in zip(states(), vectors(), strict=True):
            assert vector.hour_of_day == state.as_of.hour
            assert vector.day_of_week == state.as_of.weekday()

    def test_choch_count_is_zero_under_the_synonym_policy(self):
        """CHoCH is MSS by default; zero is correct, not a gap."""
        assert all(v.choch_count == 0 for v in vectors())

    def test_no_label_or_target_field_exists(self):
        banned = ("label", "target", "future", "next_", "forward_", "return", "pnl", "profit")
        for name in FEATURE_NAMES:
            assert not any(token in name for token in banned), name


class TestSerialization:
    def test_round_trip_preserves_the_vector_exactly(self):
        for vector in vectors():
            assert ICTFeatureVector.from_dict(vector.as_dict()) == vector

    def test_round_trip_preserves_missing_values(self):
        vector = vectors(FLAT)[-1]
        restored = ICTFeatureVector.from_dict(vector.as_dict())
        assert restored.percentage_position is None
        assert restored == vector

    def test_the_dict_is_json_safe(self):
        import json

        text = json.dumps(vectors()[-1].as_dict())
        assert json.loads(text)["feature_version"] == FEATURE_VERSION

    def test_timestamps_serialise_as_iso_utc(self):
        assert vectors()[-1].as_dict()["as_of"].endswith("+00:00")

    def test_the_frame_uses_the_schema_order(self):
        frame = vectors_to_frame(vectors())
        assert list(frame.columns) == [
            "symbol",
            "timeframe",
            "as_of",
            "feature_version",
            *FEATURE_NAMES,
        ]

    def test_an_empty_frame_keeps_the_schema(self):
        frame = vectors_to_frame([])
        assert len(frame) == 0
        assert list(frame.columns)[-len(FEATURE_NAMES) :] == list(FEATURE_NAMES)

    def test_serialisation_is_deterministic(self):
        assert vectors()[-1].as_dict() == vectors()[-1].as_dict()
        assert vectors()[-1].as_row() == vectors()[-1].as_row()

    def test_vectors_are_immutable(self):
        with pytest.raises(FrozenInstanceError):
            vectors()[-1].close = 2.0


class TestBatchEqualsStreaming:
    """The whole layer, not just the vector — that is the only level at which
    'the vector at t used only information available at t' means anything."""

    @staticmethod
    def _vectors_for(frame):
        return feature_vectors(MarketStateBuilder().analyse(frame, SYM, M5).states())

    def test_prefix_replay_matches_batch_at_every_cut(self):
        frame = bars(TREND)
        full = self._vectors_for(frame)
        by_time = {v.as_of: v for v in full}

        for cut in range(1, len(frame) + 1):
            replayed = self._vectors_for(frame.iloc[:cut])
            for vector in replayed:
                assert vector == by_time[vector.as_of], f"diverged at cut {cut}"

    def test_true_bar_by_bar_accumulation_matches_batch(self):
        frame = bars(TREND)
        seen: dict = {}
        for cut in range(1, len(frame) + 1):
            for vector in self._vectors_for(frame.iloc[:cut]):
                seen.setdefault(vector.as_of, vector)

        for vector in self._vectors_for(frame):
            assert seen[vector.as_of] == vector

    def test_the_state_stream_replays_identically(self):
        frame = bars(TREND)
        full = {s.as_of: s for s in MarketStateBuilder().analyse(frame, SYM, M5).states()}
        for cut in range(1, len(frame) + 1):
            for state in MarketStateBuilder().analyse(frame.iloc[:cut], SYM, M5).states():
                assert state == full[state.as_of], f"state diverged at cut {cut}"

    def test_appending_bars_never_rewrites_an_earlier_vector(self):
        frame = bars(TREND)
        early = self._vectors_for(frame.iloc[:14])
        late = {v.as_of: v for v in self._vectors_for(frame)}
        for vector in early:
            assert late[vector.as_of] == vector


class TestLeakage:
    """Adversarial. Every inertness assertion is paired with a control proving the
    layer reads prices at all — without it, a detector that ignored its input entirely
    would pass."""

    @staticmethod
    def _states_for(frame):
        return MarketStateBuilder().analyse(frame, SYM, M5).states()

    def _mutate_after(self, frame, cutoff, **factors):
        mutated = frame.copy()
        later = mutated["timestamp"] > cutoff
        for column, factor in factors.items():
            mutated.loc[later, column] = mutated.loc[later, column] * factor
        return mutated

    def test_future_bars_cannot_change_an_earlier_state(self):
        frame = bars(TREND)
        before = self._states_for(frame)
        cutoff = before[len(before) // 2].as_of

        mutated = self._mutate_after(frame, cutoff, high=1.5, low=0.5, close=1.2)
        after = {s.as_of: s for s in self._states_for(mutated)}

        for state in before:
            if state.as_of <= cutoff:
                assert after[state.as_of] == state

    def test_future_bars_cannot_change_an_earlier_vector(self):
        frame = bars(TREND)
        before = feature_vectors(self._states_for(frame))
        cutoff = before[len(before) // 2].as_of

        mutated = self._mutate_after(frame, cutoff, high=1.5, low=0.5, close=1.2)
        after = {v.as_of: v for v in feature_vectors(self._states_for(mutated))}

        for vector in before:
            if vector.as_of <= cutoff:
                assert after[vector.as_of] == vector

    def test_the_control_proves_the_layer_reads_prices(self):
        frame = bars(TREND)
        moved = bars([p * 1.05 for p in TREND])
        assert self._states_for(moved) != self._states_for(frame)

    def test_a_future_swing_anchor_cannot_reach_back(self):
        """Extending the series creates new pivots; earlier states must not see them."""
        short = bars(TREND[:16])
        longer = bars(TREND)
        early = self._states_for(short)
        late = {s.as_of: s for s in self._states_for(longer)}
        for state in early:
            assert late[state.as_of] == state

    @pytest.mark.parametrize(
        "component",
        [
            "structure",
            "liquidity",
            "imbalance",
            "institutional",
            "composites",
            "daily_open",
            "premium_discount",
        ],
    )
    def test_no_component_changes_when_the_future_is_wrecked(self, component):
        """Covers future structural breaks, sweeps, FVGs, OBs, Breakers, Unicorns,
        dealing-range anchors and the True Daily Open in one parametrised sweep."""
        frame = bars(TREND)
        before = self._states_for(frame)
        cutoff = before[len(before) // 2].as_of

        mutated = self._mutate_after(frame, cutoff, high=1.4, low=0.6, close=1.15)
        after = {s.as_of: s for s in self._states_for(mutated)}

        for state in before:
            if state.as_of <= cutoff:
                assert getattr(after[state.as_of], component) == getattr(state, component)

    def test_the_confirming_bars_wick_cannot_retroactively_alter_a_state(self):
        frame = bars(TREND)
        before = self._states_for(frame)
        target = before[len(before) // 2].as_of

        widened = frame.copy()
        at = widened["timestamp"] + M5.duration == target
        widened.loc[at, "high"] = widened.loc[at, "high"] + 0.0009
        widened.loc[at, "low"] = widened.loc[at, "low"] - 0.0009

        after = {s.as_of: s for s in self._states_for(widened)}
        for state in before:
            if state.as_of < target:
                assert after[state.as_of] == state

    def test_the_naive_final_state_implementation_disagrees(self):
        """L4 — the tempting shortcut: build one state from the END of the frame and
        reuse it for every row. It is a leak, and it is proven to disagree."""
        frame = bars(TREND)
        honest = self._states_for(frame)
        naive = honest[-1]

        differing = [s for s in honest[:-1] if s.structure != naive.structure]
        assert differing, "the fixture must evolve, or this proves nothing"

    def test_no_state_sees_an_event_confirmed_after_it(self):
        """The whole-vector criterion, checked against every emitted id."""
        frame = bars(TREND)
        registries = _confirmations(frame)

        for state in self._states_for(frame):
            for group, ids in state.source_ids().items():
                for source_id in ids:
                    confirmation = registries[group].get(source_id)
                    assert confirmation is not None
                    assert confirmation <= state.as_of, f"{group}:{source_id} leaks"


def _confirmations(frame) -> dict[str, dict]:
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
        StructureDetector,
        SwingConfig,
        TrueDailyOpenDetector,
        UnicornDetector,
        structure_break_id,
        swing_registry,
    )

    def stamps(items, id_field):
        return {getattr(i, id_field): i.confirmation_timestamp for i in items}

    breaks = StructureDetector().analyse(frame, SYM, M5).breaks
    return {
        "structure": {structure_break_id(b): b.confirmation_timestamp for b in breaks},
        "liquidity_level": stamps(LiquidityDetector().analyse(frame, SYM, M5).levels, "level_id"),
        "fvg": stamps(FvgDetector().detect(frame, SYM, M5), "zone_id"),
        "ifvg": stamps(IfvgDetector().detect(frame, SYM, M5), "ifvg_id"),
        "bpr": stamps(BprDetector().detect(frame, SYM, M5), "bpr_id"),
        "order_block": stamps(OrderBlockDetector().detect(frame, SYM, M5), "order_block_id"),
        "breaker": stamps(BreakerDetector().detect(frame, SYM, M5), "breaker_id"),
        "rdrb": stamps(RdrbDetector().detect(frame, SYM, M5), "rdrb_id"),
        "cisd": stamps(CisdDetector().detect(frame, SYM, M5), "cisd_id"),
        "unicorn": stamps(UnicornDetector().detect(frame, SYM, M5), "unicorn_id"),
        "daily_open": stamps(TrueDailyOpenDetector().detect(frame, SYM, M5), "level_id"),
        "dealing_range": stamps(DealingRangeDetector().detect(frame, SYM, M5), "range_id"),
        "swing": {
            k: v.confirmation_timestamp for k, v in swing_registry(frame, SYM, M5, SwingConfig()).items()
        },
    }


class TestTheVectorTouchesNothingButState:
    """Structural guard: the projection must have no way to leak."""

    @staticmethod
    def _code() -> str:
        from tests.test_market_state import _code_of

        return _code_of("ict_kronos/ict/feature_vector.py")

    def test_it_never_reads_a_frame_or_a_detector(self):
        """It may CONSTRUCT a DataFrame as output; it may not READ bars or detectors."""
        code = self._code()
        for banned in ("detect(", "analyse(", "with_close_time", ".iloc[", '["close"]', "to_numpy"):
            assert banned not in code, f"feature_vector.py reaches past the state: {banned!r}"

    def test_it_never_compares_a_confirmation_timestamp(self):
        code = self._code()
        assert "confirmation_timestamp" not in code

    def test_it_imports_the_state_layer_only(self):
        code = self._code()
        assert "from .market_state import" in code
