"""R2-08 chronological splitting and the target-window embargo.

The thing this file is really testing is a leak that leaves no trace. A row whose
features stop at `as_of` but whose target was resolved by bars in the *next* split
trains on the period it is later scored against — with no exception, no NaN and no
warning, only a validation score that is too good. The embargo exists to make that
impossible, and these tests exist to make sure the embargo is actually applied.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ict_kronos.features import (
    SPLIT_SCHEMA_VERSION,
    SplitError,
    SplitLabel,
    SplitSpec,
    assign_splits,
)

START = datetime(2024, 3, 8, 9, 0, tzinfo=UTC)


def instants(count, minutes=5):
    return [START + timedelta(minutes=minutes * i) for i in range(count)]


class TestSpecValidation:
    def test_boundaries_must_be_chronological(self):
        with pytest.raises(SplitError, match="chronological"):
            SplitSpec(train_end=START + timedelta(hours=2), validation_end=START)

    def test_identical_boundaries_are_refused(self):
        with pytest.raises(SplitError, match="chronological"):
            SplitSpec(train_end=START, validation_end=START)

    def test_naive_boundaries_are_refused(self):
        with pytest.raises(SplitError, match="timezone-aware"):
            SplitSpec(train_end=datetime(2024, 3, 8), validation_end=datetime(2024, 3, 9))

    def test_a_negative_embargo_is_refused(self):
        with pytest.raises(SplitError, match="embargo_bars"):
            SplitSpec(train_end=START, validation_end=START + timedelta(hours=1), embargo_bars=-1)

    def test_a_spec_records_its_version_and_round_trips(self):
        spec = SplitSpec(
            train_end=START + timedelta(hours=1),
            validation_end=START + timedelta(hours=2),
            embargo_bars=4,
        )
        assert spec.version == SPLIT_SCHEMA_VERSION
        assert SplitSpec.from_dict(spec.as_dict()) == spec


class TestBoundarySemantics:
    SPEC = SplitSpec(train_end=START + timedelta(hours=1), validation_end=START + timedelta(hours=2))

    def test_a_boundary_is_an_exclusive_upper_bound(self):
        """Stated once so no caller has to guess which side an exact hit falls on."""
        assert self.SPEC.label_of(self.SPEC.train_end - timedelta(seconds=1)) is SplitLabel.TRAIN
        assert self.SPEC.label_of(self.SPEC.train_end) is SplitLabel.VALIDATION
        assert self.SPEC.label_of(self.SPEC.validation_end) is SplitLabel.TEST

    def test_test_is_open_ended(self):
        assert self.SPEC.end_of(SplitLabel.TEST) is None
        assert self.SPEC.end_of(SplitLabel.TRAIN) == self.SPEC.train_end


class TestProportionalBoundaries:
    def test_proportions_cut_the_observation_SEQUENCE_not_the_clock(self):
        """A weekend must not hand one split more rows than the proportion asked for."""
        dense = instants(20)
        sparse = [dense[-1] + timedelta(days=3) + timedelta(minutes=5 * i) for i in range(20)]
        spec = SplitSpec.by_proportion(dense + sparse, train=0.5, validation=0.25)
        plan = assign_splits(dense + sparse, spec)
        assert plan.counts()["train"] == 20
        assert plan.counts()["validation"] == 10

    def test_an_empty_sequence_is_refused(self):
        with pytest.raises(SplitError, match="empty"):
            SplitSpec.by_proportion([])

    def test_proportions_that_leave_no_test_period_are_refused(self):
        with pytest.raises(SplitError, match="leave room"):
            SplitSpec.by_proportion(instants(10), train=0.8, validation=0.2)

    def test_too_few_instants_to_split_is_refused_rather_than_fudged(self):
        with pytest.raises(SplitError):
            SplitSpec.by_proportion(instants(2), train=0.6, validation=0.2)

    def test_the_three_proportions_can_never_fail_to_sum_to_one(self):
        """Test is the remainder, not a third parameter."""
        spec = SplitSpec.by_proportion(instants(100), train=0.6, validation=0.2)
        plan = assign_splits(instants(100), spec)
        counts = plan.counts()
        assert counts["train"] + counts["validation"] + counts["test"] == 100


class TestPartitionIntegrity:
    SPEC = SplitSpec(train_end=START + timedelta(hours=1), validation_end=START + timedelta(hours=2))

    def test_every_instant_is_assigned_exactly_once(self):
        moments = instants(40)
        plan = assign_splits(moments, self.SPEC)
        assert len(plan.assignments) == len(moments)
        assert len({a.as_of for a in plan.assignments}) == len(moments)

    def test_no_instant_appears_in_two_splits(self):
        moments = instants(40)
        plan = assign_splits(moments, self.SPEC)
        groups = [set(plan.of(label)) for label in SplitLabel]
        for i, first in enumerate(groups):
            for second in groups[i + 1 :]:
                assert first.isdisjoint(second)

    def test_the_splits_are_in_chronological_order(self):
        plan = assign_splits(instants(40), self.SPEC)
        train, validation, test = (
            plan.of(SplitLabel.TRAIN),
            plan.of(SplitLabel.VALIDATION),
            plan.of(SplitLabel.TEST),
        )
        assert max(train) < min(validation) < min(test)
        assert max(validation) < min(test)

    def test_unsorted_input_is_ordered_rather_than_trusted(self):
        moments = instants(20)
        shuffled = moments[10:] + moments[:10]
        plan = assign_splits(shuffled, self.SPEC)
        assert [a.as_of for a in plan.assignments] == moments

    def test_counts_cover_every_label(self):
        plan = assign_splits(instants(20), self.SPEC)
        assert set(plan.counts()) == {label.value for label in SplitLabel}


class TestTheEmbargo:
    """The point of the module: a target window may not cross a split boundary."""

    SPEC = SplitSpec(
        train_end=START + timedelta(hours=1),
        validation_end=START + timedelta(hours=2),
        embargo_bars=4,
    )

    def windows(self, moments, horizon_bars):
        """Measured window ends: each row's target reads ``horizon_bars`` ahead."""
        return {
            moment: (moments[i + horizon_bars] if i + horizon_bars < len(moments) else None)
            for i, moment in enumerate(moments)
        }

    def test_a_row_whose_window_crosses_the_boundary_is_embargoed(self):
        moments = instants(40)
        plan = assign_splits(moments, self.SPEC, target_window_ends=self.windows(moments, 4))
        embargoed = plan.of(SplitLabel.EMBARGOED)
        assert embargoed, "rows near the boundary must be withheld"
        for moment in embargoed:
            window_end = self.windows(moments, 4)[moment]
            natural = self.SPEC.label_of(moment)
            assert window_end >= self.SPEC.end_of(natural)

    def test_an_embargoed_row_remembers_where_it_would_have_gone(self):
        moments = instants(40)
        plan = assign_splits(moments, self.SPEC, target_window_ends=self.windows(moments, 4))
        withheld = [a for a in plan.assignments if a.embargoed]
        assert withheld
        for item in withheld:
            assert item.natural_label in (SplitLabel.TRAIN, SplitLabel.VALIDATION)
            assert item.label is SplitLabel.EMBARGOED

    def test_no_surviving_train_row_reads_a_validation_bar(self):
        """The property the embargo exists for, asserted directly rather than by count."""
        moments = instants(40)
        ends = self.windows(moments, 4)
        plan = assign_splits(moments, self.SPEC, target_window_ends=ends)
        for moment in plan.of(SplitLabel.TRAIN):
            assert ends[moment] is None or ends[moment] < self.SPEC.train_end

    def test_no_surviving_validation_row_reads_a_test_bar(self):
        moments = instants(40)
        ends = self.windows(moments, 4)
        plan = assign_splits(moments, self.SPEC, target_window_ends=ends)
        for moment in plan.of(SplitLabel.VALIDATION):
            assert ends[moment] is None or ends[moment] < self.SPEC.validation_end

    def test_test_rows_are_never_embargoed_because_nothing_follows_them(self):
        moments = instants(40)
        plan = assign_splits(moments, self.SPEC, target_window_ends=self.windows(moments, 4))
        for item in plan.assignments:
            if item.natural_label is SplitLabel.TEST:
                assert item.label is SplitLabel.TEST

    def test_a_row_whose_horizon_runs_off_the_end_is_not_embargoed(self):
        """Its target is unresolved, not leaking — those are different problems."""
        moments = instants(40)
        ends = self.windows(moments, 4)
        plan = assign_splits(moments, self.SPEC, target_window_ends=ends)
        tail = [a for a in plan.assignments if ends[a.as_of] is None]
        assert tail
        for item in tail:
            assert item.label is not SplitLabel.EMBARGOED

    def test_a_longer_horizon_embargoes_more_rows(self):
        moments = instants(40)
        spec = SplitSpec(
            train_end=self.SPEC.train_end, validation_end=self.SPEC.validation_end, embargo_bars=8
        )
        few = assign_splits(moments, spec, target_window_ends=self.windows(moments, 2))
        many = assign_splits(moments, spec, target_window_ends=self.windows(moments, 8))
        assert len(many.of(SplitLabel.EMBARGOED)) > len(few.of(SplitLabel.EMBARGOED))

    def test_a_zero_embargo_leaves_every_row_in_its_natural_split(self):
        """Correct only for a dataset with no forward-looking target at all."""
        moments = instants(40)
        spec = SplitSpec(
            train_end=self.SPEC.train_end, validation_end=self.SPEC.validation_end, embargo_bars=0
        )
        plan = assign_splits(moments, spec, target_window_ends=self.windows(moments, 4))
        assert plan.of(SplitLabel.EMBARGOED) == []

    def test_without_measured_windows_the_embargo_falls_back_to_counting_bars(self):
        moments = instants(40)
        plan = assign_splits(moments, self.SPEC)
        assert plan.of(SplitLabel.EMBARGOED), "the bar-counted rule must still withhold rows"

    def test_the_measured_and_counted_rules_agree_on_a_regular_grid(self):
        """On evenly spaced bars the two formulations describe the same rows."""
        moments = instants(40)
        counted = assign_splits(moments, self.SPEC)
        measured = assign_splits(moments, self.SPEC, target_window_ends=self.windows(moments, 4))
        assert set(counted.of(SplitLabel.EMBARGOED)) == set(measured.of(SplitLabel.EMBARGOED))


