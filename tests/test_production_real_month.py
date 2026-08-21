"""Independent one-month revalidation on the PRODUCTION universe — 2026-07.

A fresh, contiguous calendar month of real Dukascopy ticks, downloaded through the
repository's own `RealDataPipeline`, resampled from the same 1M base, and run through the
**entire** R2-01 → R2-08 stack from raw bars. Nothing is reused from the 2024-03 fixture:
different year, different partition file, different bars.

The question this module answers is not "do the numbers match the old fixture" — they
will not, and expecting them to would be expecting the market to repeat. It is:

    does every INVARIANT still hold on data the engine has never seen?

causality · provenance · identity · serialisation · streaming · split integrity

Production universe only: EURUSD and XAUUSD × {1H, 4H, 1D}. The lower timeframes are
covered by their own suites and are refused here by `assert_production_pair`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

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
from ict_kronos.features.production import (
    PRODUCTION_SYMBOLS,
    PRODUCTION_TIMEFRAMES,
    assert_production_pair,
    build_production_dataset,
)
from ict_kronos.ict import ICTFeatureVector, TrueDailyOpenDetector
from ict_kronos.storage import ParquetCandleStore

DATA_ROOT = "data/normalized"
#: The most recent COMPLETE calendar month available from the feed at download time.
MONTH_START = datetime(2026, 7, 1, tzinfo=UTC)
MONTH_END = datetime(2026, 8, 1, tzinfo=UTC)

#: Deliberately modest, because a month holds ~22 Daily bars. A 16-bar Daily horizon
#: would leave most rows unresolved for a reason that has nothing to do with the engine.
HORIZONS = {Timeframe.H1: 4, Timeframe.H4: 4, Timeframe.D1: 2}


def load(symbol: Symbol, timeframe: Timeframe) -> pd.DataFrame:
    """The fresh month, straight from the immutable store. Never resampled here."""
    frame = ParquetCandleStore(DATA_ROOT).read(
        symbol, timeframe, start=pd.Timestamp(MONTH_START), end=pd.Timestamp(MONTH_END)
    )
    if len(frame) < 8:
        pytest.skip(f"fresh {MONTH_START:%Y-%m} data absent for {symbol.value}/{timeframe.value}")
    return frame


@pytest.fixture(params=PRODUCTION_SYMBOLS, ids=lambda s: s.value)
def symbol(request) -> Symbol:
    return request.param


@pytest.fixture(params=PRODUCTION_TIMEFRAMES, ids=lambda t: t.value)
def timeframe(request) -> Timeframe:
    return request.param


def specs_for(timeframe: Timeframe, symbol: Symbol) -> tuple[TargetSpec, ...]:
    """Target specs sized to the instrument and timeframe — see `production_universe.md` §4."""
    from ict_kronos.features.production import parameters_for

    params = parameters_for(symbol, timeframe)
    horizon = HORIZONS[timeframe]
    return (
        TargetSpec(name="ret", target_type=TargetType.FUTURE_RETURN, horizon_bars=horizon),
        TargetSpec(
            name="dir",
            target_type=TargetType.DIRECTION,
            horizon_bars=horizon,
            threshold_points=params.threshold_points,
        ),
        TargetSpec(name="exc", target_type=TargetType.EXCURSION, horizon_bars=horizon),
        TargetSpec(
            name="tpsl",
            target_type=TargetType.TP_BEFORE_SL,
            horizon_bars=horizon,
            side=TradeSide.LONG,
            take_profit_points=params.take_profit_points,
            stop_loss_points=params.stop_loss_points,
        ),
    )


#: One dataset AND its engine per pair, for the whole module. Every approved detector
#: runs once over the frame; re-doing that inside each test would make this file a
#: timeout rather than a suite. Analyses are pure functions of the frame.
_BUILT: dict[tuple[Symbol, Timeframe], tuple] = {}


def built(symbol: Symbol, timeframe: Timeframe):
    key = (symbol, timeframe)
    if key not in _BUILT:
        assert_production_pair(symbol, timeframe)
        frame = load(symbol, timeframe)
        spec = DatasetSpec(targets=specs_for(timeframe, symbol))
        builder = DatasetBuilder()
        engine = builder.state_builder.analyse(frame, symbol, timeframe)
        dataset = build_production_dataset(frame, symbol, timeframe, spec, builder=builder)
        _BUILT[key] = (dataset, engine, frame, spec)
    return _BUILT[key]


class TestFreshRawData:
    """The raw layer, before anything interprets it."""

    def test_the_month_is_present_and_independent_of_the_old_fixture(self, symbol, timeframe):
        frame = load(symbol, timeframe)
        assert frame["timestamp"].min() >= pd.Timestamp(MONTH_START)
        assert frame["timestamp"].max() < pd.Timestamp(MONTH_END)

    def test_timestamps_are_strictly_increasing(self, symbol, timeframe):
        stamps = load(symbol, timeframe)["timestamp"]
        assert stamps.is_monotonic_increasing
        assert (stamps.diff().dropna() > pd.Timedelta(0)).all()

    def test_there_are_no_duplicate_bars(self, symbol, timeframe):
        stamps = load(symbol, timeframe)["timestamp"]
        assert stamps.duplicated().sum() == 0

    def test_every_bar_is_timezone_aware_utc(self, symbol, timeframe):
        stamps = load(symbol, timeframe)["timestamp"]
        assert stamps.dt.tz is not None
        assert str(stamps.dt.tz) in {"UTC", "utc"}

    def test_no_impossible_high_low_relationships(self, symbol, timeframe):
        frame = load(symbol, timeframe)
        assert (frame["high"] >= frame["low"]).all()
        assert (frame["high"] >= frame[["open", "close"]].max(axis=1)).all()
        assert (frame["low"] <= frame[["open", "close"]].min(axis=1)).all()

    def test_no_malformed_or_non_finite_prices(self, symbol, timeframe):
        frame = load(symbol, timeframe)
        for column in ("open", "high", "low", "close"):
            assert frame[column].notna().all()
            assert (frame[column] > 0).all()

    def test_bars_sit_on_the_timeframe_grid(self, symbol, timeframe):
        """A bar off the grid means a resample origin drifted — silent and corrosive."""
        stamps = load(symbol, timeframe)["timestamp"]
        seconds = timeframe.minutes * 60
        offsets = (stamps.view("int64") // 1_000_000_000) % seconds
        assert (offsets == 0).all()

    def test_the_weekend_is_absent_rather_than_filled(self, symbol):
        """A closed market is a gap in the data, never a synthesised flat bar."""
        frame = load(symbol, Timeframe.H1)
        weekdays = frame["timestamp"].dt.dayofweek
        saturday = frame[weekdays == 5]
        # Saturday is fully closed; a handful of Sunday-evening bars is the normal
        # FX week open and is not treated as an anomaly.
        assert len(saturday) == 0, "Saturday bars would mean fabricated data"


class TestDailyIsFirstClass:
    """Daily is a production timeframe, so it gets its own scrutiny."""

    def test_daily_bars_exist_and_are_complete_days(self, symbol):
        frame = load(symbol, Timeframe.D1)
        assert len(frame) >= 15, "a calendar month must yield a workable number of trading days"
        assert (frame["timestamp"].dt.hour == 0).all(), "this repository's D1 opens at 00:00 UTC"
        assert (frame["timestamp"].dt.minute == 0).all()

    def test_daily_bars_skip_the_weekend_rather_than_inventing_it(self, symbol):
        weekdays = load(symbol, Timeframe.D1)["timestamp"].dt.dayofweek
        assert not (weekdays == 5).any(), "no Saturday daily bar may be fabricated"

    def test_daily_ordering_survives_the_month(self, symbol):
        stamps = load(symbol, Timeframe.D1)["timestamp"]
        assert stamps.is_monotonic_increasing

    def test_daily_states_and_features_are_produced(self, symbol):
        dataset, _, frame, _ = built(symbol, Timeframe.D1)
        assert len(dataset) == len(frame)
        assert all(isinstance(row.features, ICTFeatureVector) for row in dataset.rows)

    def test_the_true_daily_open_is_ABSENT_on_daily_and_that_is_documented(self, symbol):
        """The discrepancy named in `docs/features/production_universe.md` §2.

        A D1 bar opens at 00:00 UTC; the True Daily Open is 00:00 America/New_York
        (04:00 or 05:00 UTC). They never coincide, so the level is absent — reported,
        never fabricated by snapping a boundary.
        """
        frame = load(symbol, Timeframe.D1)
        assert TrueDailyOpenDetector().detect(frame, symbol, Timeframe.D1) == []

        dataset, _, _, _ = built(symbol, Timeframe.D1)
        for row in dataset.rows:
            assert row.features.distance_from_true_daily_open_points is None
            assert row.features.trading_day_age_minutes is None

    def test_the_true_daily_open_IS_present_on_hourly_real_bars(self, symbol):
        """The feature is not lost to production — it lives on 1H, where it aligns."""
        frame = load(symbol, Timeframe.H1)
        levels = TrueDailyOpenDetector().detect(frame, symbol, Timeframe.H1)
        assert levels, "a month of 1H bars must contain New York midnight boundaries"
        assert all(level.event_timestamp.hour in (4, 5) for level in levels)


class TestFreshDetectorStack:
    """Every approved detector, re-run from the fresh month's raw bars."""

    def test_the_full_stack_runs_on_every_production_pair(self, symbol, timeframe):
        _, engine, frame, _ = built(symbol, timeframe)
        states = engine.states()
        assert len(states) == len(frame)

    def test_counts_are_non_negative_and_match_their_id_tuples(self, symbol, timeframe):
        _, engine, _, _ = built(symbol, timeframe)
        for state in engine.states():
            assert len(state.imbalance.active_bullish_fvg_ids) == state.imbalance.bullish_fvg_count
            assert len(state.composites.active_unicorn_ids) == state.composites.unicorn_count
            assert state.liquidity.buy_side_count >= 0

    def test_no_identity_collisions_anywhere_in_the_month(self, symbol, timeframe):
        """Fresh data, more events, more chances for two distinct events to share an id."""
        _, engine, _, _ = built(symbol, timeframe)
        families = (
            ("fvg", engine.fvg.zones, "zone_id"),
            ("ifvg", engine.ifvg.zones, "ifvg_id"),
            ("order_block", engine.order_blocks.blocks, "order_block_id"),
            ("breaker", engine.breakers.breakers, "breaker_id"),
            ("bpr", engine.bpr.ranges, "bpr_id"),
            ("rdrb", engine.rdrb.zones, "rdrb_id"),
            ("cisd", engine.cisd.transitions, "cisd_id"),
            ("unicorn", engine.unicorn.unicorns, "unicorn_id"),
            ("dealing_range", engine.dealing_range.ranges, "range_id"),
        )
        for name, items, field in families:
            ids = [getattr(x, field) for x in items]
            assert len(ids) == len(set(ids)), f"{name} produced duplicate ids on fresh data"

    def test_every_event_confirms_no_earlier_than_it_occurred(self, symbol, timeframe):
        _, engine, _, _ = built(symbol, timeframe)
        for zone in engine.fvg.zones:
            assert zone.confirmation_timestamp >= zone.formation_timestamp
        for brk in engine.structure.breaks:
            assert brk.confirmation_timestamp >= brk.event_timestamp


