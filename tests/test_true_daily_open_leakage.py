"""R2-05.1 leakage and replay — the tests that make the story worth having.

The True Daily Open is the one Phase 2 concept with **zero** confirmation lag, which
is exactly the shape of the ForexQuant bug this project keeps finding. It is correct
here for a specific reason — the level reads a bar's ``open``, which is fixed at the
bar's first print — and these tests exist to prove that the zero lag is a consequence
of that reason and not of a missing check.

The proofs are behavioural wherever possible: data is mutated and the detector re-run,
rather than the source being inspected and pronounced fine.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame
from ict_kronos.ict import (
    ContractViolation,
    TrueDailyOpenConfig,
    TrueDailyOpenDetector,
    assert_no_leakage,
    assert_observable,
    filter_observable,
)

H1 = Timeframe.H1
START = datetime(2024, 3, 8, 0, 0, tzinfo=UTC)
EST_BOUNDARY = datetime(2024, 3, 8, 5, 0, tzinfo=UTC)


def span(hours: int, *, start=START, timeframe=H1):
    candles = []
    for i in range(hours):
        base = 1.1000 + i / 10000
        candles.append(
            MarketCandle(
                timestamp=start + timedelta(minutes=timeframe.minutes * i),
                symbol=Symbol.EURUSD,
                timeframe=timeframe,
                open=base,
                high=base + 0.0020,
                low=base - 0.0020,
                close=base + 0.0005,
                volume=1.0,
            )
        )
    return candles_to_frame(candles)


@pytest.fixture
def detector() -> TrueDailyOpenDetector:
    return TrueDailyOpenDetector()


@pytest.fixture
def frame():
    return span(24 * 4)


class TestObservabilityWindow:
    def test_the_level_does_not_exist_before_the_boundary(self, detector, frame):
        levels = detector.detect(frame, Symbol.EURUSD, H1)
        just_before = EST_BOUNDARY - timedelta(microseconds=1)

        visible = filter_observable(levels, just_before)
        assert date(2024, 3, 8) not in {level.trading_date for level in visible}

    def test_the_level_becomes_observable_at_the_boundary(self, detector, frame):
        levels = detector.detect(frame, Symbol.EURUSD, H1)
        visible = filter_observable(levels, EST_BOUNDARY)

        assert date(2024, 3, 8) in {level.trading_date for level in visible}
        assert_observable(visible, EST_BOUNDARY)

    def test_nothing_is_observable_before_the_first_boundary(self, detector, frame):
        levels = detector.detect(frame, Symbol.EURUSD, H1)
        assert filter_observable(levels, START) == []

    def test_observable_at_never_returns_a_future_level(self, detector, frame):
        as_of = datetime(2024, 3, 10, 12, 0, tzinfo=UTC)
        visible = detector.observable_at(frame, as_of, Symbol.EURUSD, H1)

        assert visible
        assert_observable(visible, as_of)
        assert all(level.event_timestamp <= as_of for level in visible)

    def test_every_prefix_of_time_is_monotone(self, detector, frame):
        """What is visible can only grow as time advances — never shrink or change."""
        levels = detector.detect(frame, Symbol.EURUSD, H1)
        seen: list = []
        for hours in range(0, 24 * 4, 3):
            as_of = START + timedelta(hours=hours)
            visible = filter_observable(levels, as_of)
            assert visible[: len(seen)] == seen
            seen = visible

    def test_events_carry_no_leakage(self, detector, frame):
        assert_no_leakage(detector.events(frame, Symbol.EURUSD, H1))


class TestFutureBarsAreInert:
    def test_appending_bars_cannot_modify_an_existing_level(self, detector):
        short, long = span(24 * 2), span(24 * 4)

        early = detector.detect(short, Symbol.EURUSD, H1)
        late = detector.detect(long, Symbol.EURUSD, H1)

        assert early == late[: len(early)]

    def test_later_high_low_close_and_volume_are_irrelevant(self, detector, frame):
        """Behavioural proof: wreck every later bar and the levels must not move."""
        before = detector.detect(frame, Symbol.EURUSD, H1)

        mutated = frame.copy()
        after_boundary = mutated["timestamp"] > EST_BOUNDARY
        mutated.loc[after_boundary, "high"] = 9.9999
        mutated.loc[after_boundary, "low"] = 0.0001
        mutated.loc[after_boundary, "close"] = 5.0
        mutated.loc[after_boundary, "volume"] = 1e9

        assert detector.detect(mutated, Symbol.EURUSD, H1) == before

    def test_the_boundary_bars_own_high_low_and_close_are_irrelevant(self, detector, frame):
        """The strongest form: even the SAME bar's other prices cannot matter."""
        before = detector.detect(frame, Symbol.EURUSD, H1)

        mutated = frame.copy()
        row = mutated["timestamp"] == EST_BOUNDARY
        mutated.loc[row, "high"] = 9.9999
        mutated.loc[row, "low"] = 0.0001
        mutated.loc[row, "close"] = 5.0

        after = detector.detect(mutated, Symbol.EURUSD, H1)
        assert after == before

    def test_changing_the_boundary_bars_open_DOES_change_the_level(self, detector, frame):
        """The control. If this passed unchanged, the tests above would prove nothing."""
        before = detector.detect(frame, Symbol.EURUSD, H1)

        mutated = frame.copy()
        mutated.loc[mutated["timestamp"] == EST_BOUNDARY, "open"] = 1.2345

        after = detector.detect(mutated, Symbol.EURUSD, H1)
        assert after != before
        assert after[0].price_level == pytest.approx(1.2345)

    def test_a_later_date_cannot_mutate_an_earlier_one(self, detector, frame):
        before = detector.detect(frame, Symbol.EURUSD, H1)

        mutated = frame.copy()
        later = mutated["timestamp"] >= datetime(2024, 3, 10, tzinfo=UTC)
        mutated.loc[later, "open"] = 7.5

        after = detector.detect(mutated, Symbol.EURUSD, H1)
        first_two = [level for level in after if level.trading_date <= date(2024, 3, 9)]
        assert first_two == [level for level in before if level.trading_date <= date(2024, 3, 9)]

    def test_truncating_the_future_leaves_the_past_identical(self, detector, frame):
        full = detector.detect(frame, Symbol.EURUSD, H1)
        for cut in range(6, len(frame), 12):
            prefix = detector.detect(frame.iloc[:cut], Symbol.EURUSD, H1)
            assert prefix == full[: len(prefix)]