class TestAuditability:
    SPEC = SplitSpec(
        train_end=START + timedelta(hours=1),
        validation_end=START + timedelta(hours=2),
        embargo_bars=2,
    )

    def test_boundaries_report_first_and_last_of_every_split(self):
        plan = assign_splits(instants(40), self.SPEC)
        report = plan.boundaries()
        for label in ("train", "validation", "test"):
            assert report[label]["count"] > 0
            assert report[label]["first"] <= report[label]["last"]

    def test_the_plan_serialises_every_assignment(self):
        moments = instants(20)
        plan = assign_splits(moments, self.SPEC)
        payload = plan.as_dict()
        assert len(payload["assignments"]) == len(moments)
        assert payload["spec"] == self.SPEC.as_dict()

    def test_label_at_returns_none_for_an_unknown_instant(self):
        plan = assign_splits(instants(10), self.SPEC)
        assert plan.label_at(START - timedelta(days=1)) is None

    def test_no_random_split_helper_exists_anywhere_in_the_module(self):
        """CLAUDE.md rule 6: a financial series is never shuffled."""
        from tests.test_market_state import _code_of

        code = _code_of("ict_kronos/features/splits.py")
        for banned in ("train_test_split", "shuffle", "random", "sample("):
            assert banned not in code, f"splits.py offers a non-chronological split: {banned!r}"
        assert "def assign_splits(" in code