class TestFreshProvenance:
    def test_every_emitted_id_resolves_and_was_observable_first(self, symbol, timeframe):
        """The two questions that matter: does it exist, and could we have known it."""
        dataset, engine, _, _ = built(symbol, timeframe)
        registry = _confirmations(engine)

        checked = 0
        for row in dataset.rows:
            for group, ids in row.feature_provenance.items():
                for source_id in ids:
                    confirmation = registry[group].get(source_id)
                    assert confirmation is not None, f"{group}:{source_id} resolves to nothing"
                    assert confirmation <= row.as_of, f"{group}:{source_id} confirms after as_of"
                    checked += 1
        assert checked > 0, "the month must emit provenance ids"

    def test_unicorn_provenance_is_inherited_whole(self, symbol, timeframe):
        """Unicorn → FVG → Breaker → Order Block, by id, with no geometry copied."""
        dataset, engine, _, _ = built(symbol, timeframe)
        unicorns = {u.unicorn_id: u for u in engine.unicorn.unicorns}
        gaps = {z.zone_id for z in engine.fvg.zones}
        breakers = {b.breaker_id: b for b in engine.breakers.breakers}
        blocks = {b.order_block_id for b in engine.order_blocks.blocks}

        # Provenance lives on the STATE: the flat feature vector deliberately carries
        # numbers only, and the row carries the state's ids beside it.
        assert dataset.rows, "the month must produce rows"
        seen = 0
        for state in engine.states():
            uid = state.composites.latest_unicorn_id
            if uid is None:
                continue
            assert uid in unicorns
            assert state.composites.latest_unicorn_fvg_id in gaps
            assert state.composites.latest_unicorn_breaker_id in breakers
            parent = breakers[state.composites.latest_unicorn_breaker_id]
            assert parent.source_order_block_id in blocks
            seen += 1
        if seen == 0:
            pytest.skip(f"no unicorn confirmed on {symbol.value}/{timeframe.value} this month")

    def test_dealing_range_provenance_resolves_through_the_state(self, symbol, timeframe):
        from ict_kronos.ict import structure_break_id

        _, engine, frame, _ = built(symbol, timeframe)
        breaks = {structure_break_id(b) for b in engine.structure.breaks}
        seen = 0
        for state in engine.states():
            source = state.premium_discount.source_break_id
            if source is None:
                continue
            assert source in breaks
            assert source in state.source_ids()["structure"]
            seen += 1
        if seen == 0:
            pytest.skip(f"no dealing range confirmed on {symbol.value}/{timeframe.value}")


