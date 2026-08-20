"""R2-08 dataset rows, the temporal contract, and the quality audit.

The whole story reduces to one asymmetry, and this file is where it is proven:

.. code-block:: text

    row.features   must be IDENTICAL when the future is deleted, mutated or spiked
    row.targets    are allowed to change, and must

Both halves matter. If the features moved, R2-07's guarantee is broken. If the targets
did *not* move under a violent future mutation, the leakage tests are vacuous — they
would pass just as happily against a layer that computed nothing at all.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame
from ict_kronos.features import (
    DATASET_SCHEMA_VERSION,
    Dataset,
    DatasetBuilder,
    DatasetRow,
    DatasetSpec,
    SplitError,
    SplitLabel,
    SplitSpec,
    TargetSpec,
    TargetType,
    TradeSide,
    audit_dataset,
    audit_rows,
    rows_to_frame,
)
from ict_kronos.ict import FEATURE_NAMES, FEATURE_VERSION

START = datetime(2024, 3, 8, 9, 0, tzinfo=UTC)
M5 = Timeframe.M5
SYM = Symbol.EURUSD

#: Long enough for swings, a structural break, a dealing range and some composites.
TREND = [
    1.0000, 1.0020, 1.0040, 1.0060, 1.0030, 1.0010, 1.0025, 1.0050,
    1.0080, 1.0100, 1.0070, 1.0120, 1.0090, 1.0140, 1.0110, 1.0160,
    1.0130, 1.0180, 1.0150, 1.0200, 1.0170, 1.0090, 1.0050, 1.0020,
]  # fmt: skip

SPECS = (
    TargetSpec(name="ret_4", target_type=TargetType.FUTURE_RETURN, horizon_bars=4),
    TargetSpec(name="dir_4", target_type=TargetType.DIRECTION, horizon_bars=4, threshold_points=20.0),
    TargetSpec(name="exc_4", target_type=TargetType.EXCURSION, horizon_bars=4),
    TargetSpec(
        name="tpsl_8",
        target_type=TargetType.TP_BEFORE_SL,
        horizon_bars=8,
        side=TradeSide.LONG,
        take_profit_points=30.0,
        stop_loss_points=30.0,
    ),
)


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


def spec(split: SplitSpec | None = None) -> DatasetSpec:
    base = DatasetSpec(targets=SPECS)
    return base if split is None else base.with_split(split)


def build(frame=None, dataset_spec: DatasetSpec | None = None) -> Dataset:
    return DatasetBuilder().build(
        frame if frame is not None else bars(TREND), SYM, M5, dataset_spec or spec()
    )


class TestRowConstruction:
    def test_one_row_per_bar_close(self):
        frame = bars(TREND)
        assert len(build(frame)) == len(frame)

    def test_rows_are_chronological_and_unique(self):
        rows = build().rows
        moments = [r.as_of for r in rows]
        assert moments == sorted(moments)
        assert len(set(moments)) == len(moments)

    def test_a_row_carries_all_three_schema_versions(self):
        row = build().rows[-1]
        assert row.dataset_schema_version == DATASET_SCHEMA_VERSION
        assert row.feature_schema_version == FEATURE_VERSION
        assert row.target_schema_version == SPECS[0].version

    def test_a_row_carries_one_value_per_specification_in_order(self):
        row = build().rows[0]
        assert [t.spec_name for t in row.targets] == [s.name for s in SPECS]

    def test_a_row_is_immutable(self):
        from dataclasses import FrozenInstanceError

        with pytest.raises(FrozenInstanceError):
            build().rows[0].symbol = "XAUUSD"

    def test_features_come_from_r2_07_unchanged(self):
        """The builder joins; it does not recompute. Same instant, same vector."""
        from ict_kronos.ict import ICTFeatureVector, MarketStateBuilder

        frame = bars(TREND)
        dataset = build(frame)
        engine = MarketStateBuilder().analyse(frame, SYM, M5)
        expected = {s.as_of: ICTFeatureVector.from_state(s) for s in engine.states()}
        for row in dataset.rows:
            assert row.features == expected[row.as_of]

    def test_feature_provenance_is_r2_07s_source_ids_verbatim(self):
        from ict_kronos.ict import MarketStateBuilder

        frame = bars(TREND)
        dataset = build(frame)
        engine = MarketStateBuilder().analyse(frame, SYM, M5)
        expected = {s.as_of: s.source_ids() for s in engine.states()}
        for row in dataset.rows:
            assert row.feature_provenance == expected[row.as_of]

    def test_an_instant_that_is_not_a_bar_close_produces_no_row(self):
        frame = bars(TREND)
        engine_instants = [START + timedelta(minutes=5 * i) + timedelta(seconds=1) for i in range(3)]
        dataset = DatasetBuilder().build(frame, SYM, M5, spec(), instants=engine_instants)
        assert len(dataset) == 0

    def test_target_lookup_by_name(self):
        row = build().rows[0]
        assert row.target("dir_4") is not None
        assert row.target("nonexistent") is None


class TestTheTemporalContract:
    """Truncate, mutate, spike, control. The four checks the brief names in §8."""

    CUT = 12

    def frames(self):
        base = bars(TREND)
        return base, base.iloc[: self.CUT + 1].copy()

    def features_at(self, frame, moment):
        rows = DatasetBuilder().build(frame, SYM, M5, spec()).rows
        found = next((r for r in rows if r.as_of == moment), None)
        return None if found is None else found.features

    def moment(self):
        return build().rows[self.CUT].as_of

    def test_a_truncated_frame_produces_identical_features(self):
        full, cut = self.frames()
        moment = self.moment()
        assert self.features_at(cut, moment) == self.features_at(full, moment)

    def test_a_truncated_frame_may_lose_a_target_and_that_is_expected(self):
        full, cut = self.frames()
        moment = self.moment()
        full_row = next(r for r in build(full).rows if r.as_of == moment)
        cut_row = next(r for r in build(cut).rows if r.as_of == moment)
        assert full_row.target("ret_4").resolved is True
        assert cut_row.target("ret_4").resolved is False

    def mutated(self, columns, factor_up, factor_down):
        frame = bars(TREND).copy()
        index = frame.index[self.CUT + 1 :]
        for column in columns:
            frame.loc[index, column] = frame.loc[index, column] * (
                factor_up if column in ("high", "close", "open") else factor_down
            )
        return frame

    def test_violently_mutating_every_future_bar_leaves_features_untouched(self):
        moment = self.moment()
        frame = self.mutated(("open", "high", "low", "close"), 1.5, 0.5)
        assert self.features_at(frame, moment) == self.features_at(bars(TREND), moment)

    def test_violently_mutating_every_future_bar_DOES_move_the_targets(self):
        """Non-vacuity: if the targets were inert too, the test above proves nothing."""
        moment = self.moment()
        frame = self.mutated(("open", "high", "low", "close"), 1.5, 0.5)
        before = next(r for r in build().rows if r.as_of == moment)
        after = next(r for r in build(frame).rows if r.as_of == moment)
        assert after.target("ret_4").future_return != before.target("ret_4").future_return

    def test_a_wick_only_future_mutation_leaves_features_untouched(self):
        moment = self.moment()
        frame = self.mutated(("high", "low"), 1.2, 0.8)
        assert self.features_at(frame, moment) == self.features_at(bars(TREND), moment)

    def test_a_wick_only_future_mutation_moves_the_excursion_target(self):
        """Excursion reads highs and lows, so it must respond where a close-based one does not."""
        moment = self.moment()
        frame = self.mutated(("high", "low"), 1.2, 0.8)
        before = next(r for r in build().rows if r.as_of == moment)
        after = next(r for r in build(frame).rows if r.as_of == moment)
        assert after.target("exc_4").up_excursion_points != before.target("exc_4").up_excursion_points
        assert after.target("ret_4").future_return == before.target("ret_4").future_return

    def test_CONTROL_mutating_history_does_change_the_features(self):
        """Without this, every inertness assertion above could pass vacuously."""
        moment = self.moment()
        frame = bars(TREND).copy()
        index = frame.index[: self.CUT - 2]
        frame.loc[index, "high"] = frame.loc[index, "high"] * 1.3
        frame.loc[index, "low"] = frame.loc[index, "low"] * 0.7
        assert self.features_at(frame, moment) != self.features_at(bars(TREND), moment)

    def test_no_feature_reads_a_bar_after_its_own_as_of(self):
        """Swept across every instant, not only a convenient one."""
        full = bars(TREND)
        for position in range(4, len(full)):
            moment = full["timestamp"].iloc[position].to_pydatetime() + M5.duration
            truncated = full.iloc[: position + 1].copy()
            assert self.features_at(truncated, moment) == self.features_at(full, moment)


class TestSerialization:
    def test_a_row_round_trips_exactly(self):
        for row in build().rows:
            assert DatasetRow.from_dict(row.as_dict()) == row

    def test_a_row_with_unresolved_targets_round_trips(self):
        rows = build().rows
        unresolved = [r for r in rows if any(not t.resolved for t in r.targets)]
        assert unresolved, "the tail of the frame must produce unresolved targets"
        for row in unresolved:
            assert DatasetRow.from_dict(row.as_dict()) == row

    def test_a_row_with_a_split_label_round_trips(self):
        dataset = build(dataset_spec=self.split_spec())
        labelled = [r for r in dataset.rows if r.split is not None]
        assert labelled
        for row in labelled:
            assert DatasetRow.from_dict(row.as_dict()) == row

    @staticmethod
    def split_spec():
        return spec(
            SplitSpec(
                train_end=START + timedelta(minutes=5 * 12),
                validation_end=START + timedelta(minutes=5 * 18),
                embargo_bars=8,
            )
        )

    def test_a_dataset_spec_round_trips(self):
        original = self.split_spec()
        assert DatasetSpec.from_dict(original.as_dict()) == original

    def test_serialisation_is_deterministic_across_runs(self):
        assert build().rows[5].as_dict() == build().rows[5].as_dict()

    def test_a_real_zero_and_a_missing_value_stay_distinguishable(self):
        """The distinction R2-07's §7 exists to protect, checked through the row.

        ``bullish_fvg_count == 0`` means "no live bullish gaps"; ``nearest_bullish_fvg_points
        is None`` means "there is no gap to measure to". Emitting 0 for both would tell a
        model price is sitting ON a level that does not exist.
        """
        payload = build().rows[0].as_dict()["features"]
        assert payload["bullish_fvg_count"] == 0
        assert payload["nearest_bullish_fvg_points"] is None
        assert [k for k, v in payload.items() if v is None], "an early bar must have absent features"

    def test_the_numeric_row_uses_nan_where_the_dict_uses_none(self):
        row = build().rows[0]
        payload, numeric = row.features.as_dict(), row.features.as_row()
        for name, value in zip(FEATURE_NAMES, numeric, strict=True):
            if payload[name] is None:
                assert math.isnan(value)


class TestTheFlatFrame:
    def test_column_order_is_identity_then_features_then_targets(self):
        frame = rows_to_frame(build().rows, target_name="dir_4")
        columns = list(frame.columns)
        first = columns.index("close")
        assert columns[:4] == ["symbol", "timeframe", "as_of", "split"]
        assert columns[first : first + len(FEATURE_NAMES)] == list(FEATURE_NAMES)
        assert columns[-1] == "target_outcome"

    def test_an_empty_list_still_declares_the_schema(self):
        frame = rows_to_frame([])
        assert len(frame) == 0
        assert set(FEATURE_NAMES) <= set(frame.columns)

    def test_the_named_target_is_the_one_projected(self):
        frame = rows_to_frame(build().rows, target_name="exc_4")
        assert set(frame["target_name"].dropna()) == {"exc_4"}

    def test_unresolved_targets_appear_as_missing_not_as_zero(self):
        frame = rows_to_frame(build().rows, target_name="ret_4")
        unresolved = frame[~frame["target_resolved"].astype(bool)]
        assert len(unresolved) > 0
        assert unresolved["target_future_return"].isna().all()


class TestSplitIntegration:
    def split(self, embargo=8):
        return SplitSpec(
            train_end=START + timedelta(minutes=5 * 12),
            validation_end=START + timedelta(minutes=5 * 18),
            embargo_bars=embargo,
        )

    def test_an_embargo_shorter_than_the_longest_horizon_is_refused(self):
        """The one way contamination could enter by configuration."""
        with pytest.raises(SplitError, match="shorter than the longest target horizon"):
            DatasetSpec(targets=SPECS, split=self.split(embargo=2))

    def test_with_split_widens_the_embargo_to_cover_every_horizon(self):
        built = DatasetSpec(targets=SPECS).with_split(self.split(embargo=0))
        assert built.split.embargo_bars == built.max_horizon_bars == 8

    def test_every_row_is_labelled_when_a_split_is_given(self):
        dataset = build(dataset_spec=spec(self.split()))
        assert all(r.split is not None for r in dataset.rows)

    def test_no_row_is_labelled_when_no_split_is_given(self):
        assert all(r.split is None for r in build().rows)

    def test_no_train_row_resolves_its_target_from_a_later_split(self):
        """The contamination check, asserted on real rows rather than on timestamps."""
        split = self.split()
        dataset = build(dataset_spec=spec(split))
        for row in dataset.of(SplitLabel.TRAIN):
            for value in row.targets:
                if value.future_window_end is not None:
                    assert value.future_window_end < split.train_end

    def test_no_validation_row_resolves_its_target_from_the_test_period(self):
        split = self.split()
        dataset = build(dataset_spec=spec(split))
        for row in dataset.of(SplitLabel.VALIDATION):
            for value in row.targets:
                if value.future_window_end is not None:
                    assert value.future_window_end < split.validation_end

    def test_embargoed_rows_are_kept_and_visible_rather_than_dropped(self):
        dataset = build(dataset_spec=spec(self.split()))
        withheld = dataset.of(SplitLabel.EMBARGOED)
        assert withheld, "the fixture must exercise the embargo"
        assert all(r.as_of in {a.as_of for a in dataset.split_plan.assignments} for r in withheld)


class TestTheQualityAudit:
    def test_it_reports_the_schema_it_measured(self):
        report = audit_dataset(build())
        assert report.feature_count == len(FEATURE_NAMES)
        assert report.feature_names == FEATURE_NAMES
        assert report.feature_schema_versions == (FEATURE_VERSION,)

    def test_it_reports_coverage_and_symbol_scope(self):
        report = audit_dataset(build())
        assert report.symbols == ("EURUSD",)
        assert report.timeframes == ("5m",)
        assert report.row_count == len(TREND)
        assert report.chronological is True
        assert report.duplicate_as_of_count == 0

    def test_it_separates_missing_from_constant(self):
        """A column that is always absent is not the same finding as one that never varies."""
        report = audit_dataset(build())
        for diagnostic in report.features:
            if diagnostic.present_count == 0:
                assert diagnostic.constant is False

    def test_it_counts_unresolved_targets_by_reason(self):
        report = audit_dataset(build())
        by_name = {t.name: t for t in report.targets}
        assert by_name["ret_4"].unresolved_count == 4
        assert set(by_name["ret_4"].unresolved_by_reason) == {"insufficient_history"}

    def test_it_reports_class_counts_for_categorical_targets_only(self):
        report = audit_dataset(build())
        by_name = {t.name: t for t in report.targets}
        assert by_name["dir_4"].class_counts
        assert by_name["ret_4"].class_counts == {}

    def test_coverage_is_resolved_over_total(self):
        report = audit_dataset(build())
        for diagnostic in report.targets:
            assert diagnostic.coverage == pytest.approx(diagnostic.resolved_count / diagnostic.total)

    def test_it_reports_split_counts_including_embargoed(self):
        dataset = build(
            dataset_spec=spec(
                SplitSpec(
                    train_end=START + timedelta(minutes=60),
                    validation_end=START + timedelta(minutes=90),
                    embargo_bars=8,
                )
            )
        )
        counts = audit_dataset(dataset).split_counts
        assert counts["embargoed"] > 0
        assert sum(counts.values()) == len(dataset)

    def test_the_report_is_byte_identical_across_runs(self):
        assert audit_dataset(build()).as_dict() == audit_dataset(build()).as_dict()

    def test_an_empty_dataset_audits_without_inventing_anything(self):
        report = audit_rows([])
        assert report.row_count == 0
        assert report.first_as_of is None and report.last_as_of is None
        assert report.targets == ()

    def test_the_audit_selects_nothing(self):
        """It describes. Feature selection is a modelling decision and belongs to Phase 4."""
        from tests.test_market_state import _code_of

        code = _code_of("ict_kronos/features/audit.py")
        for banned in ("importance", "correlation", "select", "drop(", "fillna", "impute"):
            assert banned not in code, f"audit.py does more than describe: {banned!r}"
        assert "def audit_rows(" in code


class TestNoModelTraining:
    """R2-08 produces a dataset. Nothing in it may reach a model."""

    def test_no_module_imports_a_learner(self):
        from pathlib import Path

        from tests.test_market_state import _code_of

        banned = (
            "xgboost",
            "lightgbm",
            "sklearn",
            "torch",
            "optuna",
            "kronos",
            "train_test_split",
            "fit(",
            "predict(",
        )
        for path in sorted(Path("ict_kronos/features").glob("*.py")):
            code = _code_of(str(path))
            for token in banned:
                assert token not in code, f"{path.name} reaches for a model: {token!r}"