class TestTheNaiveAlternatives:
    """Each of these is a plausible implementation that leaks or fabricates.

    The production detector must disagree with every one of them.
    """

    def test_it_is_not_the_first_bar_of_the_utc_day(self, detector, frame):
        naive = {
            stamp.date(): float(row["open"])
            for stamp, row in ((row["timestamp"].to_pydatetime(), row) for _, row in frame.iterrows())
            if stamp.hour == 0
        }
        real = {level.trading_date: level.price_level for level in detector.detect(frame, Symbol.EURUSD, H1)}

        assert naive  # the naive answer exists...
        assert real  # ...and so does ours...
        assert real != naive  # ...and they are different prices.

    def test_it_is_not_the_nearest_bar_to_the_boundary(self, detector):
        """Boundary bar deleted. A nearest-bar implementation would still answer."""
        frame = span(24 * 2)
        without = frame[frame["timestamp"] != EST_BOUNDARY]

        assert detector.detect(without, Symbol.EURUSD, H1) == [
            level
            for level in detector.detect(frame, Symbol.EURUSD, H1)
            if level.trading_date != date(2024, 3, 8)
        ]

    def test_it_is_not_the_previous_bars_close(self, detector, frame):
        previous = frame[frame["timestamp"] == EST_BOUNDARY - timedelta(hours=1)].iloc[0]
        level = detector.detect(frame, Symbol.EURUSD, H1)[0]

        assert level.price_level != pytest.approx(float(previous["close"]))

    def test_it_is_not_the_17_00_trading_day_boundary(self, detector, frame):
        """R2-04's daily rollover is 17:00 NY == 22:00 UTC that date. Different bar."""
        trading_day_bar = frame[frame["timestamp"] == datetime(2024, 3, 8, 22, 0, tzinfo=UTC)].iloc[0]
        level = detector.detect(frame, Symbol.EURUSD, H1)[0]

        assert level.event_timestamp != datetime(2024, 3, 8, 22, 0, tzinfo=UTC)
        assert level.price_level != pytest.approx(float(trading_day_bar["open"]))

    def test_a_fixed_utc_offset_implementation_would_disagree_across_dst(self, detector):
        """The bug the local-time definition exists to prevent.

        A detector that froze "00:00 NY == 05:00 UTC" during EST keeps answering 05:00
        after the transition. Ours moves to 04:00. Proving they DIVERGE is the point;
        an implementation that agreed with the frozen offset would be the broken one.
        """
        frame = span(24 * 6)
        levels = {
            level.trading_date: level.event_timestamp for level in detector.detect(frame, Symbol.EURUSD, H1)
        }

        for day, hardcoded_est in (
            (date(2024, 3, 11), datetime(2024, 3, 11, 5, 0, tzinfo=UTC)),
            (date(2024, 3, 12), datetime(2024, 3, 12, 5, 0, tzinfo=UTC)),
        ):
            assert levels[day] != hardcoded_est
            assert levels[day] == hardcoded_est - timedelta(hours=1)