def _confirmations(engine) -> dict[str, dict[str, datetime]]:
    """``group -> {id: confirmation}`` for every detector the state cites."""
    from ict_kronos.ict import structure_break_id, swing_point_id

    out: dict[str, dict[str, datetime]] = {
        "structure": {structure_break_id(b): b.confirmation_timestamp for b in engine.structure.breaks},
        "liquidity_level": {x.level_id: x.confirmation_timestamp for x in engine.liquidity.levels},
        "fvg": {z.zone_id: z.confirmation_timestamp for z in engine.fvg.zones},
        "ifvg": {z.ifvg_id: z.confirmation_timestamp for z in engine.ifvg.zones},
        "bpr": {r.bpr_id: r.confirmation_timestamp for r in engine.bpr.ranges},
        "order_block": {b.order_block_id: b.confirmation_timestamp for b in engine.order_blocks.blocks},
        "breaker": {b.breaker_id: b.confirmation_timestamp for b in engine.breakers.breakers},
        "rdrb": {z.rdrb_id: z.confirmation_timestamp for z in engine.rdrb.zones},
        "cisd": {c.cisd_id: c.confirmation_timestamp for c in engine.cisd.transitions},
        "unicorn": {u.unicorn_id: u.confirmation_timestamp for u in engine.unicorn.unicorns},
        "daily_open": {level.level_id: level.confirmation_timestamp for level in engine.daily_open_levels},
        "dealing_range": {r.range_id: r.confirmation_timestamp for r in engine.dealing_range.ranges},
        "swing": (
            {swing_point_id(s): s.confirmation_timestamp for s in engine.dealing_range.swings_used}
            if hasattr(engine.dealing_range, "swings_used")
            else {}
        ),
    }
    return out


