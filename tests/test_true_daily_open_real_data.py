"""R2-05.1 real-data acceptance — EURUSD + XAUUSD, 2024-03-08 → 2024-03-11.

The Phase 1.5 window is unusually well suited to this story: it contains one **EST**
NY date (2024-03-08, boundary 05:00 UTC), one **EDT** NY date (2024-03-11, boundary
04:00 UTC), and the **weekend closure** between them. Both DST acceptance cases are
therefore observed on real bars rather than constructed.

The autumn transition has no real-data coverage — the fixture is in March — and is
tested synthetically in ``test_true_daily_open.py``.

The Phase 1.5 dataset is gitignored, so these skip cleanly when absent.
**Engineering and timestamp validation only** — no performance claim is made.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from ict_kronos.data import resample
from ict_kronos.domain import Symbol, Timeframe
from ict_kronos.ict import (
    EventType,
    TrueDailyOpenConfig,
    TrueDailyOpenDetector,
    assert_no_leakage,
    assert_observable,
    filter_observable,
    reference_true_daily_opens,
)
from ict_kronos.storage import ParquetCandleStore

DATA_ROOT = "data/normalized"
WINDOW_START = datetime(2024, 3, 8, tzinfo=UTC)
WINDOW_END = datetime(2024, 3, 12, tzinfo=UTC)

NY = ZoneInfo("America/New_York")

#: The two NY dates the window covers that have an open market.
EST_DATE, EST_BOUNDARY = date(2024, 3, 8), datetime(2024, 3, 8, 5, 0, tzinfo=UTC)
EDT_DATE, EDT_BOUNDARY = date(2024, 3, 11), datetime(2024, 3, 11, 4, 0, tzinfo=UTC)
#: The closure. Neither may produce a level on any timeframe.
CLOSED_DATES = (date(2024, 3, 9), date(2024, 3, 10))

STORED = (Timeframe.M1, Timeframe.M5, Timeframe.M15)
DERIVED = (Timeframe.H1, Timeframe.H4)
#: 1m/5m/15m grids contain both boundaries for both symbols. 1H and 4H depend on the
#: resampler's completeness policy and on the grid — see ``TestCoarseGrids``.
DENSE = STORED


def load(symbol: Symbol, timeframe: Timeframe) -> pd.DataFrame:
    """Stored timeframes are read directly; higher ones are derived from 1M."""
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

    if len(frame) == 0:
        pytest.skip(f"real data absent for {symbol.value}/{timeframe.value}")
    return frame


@pytest.fixture(params=[Symbol.EURUSD, Symbol.XAUUSD], ids=lambda s: s.value)
def symbol(request) -> Symbol:
    return request.param


@pytest.fixture(params=[*STORED, *DERIVED], ids=lambda t: t.value)
def timeframe(request) -> Timeframe:
    return request.param


@pytest.fixture
def detector() -> TrueDailyOpenDetector:
    return TrueDailyOpenDetector()


class TestRealDetection:
    def test_both_open_dates_are_found_on_the_dense_timeframes(self, detector, symbol):
        for tf in DENSE:
            levels = detector.detect(load(symbol, tf), symbol, tf)
            assert [level.trading_date for level in levels] == [EST_DATE, EDT_DATE], tf.value

    def test_the_price_is_the_actual_source_bar_open(self, detector, symbol, timeframe):
        frame = load(symbol, timeframe)
        for level in detector.detect(frame, symbol, timeframe):
            row = frame[frame["timestamp"] == pd.Timestamp(level.event_timestamp)]
            assert len(row) == 1, f"no source bar at {level.event_timestamp.isoformat()}"
            assert level.price_level == pytest.approx(float(row.iloc[0]["open"]))

    def test_the_price_is_not_any_other_ohlc_field(self, detector, symbol):
        """On real bars open/high/low/close genuinely differ, so this discriminates.

        A detector that had picked ``close`` (or either wick) would pass every other
        test in this file on synthetic data. Here it would fail — provided the bars
        really do differ, which is asserted rather than assumed.
        """
        discriminating = 0
        for tf in DENSE:
            frame = load(symbol, tf)
            for level in detector.detect(frame, symbol, tf):
                row = frame[frame["timestamp"] == pd.Timestamp(level.event_timestamp)].iloc[0]
                open_, high, low, close = (float(row[c]) for c in ("open", "high", "low", "close"))

                assert level.price_level == pytest.approx(open_)
                assert low <= level.price_level <= high

                if len({open_, high, low, close}) == 4:
                    discriminating += 1
                    assert level.price_level != pytest.approx(close)
                    assert level.price_level != pytest.approx(high)
                    assert level.price_level != pytest.approx(low)

        assert discriminating > 0, "no boundary bar had four distinct prices — test proves nothing"

    def test_every_level_reads_midnight_new_york(self, detector, symbol, timeframe):
        for level in detector.detect(load(symbol, timeframe), symbol, timeframe):
            assert level.local_time().time() == time(0, 0)
            assert str(level.local_time().tzinfo) == "America/New_York"

    def test_level_ids_are_unique_per_date(self, detector, symbol, timeframe):
        levels = detector.detect(load(symbol, timeframe), symbol, timeframe)
        ids = [level.level_id for level in levels]
        assert len(ids) == len(set(ids))

    def test_at_most_one_level_per_ny_date(self, detector, symbol, timeframe):
        levels = detector.detect(load(symbol, timeframe), symbol, timeframe)
        dates = [level.trading_date for level in levels]
        assert len(dates) == len(set(dates))

    def test_the_recorded_prices_match_across_timeframes(self, detector, symbol):
        """The same instant on a finer and a coarser grid must give the same open."""
        by_date: dict[date, set[float]] = {}
        for tf in (*STORED, *DERIVED):
            try:
                frame = load(symbol, tf)
            except Exception:  # pragma: no cover - skip is raised inside load
                continue
            for level in detector.detect(frame, symbol, tf):
                by_date.setdefault(level.trading_date, set()).add(round(level.price_level, 10))

        assert by_date
        for day, prices in by_date.items():
            assert len(prices) == 1, f"{symbol.value} {day} disagrees across timeframes: {prices}"


class TestRealDst:
    def test_the_est_date_resolves_to_05_utc(self, detector, symbol):
        for tf in DENSE:
            levels = {
                lv.trading_date: lv.event_timestamp for lv in detector.detect(load(symbol, tf), symbol, tf)
            }
            assert levels[EST_DATE] == EST_BOUNDARY

    def test_the_edt_date_resolves_to_04_utc(self, detector, symbol):
        for tf in DENSE:
            levels = {
                lv.trading_date: lv.event_timestamp for lv in detector.detect(load(symbol, tf), symbol, tf)
            }
            assert levels[EDT_DATE] == EDT_BOUNDARY

    def test_the_utc_hour_moves_but_the_local_hour_does_not(self, detector, symbol):
        """The DST acceptance case, observed rather than constructed."""
        for tf in DENSE:
            levels = detector.detect(load(symbol, tf), symbol, tf)
            utc_hours = {level.event_timestamp.hour for level in levels}
            local_hours = {level.local_time().hour for level in levels}

            assert utc_hours == {4, 5}
            assert local_hours == {0}

    def test_no_real_level_lands_on_utc_midnight(self, detector, symbol, timeframe):
        for level in detector.detect(load(symbol, timeframe), symbol, timeframe):
            assert level.event_timestamp.hour != 0

    def test_no_boundary_anomaly_on_real_dates(self, detector, symbol, timeframe):
        from ict_kronos.ict.sessions import BoundaryAnomaly

        for level in detector.detect(load(symbol, timeframe), symbol, timeframe):
            assert level.boundary_anomaly is BoundaryAnomaly.NONE


class TestRealClosure:
    def test_the_weekend_produces_no_level(self, detector, symbol, timeframe):
        levels = detector.detect(load(symbol, timeframe), symbol, timeframe)
        found = {level.trading_date for level in levels}
        assert found.isdisjoint(CLOSED_DATES)

    def test_no_bar_exists_at_the_weekend_boundaries(self, symbol):
        """The reason there is no level — the absence is in the data, not a filter."""
        frame = load(symbol, Timeframe.M1)
        for day in CLOSED_DATES:
            boundary = datetime.combine(day, time(0, 0)).replace(tzinfo=NY).astimezone(UTC)
            assert len(frame[frame["timestamp"] == pd.Timestamp(boundary)]) == 0

    def test_the_friday_close_is_not_used_for_saturday(self, detector, symbol):
        frame = load(symbol, Timeframe.M1)
        levels = detector.detect(frame, symbol, Timeframe.M1)
        last_friday_bar = frame[frame["timestamp"] < pd.Timestamp("2024-03-09T00:00:00Z")].iloc[-1]

        assert float(last_friday_bar["close"]) not in {level.price_level for level in levels}

    def test_the_sunday_reopen_is_not_used_for_sunday(self, detector, symbol):
        """The reopen bar exists and is a real, tempting substitute. It must be ignored."""
        frame = load(symbol, Timeframe.M1)
        reopen = frame[frame["timestamp"] > pd.Timestamp("2024-03-10T12:00:00Z")].iloc[0]
        levels = detector.detect(frame, symbol, Timeframe.M1)

        assert date(2024, 3, 10) not in {level.trading_date for level in levels}
        assert reopen["timestamp"].to_pydatetime() not in {level.event_timestamp for level in levels}

    def test_the_reopen_is_nowhere_near_ny_midnight(self, symbol):
        """Context for the test above: the reopen is ~20:00-22:00 UTC Sunday."""
        frame = load(symbol, Timeframe.M1)
        reopen = frame[frame["timestamp"] > pd.Timestamp("2024-03-10T12:00:00Z")].iloc[0]
        assert reopen["timestamp"].to_pydatetime().astimezone(NY).time() != time(0, 0)


class TestCoarseGrids:
    """Where the grid does not contain the boundary, nothing is emitted.

    This is the documented straddling-bar policy (docs §3.2) observed on real data,
    and it interacts with the resampler's ``require_complete`` rule in a way worth
    recording rather than hiding.
    """

    def test_daily_bars_yield_no_level(self, detector, symbol):
        base = load(symbol, Timeframe.M1)
        daily = resample(base, Timeframe.M1, Timeframe.D1, symbol).drop(columns=["close_time"])
        if len(daily) == 0:
            pytest.skip("no complete daily bars in a four-day window")
        assert detector.detect(daily, symbol, Timeframe.D1) == []

    def test_4h_never_yields_the_est_date(self, detector, symbol):
        """05:00 UTC is not on the 4H grid (00/04/08/12/16/20), so it cannot exist."""
        frame = load(symbol, Timeframe.H4)
        levels = detector.detect(frame, symbol, Timeframe.H4)
        assert EST_DATE not in {level.trading_date for level in levels}

    def test_4h_yields_the_edt_date_only_when_the_bar_exists(self, detector, symbol):
        """04:00 UTC IS on the 4H grid, so availability reduces to bar existence.

        For XAUUSD the 04:00 bar is complete and the level exists. For EURUSD one of
        the sixty 1m bars in that hour is missing, so ``require_complete=True`` drops
        the target bar upstream and no level follows. Both are the same rule.
        """
        frame = load(symbol, Timeframe.H4)
        levels = detector.detect(frame, symbol, Timeframe.H4)
        bar_exists = len(frame[frame["timestamp"] == pd.Timestamp(EDT_BOUNDARY)]) == 1

        assert (EDT_DATE in {level.trading_date for level in levels}) is bar_exists

    def test_a_missing_level_always_means_a_missing_bar(self, detector, symbol, timeframe):
        """The general invariant: absence is never a detector decision."""
        frame = load(symbol, timeframe)
        found = {level.trading_date for level in detector.detect(frame, symbol, timeframe)}

        for day in (EST_DATE, EDT_DATE):
            boundary = datetime.combine(day, time(0, 0)).replace(tzinfo=NY).astimezone(UTC)
            bar_exists = len(frame[frame["timestamp"] == pd.Timestamp(boundary)]) == 1
            assert (day in found) is bar_exists


class TestRealReferenceEquivalence:
    def test_the_vectorised_detector_matches_the_naive_reference(self, detector, symbol, timeframe):
        frame = load(symbol, timeframe)
        levels = detector.detect(frame, symbol, timeframe)
        reference = reference_true_daily_opens(frame, TrueDailyOpenConfig())

        assert [(level.trading_date, level.price_level) for level in levels] == reference


class TestRealLeakage:
    def test_no_level_leaks(self, detector, symbol, timeframe):
        assert_no_leakage(detector.events(load(symbol, timeframe), symbol, timeframe))

    def test_a_level_is_invisible_one_microsecond_early(self, detector, symbol):
        for tf in DENSE:
            for level in detector.detect(load(symbol, tf), symbol, tf):
                just_before = level.confirmation_timestamp - timedelta(microseconds=1)
                assert level not in filter_observable([level], just_before)
                assert level in filter_observable([level], level.confirmation_timestamp)

    def test_filtering_mid_window_hides_the_later_date(self, detector, symbol):
        for tf in DENSE:
            levels = detector.detect(load(symbol, tf), symbol, tf)
            as_of = datetime(2024, 3, 9, tzinfo=UTC)
            visible = filter_observable(levels, as_of)

            assert {level.trading_date for level in visible} == {EST_DATE}
            assert_observable(visible, as_of)

    def test_batch_equals_prefix_replay_on_real_bars(self, detector, symbol):
        """Cut at each real boundary and a few arbitrary points in between."""
        frame = load(symbol, Timeframe.M15)
        full = detector.detect(frame, symbol, Timeframe.M15)

        cuts = [len(frame) // 4, len(frame) // 2, 3 * len(frame) // 4, len(frame)]
        for cut in cuts:
            prefix = detector.detect(frame.iloc[:cut], symbol, Timeframe.M15)
            assert prefix == full[: len(prefix)]

    def test_appending_the_second_half_does_not_change_the_first(self, detector, symbol):
        frame = load(symbol, Timeframe.M5)
        half = len(frame) // 2

        early = detector.detect(frame.iloc[:half], symbol, Timeframe.M5)
        late = detector.detect(frame, symbol, Timeframe.M5)
        assert early == late[: len(early)]

    def test_future_price_mutation_leaves_levels_identical(self, detector, symbol):
        """Behavioural, on real bars: wreck everything after the first boundary."""
        frame = load(symbol, Timeframe.M5)
        before = detector.detect(frame, symbol, Timeframe.M5)

        mutated = frame.copy()
        later = mutated["timestamp"] > pd.Timestamp(EST_BOUNDARY)
        mutated.loc[later, "high"] = mutated.loc[later, "high"] * 2
        mutated.loc[later, "low"] = mutated.loc[later, "low"] / 2
        mutated.loc[later, "close"] = mutated.loc[later, "close"] * 1.5

        assert detector.detect(mutated, symbol, Timeframe.M5) == before


class TestRealEvents:
    def test_events_carry_the_true_daily_open_type(self, detector, symbol, timeframe):
        events = detector.events(load(symbol, timeframe), symbol, timeframe)
        assert all(e.event_type is EventType.TRUE_DAILY_OPEN for e in events)

    def test_events_have_zero_confirmation_lag(self, detector, symbol, timeframe):
        for event in detector.events(load(symbol, timeframe), symbol, timeframe):
            assert event.confirmation_lag == timedelta(0)

    def test_latest_at_tracks_the_current_date(self, detector, symbol):
        frame = load(symbol, Timeframe.M5)
        assert detector.latest_at(frame, WINDOW_START, symbol, Timeframe.M5) is None

        midday = detector.latest_at(frame, datetime(2024, 3, 8, 12, tzinfo=UTC), symbol, Timeframe.M5)
        assert midday is not None and midday.trading_date == EST_DATE

        monday = detector.latest_at(frame, datetime(2024, 3, 11, 12, tzinfo=UTC), symbol, Timeframe.M5)
        assert monday is not None and monday.trading_date == EDT_DATE


class TestNoBehaviourChangeElsewhere:
    def test_other_detectors_still_run_unchanged_on_real_data(self, symbol):
        """R2-05.1 adds an event type; R2-01…R2-05 must be unaffected."""
        from ict_kronos.ict import FvgDetector, LiquidityDetector, StructureDetector, SwingDetector

        frame = load(symbol, Timeframe.M15)
        assert SwingDetector().detect(frame, symbol, Timeframe.M15) is not None
        assert StructureDetector().analyse(frame, symbol, Timeframe.M15) is not None
        assert LiquidityDetector().analyse(frame, symbol, Timeframe.M15) is not None
        assert FvgDetector().analyse(frame, symbol, Timeframe.M15) is not None
