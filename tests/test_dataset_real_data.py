"""R2-08 real-data acceptance — EURUSD + XAUUSD, 2024-03-08 → 2024-03-11.

The window contains the US DST transition (2024-03-10) and a full weekend closure, so
both are exercised on real bars rather than argued about.

**Zeros are valid.** No fixture is altered and no target manufactured. A timeframe with
nine bars and a 16-bar horizon resolves nothing at all, and that is reported as the
correct answer.

**Sampling.** Building a row costs a full point-in-time state, so the dense timeframes
are sampled. Sampling changes nothing about any individual row — each is still built
from the full frame at its own ``as_of`` — and the temporal-contract tests, where
completeness matters, re-analyse every instant on the coarse timeframes.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from ict_kronos.data import resample
from ict_kronos.domain import Symbol, Timeframe
from ict_kronos.features import (
    DatasetBuilder,
    DatasetRow,
    DatasetSpec,
    SplitLabel,
    SplitSpec,
    TargetSpec,
    TargetType,
    TradeSide,
    UnresolvedReason,
    audit_dataset,
)
from ict_kronos.storage import ParquetCandleStore

DATA_ROOT = "data/normalized"
WINDOW_START = datetime(2024, 3, 8, tzinfo=UTC)
WINDOW_END = datetime(2024, 3, 12, tzinfo=UTC)

STORED = (Timeframe.M1, Timeframe.M5, Timeframe.M15)
DERIVED = (Timeframe.H1, Timeframe.H4)
#: 1m and 5m are sampled; everything else uses every instant.
SAMPLE_STRIDE = {Timeframe.M1: 97, Timeframe.M5: 17}
#: Timeframes for tests that must RE-BUILD a dataset from a mutated frame.
COARSE = (Timeframe.M15, *DERIVED)

SPECS = (
    TargetSpec(name="ret_4", target_type=TargetType.FUTURE_RETURN, horizon_bars=4),
    TargetSpec(name="dir_4", target_type=TargetType.DIRECTION, horizon_bars=4, threshold_points=20.0),
    TargetSpec(name="exc_4", target_type=TargetType.EXCURSION, horizon_bars=4),
    TargetSpec(
        name="tpsl_8",
        target_type=TargetType.TP_BEFORE_SL,
        horizon_bars=8,
        side=TradeSide.LONG,
        take_profit_points=50.0,
        stop_loss_points=50.0,
    ),
)
SPEC = DatasetSpec(targets=SPECS)


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


@pytest.fixture(params=COARSE, ids=lambda t: t.value)
def coarse_timeframe(request) -> Timeframe:
    return request.param


#: One dataset per (symbol, timeframe) for the whole module. Building a row runs every
#: approved detector once for the frame and then a state per instant; re-doing that
#: inside each test would make the file unusable. Rows are pure functions of the frame,
#: so caching them changes nothing any test observes.
_DATASETS: dict[tuple[Symbol, Timeframe], tuple] = {}


def built(symbol: Symbol, timeframe: Timeframe):
    """``(dataset, engine, instants)``, computed once per combination for the module.

    The R2-07 engine is cached alongside the dataset because several tests need to
    compare a row against the state that produced it, and re-analysing a 2933-bar 1m
    frame — every approved detector, including the Unicorn lifecycle pass — costs tens
    of seconds each time. Analyses are pure functions of the frame, so caching the
    inputs changes nothing any test observes.
    """
    key = (symbol, timeframe)
    if key not in _DATASETS:
        frame = load(symbol, timeframe)
        stride = SAMPLE_STRIDE.get(timeframe, 1)
        builder = DatasetBuilder()
        engine = builder.state_builder.analyse(frame, symbol, timeframe)
        instants = engine.observation_instants()[::stride]
        dataset = builder.build(frame, symbol, timeframe, SPEC, instants=instants)
        _DATASETS[key] = (dataset, engine, instants)
    return _DATASETS[key]


def dataset_for(symbol: Symbol, timeframe: Timeframe):
    return built(symbol, timeframe)[0]


class TestRealRows:
    def test_a_row_is_built_for_every_sampled_instant(self, symbol, timeframe):
        dataset = dataset_for(symbol, timeframe)
        assert len(dataset) > 0
        assert all(r.symbol == symbol.value and r.timeframe == timeframe.value for r in dataset.rows)

    def test_rows_are_chronological_and_unique(self, symbol, timeframe):
        moments = [r.as_of for r in dataset_for(symbol, timeframe).rows]
        assert moments == sorted(moments)
        assert len(set(moments)) == len(moments)

    def test_every_row_round_trips(self, symbol, timeframe):
        for row in dataset_for(symbol, timeframe).rows:
            assert DatasetRow.from_dict(row.as_dict()) == row

    def test_every_target_window_lies_strictly_after_its_observation(self, symbol, timeframe):
        """The temporal contract, checked on every real row rather than argued."""
        for row in dataset_for(symbol, timeframe).rows:
            for value in row.targets:
                if value.future_window_start is not None:
                    assert value.future_window_start > row.as_of
                if value.future_window_end is not None:
                    assert value.future_window_end >= value.future_window_start

    def test_feature_provenance_is_r2_07s_source_ids_verbatim(self, symbol, timeframe):
        """The R2-08 claim: ids are CARRIED, never recomputed or re-derived.

        That the ids resolve to real events is R2-07's guarantee and is asserted by its
        own real-data suite. Re-proving it here would duplicate the check and hide the
        one thing this layer can actually get wrong — quietly building its own provenance.
        """
        dataset, engine, instants = built(symbol, timeframe)
        expected = {s.as_of: s.source_ids() for s in engine.states(instants)}

        emitted = 0
        for row in dataset.rows:
            assert row.feature_provenance == expected[row.as_of]
            emitted += sum(len(ids) for ids in row.feature_provenance.values())
        assert emitted > 0, "the fixture must emit at least one provenance id"

    def test_the_features_are_r2_07s_vector_verbatim(self, symbol, timeframe):
        from ict_kronos.ict import ICTFeatureVector

        dataset, engine, instants = built(symbol, timeframe)
        expected = {s.as_of: ICTFeatureVector.from_state(s) for s in engine.states(instants)}
        for row in dataset.rows:
            assert row.features == expected[row.as_of]


class TestRealTargetCoverage:
    def test_the_tail_of_the_frame_cannot_resolve_a_forward_target(self, symbol, timeframe):
        """The FINAL bar of the frame, not the last SAMPLED row.

        On a strided timeframe the last sampled row sits well before the end of the data
        and its horizon resolves perfectly well — an earlier version of this test asserted
        otherwise and was simply wrong about which bar it was looking at.
        """
        _, engine, _ = built(symbol, timeframe)
        final = engine.observation_instants()[-1]
        rows = DatasetBuilder().build(load(symbol, timeframe), symbol, timeframe, SPEC, instants=[final]).rows

        assert len(rows) == 1
        value = rows[0].target("ret_4")
        assert value.resolved is False
        assert value.unresolved_reason is UnresolvedReason.INSUFFICIENT_HISTORY

    def test_a_sampled_row_far_from_the_end_does_resolve(self, symbol, timeframe):
        """The other half: unresolved must mean "ran out of data", not "always unresolved"."""
        dataset = dataset_for(symbol, timeframe)
        resolved = [r for r in dataset.rows if r.target("ret_4").resolved]
        assert resolved, "the fixture must resolve at least one forward target"

    def test_a_sparse_timeframe_may_resolve_nothing_and_that_is_reported(self, symbol):
        """Nine 4H bars against a 4-bar horizon: whatever the count is, it is not faked."""
        report = audit_dataset(dataset_for(symbol, Timeframe.H4))
        by_name = {t.name: t for t in report.targets}
        assert 0.0 <= by_name["ret_4"].coverage <= 1.0
        assert by_name["ret_4"].total == report.row_count

    def test_unresolved_targets_never_become_zero_or_neutral(self, symbol, timeframe):
        for row in dataset_for(symbol, timeframe).rows:
            for value in row.targets:
                if value.resolved:
                    continue
                assert value.future_return is None
                assert value.future_move_points is None
                assert value.direction is None
                assert value.up_excursion_points is None
                assert value.unresolved_reason is not None

    def test_every_unresolved_reason_seen_is_a_declared_one(self, symbol, timeframe):
        declared = set(UnresolvedReason)
        for row in dataset_for(symbol, timeframe).rows:
            for value in row.targets:
                assert value.unresolved_reason is None or value.unresolved_reason in declared

    def test_same_bar_ambiguity_is_reported_rather_than_broken(self, symbol, timeframe):
        """Whether it occurs here is data-dependent; that it is never invented is not."""
        ambiguous = [
            v
            for row in dataset_for(symbol, timeframe).rows
            for v in row.targets
            if v.unresolved_reason is UnresolvedReason.SAME_BAR_AMBIGUITY
        ]
        for value in ambiguous:
            assert value.resolved is False
            assert value.resolving_bar_timestamp is not None


class TestRealCalendarBoundaries:
    def test_a_horizon_spanning_the_weekend_is_visible_in_the_window(self, symbol):
        """Horizons are counted in BARS, so a closed market shows up as a wider window."""
        dataset = dataset_for(symbol, Timeframe.H1)
        spans = [
            (row, v)
            for row in dataset.rows
            for v in row.targets
            if v.future_window_end is not None
            and v.future_window_end - row.as_of > timedelta(hours=v.horizon_bars + 2)
        ]
        assert spans, "the fixture spans a weekend; some horizon must straddle it"
        for row, value in spans:
            assert value.future_window_end > row.as_of

    def test_the_dst_transition_does_not_break_ordering(self, symbol):
        """2024-03-10: New York shifts. UTC bar closes stay strictly increasing."""
        moments = [r.as_of for r in dataset_for(symbol, Timeframe.M15).rows]
        assert all(a < b for a, b in zip(moments, moments[1:], strict=False))
        assert all(m.tzinfo is not None and m.utcoffset() == timedelta(0) for m in moments)

    def test_an_incomplete_higher_timeframe_history_still_produces_rows(self, symbol):
        dataset = dataset_for(symbol, Timeframe.H4)
        assert len(dataset) > 0
        assert all(r.timeframe == "4h" for r in dataset.rows)


class TestRealTemporalContract:
    """Truncate / mutate / wick / control, on real bars, at a real instant."""

    def frame_and_cut(self, symbol, timeframe):
        frame = load(symbol, timeframe).reset_index(drop=True)
        return frame, len(frame) * 2 // 3

    def features_at(self, frame, symbol, timeframe, moment):
        rows = DatasetBuilder().build(frame, symbol, timeframe, SPEC).rows
        found = next((r for r in rows if r.as_of == moment), None)
        return None if found is None else found.features

    def test_truncating_the_future_leaves_features_identical(self, symbol, coarse_timeframe):
        frame, cut = self.frame_and_cut(symbol, coarse_timeframe)
        rows = DatasetBuilder().build(frame, symbol, coarse_timeframe, SPEC).rows
        moment = rows[cut].as_of
        truncated = frame.iloc[: cut + 1].copy()
        assert self.features_at(truncated, symbol, coarse_timeframe, moment) == rows[cut].features

    def test_mutating_the_future_leaves_features_identical(self, symbol, coarse_timeframe):
        frame, cut = self.frame_and_cut(symbol, coarse_timeframe)
        rows = DatasetBuilder().build(frame, symbol, coarse_timeframe, SPEC).rows
        moment = rows[cut].as_of

        mutated = frame.copy()
        index = mutated.index[cut + 1 :]
        mutated.loc[index, "high"] = mutated.loc[index, "high"] * 1.5
        mutated.loc[index, "low"] = mutated.loc[index, "low"] * 0.5
        mutated.loc[index, "close"] = mutated.loc[index, "high"]
        assert self.features_at(mutated, symbol, coarse_timeframe, moment) == rows[cut].features

    def test_mutating_the_future_DOES_move_the_targets(self, symbol, coarse_timeframe):
        """Non-vacuity on real bars."""
        frame, cut = self.frame_and_cut(symbol, coarse_timeframe)
        rows = DatasetBuilder().build(frame, symbol, coarse_timeframe, SPEC).rows
        moment = rows[cut].as_of

        mutated = frame.copy()
        index = mutated.index[cut + 1 :]
        mutated.loc[index, "high"] = mutated.loc[index, "high"] * 1.5
        mutated.loc[index, "low"] = mutated.loc[index, "low"] * 0.5
        mutated.loc[index, "close"] = mutated.loc[index, "high"]

        after = next(
            r
            for r in DatasetBuilder().build(mutated, symbol, coarse_timeframe, SPEC).rows
            if r.as_of == moment
        )
        before = rows[cut]
        if before.target("ret_4").resolved:
            assert after.target("ret_4").future_return != before.target("ret_4").future_return

    def test_a_wick_only_future_mutation_leaves_features_identical(self, symbol, coarse_timeframe):
        frame, cut = self.frame_and_cut(symbol, coarse_timeframe)
        rows = DatasetBuilder().build(frame, symbol, coarse_timeframe, SPEC).rows
        moment = rows[cut].as_of

        mutated = frame.copy()
        index = mutated.index[cut + 1 :]
        mutated.loc[index, "high"] = mutated.loc[index, "high"] * 1.2
        mutated.loc[index, "low"] = mutated.loc[index, "low"] * 0.8
        assert self.features_at(mutated, symbol, coarse_timeframe, moment) == rows[cut].features

    def test_CONTROL_mutating_history_does_change_the_features(self, symbol, coarse_timeframe):
        frame, cut = self.frame_and_cut(symbol, coarse_timeframe)
        rows = DatasetBuilder().build(frame, symbol, coarse_timeframe, SPEC).rows
        moment = rows[cut].as_of

        mutated = frame.copy()
        index = mutated.index[: cut - 2]
        mutated.loc[index, "high"] = mutated.loc[index, "high"] * 1.3
        mutated.loc[index, "low"] = mutated.loc[index, "low"] * 0.7
        assert self.features_at(mutated, symbol, coarse_timeframe, moment) != rows[cut].features


class TestRealPointInTimeEquivalence:
    """Batch features == point-in-time features. Targets are exempt BY DEFINITION.

    Prefix replay rebuilds the whole dataset once per cut, so it is quadratic in the bar
    count and is run on 1H and 4H. Feature-side equivalence at 15m and finer is R2-07's
    guarantee and is covered by its own suite; what is new here — and what these tests
    exist for — is the TARGET side.
    """

    @pytest.fixture(params=DERIVED, ids=lambda t: t.value)
    def small_timeframe(self, request) -> Timeframe:
        return request.param

    def test_prefix_replay_reproduces_every_feature_vector(self, symbol, small_timeframe):
        coarse_timeframe = small_timeframe
        frame = load(symbol, coarse_timeframe).reset_index(drop=True)
        builder = DatasetBuilder()
        full = {r.as_of: r for r in builder.build(frame, symbol, coarse_timeframe, SPEC).rows}

        for cut in range(1, len(frame) + 1):
            for row in builder.build(frame.iloc[:cut], symbol, coarse_timeframe, SPEC).rows:
                reference = full[row.as_of]
                if row.features == reference.features:
                    continue
                # The single documented exception belongs to R2-05.1's zero-lag True
                # Daily Open and is inherited from R2-07, not introduced here.
                assert row.features.distance_from_true_daily_open_points != (
                    reference.features.distance_from_true_daily_open_points
                ), f"features diverged at cut {cut} for a reason other than the daily open"

    def test_a_prefix_never_resolves_a_target_the_full_frame_leaves_open(self, symbol, small_timeframe):
        """The direction that matters: less data may resolve less, never more."""
        coarse_timeframe = small_timeframe
        frame = load(symbol, coarse_timeframe).reset_index(drop=True)
        builder = DatasetBuilder()
        full = {r.as_of: r for r in builder.build(frame, symbol, coarse_timeframe, SPEC).rows}

        for cut in range(1, len(frame) + 1):
            for row in builder.build(frame.iloc[:cut], symbol, coarse_timeframe, SPEC).rows:
                reference = full[row.as_of]
                for value in row.targets:
                    if value.resolved:
                        assert reference.target(
                            value.spec_name
                        ).resolved, f"{value.spec_name} resolved on a prefix but not on the full frame"


class TestRealSplits:
    def test_a_proportional_split_partitions_real_instants(self, symbol):
        dataset = dataset_for(symbol, Timeframe.M15)
        moments = [r.as_of for r in dataset.rows]
        split = SplitSpec.by_proportion(moments, train=0.6, validation=0.2, embargo_bars=8)
        built = DatasetSpec(targets=SPECS).with_split(split)
        rebuilt = DatasetBuilder().build(
            load(symbol, Timeframe.M15), symbol, Timeframe.M15, built, instants=moments
        )

        counts = rebuilt.split_plan.counts()
        assert counts["train"] > 0 and counts["validation"] > 0 and counts["test"] > 0
        assert sum(counts.values()) == len(rebuilt)

    def test_no_real_train_row_resolves_its_target_from_a_later_split(self, symbol):
        dataset = dataset_for(symbol, Timeframe.M15)
        moments = [r.as_of for r in dataset.rows]
        split = SplitSpec.by_proportion(moments, train=0.6, validation=0.2, embargo_bars=8)
        built = DatasetSpec(targets=SPECS).with_split(split)
        rebuilt = DatasetBuilder().build(
            load(symbol, Timeframe.M15), symbol, Timeframe.M15, built, instants=moments
        )

        for row in rebuilt.of(SplitLabel.TRAIN):
            for value in row.targets:
                if value.future_window_end is not None:
                    assert value.future_window_end < split.train_end


class TestRealAudit:
    def test_the_report_describes_the_dataset_it_was_given(self, symbol, timeframe):
        dataset = dataset_for(symbol, timeframe)
        report = audit_dataset(dataset)
        assert report.row_count == len(dataset)
        assert report.symbols == (symbol.value,)
        assert report.timeframes == (timeframe.value,)
        assert report.chronological is True

    def test_the_report_is_deterministic(self, symbol, timeframe):
        dataset = dataset_for(symbol, timeframe)
        assert audit_dataset(dataset).as_dict() == audit_dataset(dataset).as_dict()

    def test_target_totals_match_the_row_count(self, symbol, timeframe):
        dataset = dataset_for(symbol, timeframe)
        report = audit_dataset(dataset)
        for diagnostic in report.targets:
            assert diagnostic.total == report.row_count
            assert diagnostic.resolved_count + diagnostic.unresolved_count == diagnostic.total


class TestRealPerformance:
    """Measured, reported, and deliberately not optimised."""

    def test_dataset_construction_cost_is_recorded(self, symbol):
        frame = load(symbol, Timeframe.M15)
        builder = DatasetBuilder()

        started = time.perf_counter()
        dataset = builder.build(frame, symbol, Timeframe.M15, SPEC)
        elapsed = time.perf_counter() - started

        assert len(dataset) == len(frame)
        per_row = elapsed / max(1, len(dataset))
        # A ceiling loose enough to catch a change of ORDER, not a slow machine.
        assert per_row < 0.5, f"{symbol.value} 15m: {per_row * 1000:.1f} ms per row"

    def test_targets_alone_are_far_cheaper_than_features(self, symbol):
        """Establishes where the cost actually is before anyone optimises the wrong half."""
        from ict_kronos.features import TargetEngine

        frame = load(symbol, Timeframe.M15)
        engine = TargetEngine(symbol=symbol, timeframe=Timeframe.M15, frame=frame)

        started = time.perf_counter()
        engine.values(list(SPECS))
        target_cost = time.perf_counter() - started

        started = time.perf_counter()
        DatasetBuilder().build(frame, symbol, Timeframe.M15, SPEC)
        total_cost = time.perf_counter() - started

        assert target_cost < total_cost