class TestFreshLeakage:
    """Truncate / mutate / wick / control — on data the engine has never seen."""

    def cut_of(self, frame) -> int:
        return len(frame) * 2 // 3

    def features_at(self, frame, symbol, timeframe, moment):
        spec = DatasetSpec(targets=specs_for(timeframe, symbol))
        rows = DatasetBuilder().build(frame, symbol, timeframe, spec).rows
        found = next((r for r in rows if r.as_of == moment), None)
        return None if found is None else found.features

    def baseline(self, symbol, timeframe):
        dataset, _, frame, _ = built(symbol, timeframe)
        cut = self.cut_of(frame)
        return frame.reset_index(drop=True), cut, dataset.rows[cut]

    def test_truncating_the_future_leaves_features_identical(self, symbol, timeframe):
        frame, cut, row = self.baseline(symbol, timeframe)
        truncated = frame.iloc[: cut + 1].copy()
        assert self.features_at(truncated, symbol, timeframe, row.as_of) == row.features

    def test_violently_mutating_the_future_leaves_features_identical(self, symbol, timeframe):
        frame, cut, row = self.baseline(symbol, timeframe)
        mutated = frame.copy()
        index = mutated.index[cut + 1 :]
        mutated.loc[index, "high"] = mutated.loc[index, "high"] * 1.5
        mutated.loc[index, "low"] = mutated.loc[index, "low"] * 0.5
        mutated.loc[index, "close"] = mutated.loc[index, "high"]
        assert self.features_at(mutated, symbol, timeframe, row.as_of) == row.features

    def test_a_confirming_bar_wick_mutation_does_not_rewrite_history(self, symbol, timeframe):
        """Wick-only, after `as_of`: close-confirmed events must not retroactively move."""
        frame, cut, row = self.baseline(symbol, timeframe)
        mutated = frame.copy()
        index = mutated.index[cut + 1 :]
        mutated.loc[index, "high"] = mutated.loc[index, "high"] * 1.2
        mutated.loc[index, "low"] = mutated.loc[index, "low"] * 0.8
        assert self.features_at(mutated, symbol, timeframe, row.as_of) == row.features

    def test_CONTROL_mutating_history_DOES_change_the_features(self, symbol, timeframe):
        """Mandatory. Without it an inert pipeline would pass every test above."""
        frame, cut, row = self.baseline(symbol, timeframe)
        mutated = frame.copy()
        index = mutated.index[: max(1, cut - 2)]
        mutated.loc[index, "high"] = mutated.loc[index, "high"] * 1.3
        mutated.loc[index, "low"] = mutated.loc[index, "low"] * 0.7
        assert self.features_at(mutated, symbol, timeframe, row.as_of) != row.features

    def test_appending_future_bars_leaves_history_identical(self, symbol, timeframe):
        frame, cut, row = self.baseline(symbol, timeframe)
        extra = frame.iloc[-1:].copy()
        extra.loc[:, "timestamp"] = frame["timestamp"].iloc[-1] + pd.Timedelta(minutes=timeframe.minutes)
        for column in ("open", "high", "low", "close"):
            extra.loc[:, column] = extra[column] * 1.4
        appended = pd.concat([frame, extra], ignore_index=True)
        assert self.features_at(appended, symbol, timeframe, row.as_of) == row.features