class TestBatchStreamEquivalence:
    def test_batch_equals_prefix_replay(self, detector, frame):
        full = detector.detect(frame, Symbol.EURUSD, H1)
        for cut in range(1, len(frame) + 1):
            prefix = detector.detect(frame.iloc[:cut], Symbol.EURUSD, H1)
            assert prefix == full[: len(prefix)]

    def test_batch_equals_bar_by_bar_streaming(self, detector, frame):
        """True replay: feed one bar at a time, keep only newly emitted levels."""
        streamed: list = []
        for cut in range(1, len(frame) + 1):
            for level in detector.detect(frame.iloc[:cut], Symbol.EURUSD, H1):
                if level not in streamed:
                    streamed.append(level)

        assert streamed == detector.detect(frame, Symbol.EURUSD, H1)

    def test_a_level_is_emitted_on_the_bar_that_opens_it(self, detector, frame):
        """No earlier, no later — the defining timing property."""
        stamps = list(frame["timestamp"])
        boundary_index = stamps.index(EST_BOUNDARY)

        before = detector.detect(frame.iloc[:boundary_index], Symbol.EURUSD, H1)
        at = detector.detect(frame.iloc[: boundary_index + 1], Symbol.EURUSD, H1)

        assert date(2024, 3, 8) not in {level.trading_date for level in before}
        assert date(2024, 3, 8) in {level.trading_date for level in at}

    def test_historical_levels_are_identical_after_appending(self, detector):
        short = span(24 * 2)
        long = span(24 * 4)

        early = detector.detect(short, Symbol.EURUSD, H1)
        late = detector.detect(long, Symbol.EURUSD, H1)

        for a, b in zip(early, late, strict=False):
            assert a == b
            assert a.as_dict() == b.as_dict()

    def test_events_are_stable_under_appending(self, detector):
        early = detector.events(span(24 * 2), Symbol.EURUSD, H1)
        late = detector.events(span(24 * 4), Symbol.EURUSD, H1)

        assert [e.as_dict() for e in early] == [e.as_dict() for e in late[: len(early)]]


class TestTheSharedGate:
    def test_the_module_hand_rolls_no_observability_comparison(self):
        """Source-level guard, mirroring R2-04 and R2-05."""
        from pathlib import Path

        source = Path("ict_kronos/ict/true_daily_open.py").read_text(encoding="utf-8")
        offenders = [
            line.strip()
            for line in source.splitlines()
            if "confirmation_timestamp <=" in line or "confirmation_timestamp >=" in line
        ]
        assert offenders == [], f"true_daily_open.py re-implements the observability rule: {offenders}"

    def test_the_record_exposes_the_shared_predicate(self, detector, frame):
        level = detector.detect(frame, Symbol.EURUSD, H1)[0]

        assert not level.is_observable_at(level.confirmation_timestamp - timedelta(microseconds=1))
        assert level.is_observable_at(level.confirmation_timestamp)
        assert level.is_observable_at(level.confirmation_timestamp + timedelta(hours=1))

    def test_naive_timestamps_are_rejected(self, detector, frame):
        naive = datetime(2024, 3, 8, 12, 0)  # noqa: DTZ001
        with pytest.raises(ContractViolation, match="timezone-aware"):
            detector.observable_at(frame, naive, Symbol.EURUSD, H1)

    def test_assert_observable_catches_a_forged_level(self, detector, frame):
        from dataclasses import replace as dc_replace

        level = detector.detect(frame, Symbol.EURUSD, H1)[0]
        forged = dc_replace(level, confirmation_timestamp=level.event_timestamp + timedelta(days=99))

        with pytest.raises(ContractViolation):
            assert_observable([forged], level.event_timestamp)


class TestIsolationFromOtherDetectors:
    def test_it_reads_bars_and_nothing_else(self):
        """No coupling to structure, liquidity, swings or FVG."""
        from pathlib import Path

        source = Path("ict_kronos/ict/true_daily_open.py").read_text(encoding="utf-8")
        for forbidden in ("from .structure import", "from .liquidity import", "from .fvg import"):
            assert forbidden not in source

    def test_it_reuses_the_single_dst_implementation(self):
        """It must NOT carry a second copy of the PEP-495 fold handling."""
        from pathlib import Path

        source = Path("ict_kronos/ict/true_daily_open.py").read_text(encoding="utf-8")
        assert "_local_to_utc" in source
        assert "fold=" not in source

    def test_future_structure_and_liquidity_cannot_change_a_level(self, detector, frame):
        """Composed with R2-03/R2-04 output, the level is unmoved."""
        from ict_kronos.ict import LiquidityDetector, StructureDetector

        before = detector.detect(frame, Symbol.EURUSD, H1)
        StructureDetector().analyse(frame, Symbol.EURUSD, H1)
        LiquidityDetector().analyse(frame, Symbol.EURUSD, H1)

        assert detector.detect(frame, Symbol.EURUSD, H1) == before


class TestDeterminism:
    def test_repeated_detection_is_identical(self, detector, frame):
        first = detector.detect(frame, Symbol.EURUSD, H1)
        for _ in range(3):
            assert detector.detect(frame, Symbol.EURUSD, H1) == first

    def test_two_detectors_with_the_same_config_agree(self, frame):
        a = TrueDailyOpenDetector(TrueDailyOpenConfig())
        b = TrueDailyOpenDetector(TrueDailyOpenConfig())
        assert a.detect(frame, Symbol.EURUSD, H1) == b.detect(frame, Symbol.EURUSD, H1)