class TestFreshStreaming:
    """Batch == prefix replay, at every cut, on the production timeframes."""

    def test_prefix_replay_reproduces_every_row(self, symbol, timeframe):
        dataset, _, frame, spec = built(symbol, timeframe)
        frame = frame.reset_index(drop=True)
        full = {row.as_of: row for row in dataset.rows}
        builder = DatasetBuilder()

        for cut in range(1, len(frame) + 1):
            for row in builder.build(frame.iloc[:cut], symbol, timeframe, spec).rows:
                reference = full[row.as_of]
                if row.features == reference.features:
                    continue
                # R2-05.1's zero-lag True Daily Open is the ONE inherited exception,
                # and it cannot occur at all on D1, where no level exists.
                assert timeframe is not Timeframe.D1, "D1 has no daily open to diverge on"
                assert row.features.distance_from_true_daily_open_points != (
                    reference.features.distance_from_true_daily_open_points
                ), f"features diverged at cut {cut} for a reason other than the daily open"

    def test_a_prefix_never_resolves_a_target_the_full_frame_leaves_open(self, symbol, timeframe):
        dataset, _, frame, spec = built(symbol, timeframe)
        frame = frame.reset_index(drop=True)
        full = {row.as_of: row for row in dataset.rows}
        builder = DatasetBuilder()

        for cut in range(1, len(frame) + 1):
            for row in builder.build(frame.iloc[:cut], symbol, timeframe, spec).rows:
                reference = full[row.as_of]
                for value in row.targets:
                    if value.resolved:
                        assert reference.target(value.spec_name).resolved


class TestFreshTargetsAndDataset:
    def test_every_row_round_trips(self, symbol, timeframe):
        dataset, _, _, _ = built(symbol, timeframe)
        for row in dataset.rows:
            assert DatasetRow.from_dict(row.as_dict()) == row

    def test_unresolved_targets_carry_a_reason_and_never_a_substitute(self, symbol, timeframe):
        dataset, _, _, _ = built(symbol, timeframe)
        for row in dataset.rows:
            for value in row.targets:
                if value.resolved:
                    continue
                assert value.unresolved_reason is not None
                assert value.future_return is None
                assert value.direction is None
                assert value.up_excursion_points is None

    def test_the_final_bar_never_resolves_a_forward_target(self, symbol, timeframe):
        dataset, _, _, _ = built(symbol, timeframe)
        last = dataset.rows[-1]
        assert last.target("ret").resolved is False
        assert last.target("ret").unresolved_reason is UnresolvedReason.INSUFFICIENT_HISTORY

    def test_the_month_resolves_a_meaningful_share_of_targets(self, symbol, timeframe):
        """Not a tuning gate — a floor that catches a silently dead target engine."""
        dataset, _, _, _ = built(symbol, timeframe)
        report = audit_dataset(dataset)
        by_name = {t.name: t for t in report.targets}
        assert by_name["ret"].coverage > 0.5, "a month should resolve most close-to-close returns"

    def test_target_windows_lie_strictly_after_their_observation(self, symbol, timeframe):
        dataset, _, _, _ = built(symbol, timeframe)
        for row in dataset.rows:
            for value in row.targets:
                if value.future_window_start is not None:
                    assert value.future_window_start > row.as_of

    def test_a_weekend_gap_widens_the_window_rather_than_hiding_it(self, symbol):
        """Horizons are counted in BARS, so a closed market shows as a wider window."""
        dataset, _, _, _ = built(symbol, Timeframe.H1)
        horizon = HORIZONS[Timeframe.H1]
        widened = [
            value
            for row in dataset.rows
            for value in row.targets
            if value.future_window_end is not None
            and value.future_window_end - row.as_of > timedelta(hours=horizon + 2)
        ]
        assert widened, "a full month contains weekends; some horizon must straddle one"


class TestFreshSplits:
    def split_for(self, symbol, timeframe):
        dataset, _, frame, base = built(symbol, timeframe)
        moments = [row.as_of for row in dataset.rows]
        split = SplitSpec.by_proportion(moments, train=0.6, validation=0.2)
        spec = base.with_split(split)
        rebuilt = DatasetBuilder().build(frame, symbol, timeframe, spec)
        return split, rebuilt

    def test_the_partition_is_chronological_and_disjoint(self, symbol, timeframe):
        _, dataset = self.split_for(symbol, timeframe)
        groups = {label: [r.as_of for r in dataset.of(label)] for label in SplitLabel}
        train, validation, test = (
            groups[SplitLabel.TRAIN],
            groups[SplitLabel.VALIDATION],
            groups[SplitLabel.TEST],
        )
        if train and validation:
            assert max(train) < min(validation)
        if validation and test:
            assert max(validation) < min(test)
        seen = [moment for values in groups.values() for moment in values]
        assert len(seen) == len(set(seen)) == len(dataset)

    def test_no_train_row_resolves_its_target_from_a_later_split(self, symbol, timeframe):
        split, dataset = self.split_for(symbol, timeframe)
        for row in dataset.of(SplitLabel.TRAIN):
            for value in row.targets:
                if value.future_window_end is not None:
                    assert value.future_window_end < split.train_end

    def test_no_validation_row_reaches_into_the_test_period(self, symbol, timeframe):
        split, dataset = self.split_for(symbol, timeframe)
        for row in dataset.of(SplitLabel.VALIDATION):
            for value in row.targets:
                if value.future_window_end is not None:
                    assert value.future_window_end < split.validation_end

    def test_embargoed_rows_are_visible_rather_than_dropped(self, symbol, timeframe):
        _, dataset = self.split_for(symbol, timeframe)
        assert len(dataset) == len(dataset.split_plan.assignments)


class TestFreshAudit:
    def test_the_report_describes_the_month_it_was_given(self, symbol, timeframe):
        dataset, _, frame, _ = built(symbol, timeframe)
        report = audit_dataset(dataset)
        assert report.row_count == len(frame)
        assert report.symbols == (symbol.value,)
        assert report.timeframes == (timeframe.value,)
        assert report.chronological is True
        assert report.duplicate_as_of_count == 0

    def test_the_report_is_deterministic(self, symbol, timeframe):
        dataset, _, _, _ = built(symbol, timeframe)
        assert audit_dataset(dataset).as_dict() == audit_dataset(dataset).as_dict()

    def test_features_are_not_wholly_missing_on_fresh_data(self, symbol, timeframe):
        """A month of unseen bars must populate most of the schema, or the engine is inert."""
        dataset, _, _, _ = built(symbol, timeframe)
        report = audit_dataset(dataset)
        populated = [f for f in report.features if f.present_count > 0]
        assert len(populated) >= 30, f"only {len(populated)}/56 features ever have a value"
