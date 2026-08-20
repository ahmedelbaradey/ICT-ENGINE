"""R2-05 real-data acceptance — EURUSD + XAUUSD, 2024-03-08 → 2024-03-12.

Timeframes: 1M, 5M, 15M stored; 1H and 4H derived from 1M by the R2-01 resampler.

**1D and 1W are not validated here, and that is a dataset limit, not an
implementation limit.** A four-day window resamples to **zero** complete daily bars
(`require_complete=True` drops partial periods, and the weekend closure means no
UTC-midnight-anchored 1440-minute bar is complete), and `Timeframe` has no weekly
member at all. Three bars are the minimum for any FVG, so neither timeframe can
produce or refute a single zone. Fabricating a vacuous pass would be worse than
recording the gap — see `docs/ict/fvg.md` §13.6.

The Phase 1.5 dataset is gitignored, so these skip cleanly when absent.
**Engineering and timestamp validation only** — no performance claim is made.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from ict_kronos.data import resample
from ict_kronos.domain import Symbol, Timeframe
from ict_kronos.ict import (
    Direction,
    EventType,
    FvgConfig,
    FvgDetector,
    FvgStatus,
    GapMeasure,
    assert_no_leakage,
    filter_observable,
    reference_zones,
)
from ict_kronos.storage import ParquetCandleStore

DATA_ROOT = "data/normalized"
WINDOW_START = datetime(2024, 3, 8, tzinfo=UTC)
WINDOW_END = datetime(2024, 3, 12, tzinfo=UTC)

FRIDAY_CLOSE = pd.Timestamp("2024-03-08T22:00:00Z")
SUNDAY_REOPEN = pd.Timestamp("2024-03-10T20:00:00Z")
US_DST = pd.Timestamp("2024-03-10T07:00:00Z")

#: Stored natively.
STORED = (Timeframe.M1, Timeframe.M5, Timeframe.M15)
#: Derived from 1M via the R2-01 resampler.
DERIVED = (Timeframe.H1, Timeframe.H4)


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

    if len(frame) < 3:
        pytest.skip(f"real data insufficient for {symbol.value}/{timeframe.value}")
    return frame


@pytest.fixture(params=[Symbol.EURUSD, Symbol.XAUUSD], ids=lambda s: s.value)
def symbol(request) -> Symbol:
    return request.param


@pytest.fixture(params=[*STORED, *DERIVED], ids=lambda t: t.value)
def timeframe(request) -> Timeframe:
    return request.param


@pytest.fixture
def detector() -> FvgDetector:
    return FvgDetector(FvgConfig())


class TestRealDataDetection:
    def test_fvgs_are_found_on_the_dense_timeframes(self, detector, symbol):
        """1M/5M/15M always have enough bars for zones to occur."""
        for dense in STORED:
            zones = detector.detect(load(symbol, dense), symbol, dense)
            assert zones, f"{symbol.value}/{dense.value}: no FVGs"

    def test_detection_runs_cleanly_on_the_sparse_timeframes(self, detector, symbol, timeframe):
        """1H and 4H resample to few bars in a four-day window, and a genuine absence
        of imbalance is a valid result — XAUUSD's nine 4H bars overlap throughout, so
        it has zero FVGs while EURUSD has two. Assert the invariants hold for whatever
        is found rather than demanding a non-empty result the data need not contain.
        """
        zones = detector.detect(load(symbol, timeframe), symbol, timeframe)
        for zone in zones:
            assert zone.top > zone.bottom
            assert zone.confirmation_timestamp > zone.formation_timestamp

    def test_both_directions_occur(self, detector, symbol):
        zones = detector.detect(load(symbol, Timeframe.M5), symbol, Timeframe.M5)
        assert {z.direction for z in zones} == {Direction.BULLISH, Direction.BEARISH}

    def test_boundaries_match_real_bar_prices(self, detector, symbol):
        """Every zone edge must be a price that actually printed, on the recorded bar."""
        frame = load(symbol, Timeframe.M5)
        rows = frame.set_index("timestamp")

        for zone in detector.detect(frame, symbol, Timeframe.M5):
            c1 = rows.loc[pd.Timestamp(zone.candle1_timestamp)]
            c3 = rows.loc[pd.Timestamp(zone.candle3_timestamp)]
            if zone.is_bullish:
                assert zone.bottom == pytest.approx(float(c1["high"]))
                assert zone.top == pytest.approx(float(c3["low"]))
            else:
                assert zone.top == pytest.approx(float(c1["low"]))
                assert zone.bottom == pytest.approx(float(c3["high"]))

    def test_every_zone_has_a_positive_gap(self, detector, symbol, timeframe):
        for zone in detector.detect(load(symbol, timeframe), symbol, timeframe):
            assert zone.top > zone.bottom
            assert zone.size > 0 and zone.size_points > 0

    def test_the_three_candles_are_contiguous(self, detector, symbol):
        """The default guard, verified on real bars around a real weekend."""
        duration = pd.Timedelta(minutes=5)
        for zone in detector.detect(load(symbol, Timeframe.M5), symbol, Timeframe.M5):
            assert pd.Timestamp(zone.candle1_timestamp) + duration == pd.Timestamp(zone.candle2_timestamp)
            assert pd.Timestamp(zone.candle2_timestamp) + duration == pd.Timestamp(zone.candle3_timestamp)

    def test_vectorised_matches_the_naive_reference(self, detector, symbol, timeframe):
        """Real prices quantise hard — this is where an off-by-one would surface."""
        frame = load(symbol, timeframe)
        detected = [(z.index, z.direction.value) for z in detector.detect(frame, symbol, timeframe)]
        expected = reference_zones(frame, detector.config, timeframe, symbol.spec.point_value)
        assert detected == expected

    @pytest.mark.parametrize("measure", list(GapMeasure))
    def test_both_measures_run_on_real_data(self, symbol, measure):
        zones = FvgDetector(FvgConfig(measure=measure)).detect(
            load(symbol, Timeframe.M5), symbol, Timeframe.M5
        )
        assert all(z.size > 0 for z in zones)

    def test_a_minimum_gap_threshold_reduces_the_count(self, symbol):
        frame = load(symbol, Timeframe.M5)
        loose = FvgDetector(FvgConfig(min_gap_points=0)).detect(frame, symbol, Timeframe.M5)
        strict = FvgDetector(FvgConfig(min_gap_points=20)).detect(frame, symbol, Timeframe.M5)
        assert len(strict) < len(loose)


class TestRealDataTimestamps:
    def test_confirmation_is_exactly_one_bar_after_formation(self, detector, symbol, timeframe):
        expected = timedelta(minutes=timeframe.minutes)
        zones = detector.detect(load(symbol, timeframe), symbol, timeframe)
        for zone in zones:
            assert zone.confirmation_timestamp - zone.formation_timestamp == expected

    def test_formation_is_candle3_open(self, detector, symbol):
        for zone in detector.detect(load(symbol, Timeframe.M5), symbol, Timeframe.M5):
            assert zone.formation_timestamp == zone.candle3_timestamp

    def test_no_zone_is_observable_at_its_own_formation(self, detector, symbol):
        """The legacy off-by-one, checked against real bars."""
        for zone in detector.detect(load(symbol, Timeframe.M5), symbol, Timeframe.M5):
            assert not zone.is_observable_at(zone.formation_timestamp)


class TestRealDataFill:
    def test_fills_occur(self, detector, symbol):
        analysis = detector.analyse(load(symbol, Timeframe.M5), symbol, Timeframe.M5)
        assert analysis.fills, f"{symbol.value}: no FVG was ever touched"

    def test_both_partial_and_full_fills_occur(self, detector, symbol):
        analysis = detector.analyse(load(symbol, Timeframe.M1), symbol, Timeframe.M1)
        outcomes = {u.status_after for u in analysis.fills}
        assert FvgStatus.MITIGATED in outcomes
        assert FvgStatus.PARTIALLY_FILLED in outcomes

    def test_fill_percentages_are_bounded(self, detector, symbol, timeframe):
        for update in detector.analyse(load(symbol, timeframe), symbol, timeframe).fills:
            assert 0.0 < update.fill_percentage <= 1.0

    def test_deepest_price_is_a_real_bar_extreme(self, detector, symbol):
        frame = load(symbol, Timeframe.M5)
        rows = frame.set_index("timestamp")
        analysis = detector.analyse(frame, symbol, Timeframe.M5)

        for update in analysis.fills:
            zone = analysis.zone_by_id(update.zone_id)
            row = rows.loc[pd.Timestamp(update.event_timestamp)]
            column = "low" if zone.is_bullish else "high"
            assert update.deepest_price == pytest.approx(float(row[column]))

    def test_a_partially_filled_zone_remains_active(self, detector, symbol):
        analysis = detector.analyse(load(symbol, Timeframe.M5), symbol, Timeframe.M5)
        partial = [u for u in analysis.fills if u.status_after is FvgStatus.PARTIALLY_FILLED]
        if not partial:
            pytest.skip(f"{symbol.value}: no partial fills in this window")
        update = partial[0]
        active_ids = {z.zone_id for z in analysis.active_at(update.confirmation_timestamp)}
        assert update.zone_id in active_ids


class TestRealDataLeakage:
    def test_no_event_leaks(self, detector, symbol, timeframe):
        assert_no_leakage(detector.events(load(symbol, timeframe), symbol, timeframe))

    def test_batch_equals_prefix_replay(self, detector, symbol, timeframe):
        frame = load(symbol, timeframe)
        full = detector.analyse(frame, symbol, timeframe)
        zones = [z.as_dict() for z in full.zones]
        fills = [u.as_dict() for u in full.fills]

        step = max(len(frame) // 6, 1)
        for cut in range(step, len(frame) + 1, step):
            partial = detector.analyse(frame.iloc[:cut], symbol, timeframe)
            assert [z.as_dict() for z in partial.zones] == zones[: len(partial.zones)]
            assert [u.as_dict() for u in partial.fills] == fills[: len(partial.fills)]

    def test_observable_at_matches_visible_bars_only(self, detector, symbol):
        frame = load(symbol, Timeframe.M5)
        step = max(len(frame) // 5, 1)
        for cut in range(step, len(frame) + 1, step):
            visible = frame.iloc[:cut]
            as_of = visible["timestamp"].iloc[-1].to_pydatetime() + timedelta(minutes=5)

            from_full = detector.observable_at(frame, as_of, symbol, Timeframe.M5)
            from_visible = detector.analyse(visible, symbol, Timeframe.M5)
            assert [z.as_dict() for z in from_full.zones] == [z.as_dict() for z in from_visible.zones]


class TestRealDataWeekend:
    def test_no_fvg_spans_the_weekend_closure(self, detector, symbol):
        """The decisive real-data test of the contiguity guard: the Friday→Sunday
        price jump must not manufacture a phantom imbalance."""
        for zone in detector.detect(load(symbol, Timeframe.M5), symbol, Timeframe.M5):
            c1 = pd.Timestamp(zone.candle1_timestamp)
            c3 = pd.Timestamp(zone.candle3_timestamp)
            assert not (
                c1 <= FRIDAY_CLOSE <= c3
            ), f"{symbol.value}: a zone spans the weekend closure — phantom FVG"

    def test_disabling_contiguity_creates_phantom_zones_across_data_gaps(self, symbol):
        """Proves the guard is what suppresses them, rather than the data happening to
        be benign.

        Note what the real data actually shows: across *this* weekend EURUSD reopened
        within a few points of the Friday close, so that particular boundary leaves no
        gap either way. The phantoms appear at the several shorter intra-week data
        gaps instead — which is the more general and more useful claim.
        """
        frame = load(symbol, Timeframe.M5)
        guarded = FvgDetector(FvgConfig()).detect(frame, symbol, Timeframe.M5)
        naive = FvgDetector(FvgConfig(require_contiguous_bars=False)).detect(frame, symbol, Timeframe.M5)

        extra = {z.zone_id for z in naive} - {z.zone_id for z in guarded}
        assert extra, f"{symbol.value}: the contiguity guard suppressed nothing"

        step = pd.Timedelta(minutes=5)
        by_id = {z.zone_id: z for z in naive}
        for zone_id in extra:
            zone = by_id[zone_id]
            spans_a_gap = pd.Timestamp(zone.candle1_timestamp) + step != pd.Timestamp(
                zone.candle2_timestamp
            ) or pd.Timestamp(zone.candle2_timestamp) + step != pd.Timestamp(zone.candle3_timestamp)
            assert spans_a_gap, "a suppressed zone did not actually span a time gap"

    def test_no_zone_confirms_inside_the_closure(self, detector, symbol):
        for zone in detector.detect(load(symbol, Timeframe.M5), symbol, Timeframe.M5):
            confirmation = pd.Timestamp(zone.confirmation_timestamp)
            assert not (FRIDAY_CLOSE < confirmation < SUNDAY_REOPEN)

    def test_no_fill_confirms_inside_the_closure(self, detector, symbol):
        for update in detector.analyse(load(symbol, Timeframe.M5), symbol, Timeframe.M5).fills:
            confirmation = pd.Timestamp(update.confirmation_timestamp)
            assert not (FRIDAY_CLOSE < confirmation < SUNDAY_REOPEN)


class TestRealDataDst:
    def test_the_dst_transition_does_not_disturb_confirmation_timing(self, detector, symbol):
        """Bars are UTC and uniform, so 2024-03-10 must not change the one-bar lag."""
        zones = detector.detect(load(symbol, Timeframe.M5), symbol, Timeframe.M5)
        near = [
            z for z in zones if abs(pd.Timestamp(z.formation_timestamp) - US_DST) < pd.Timedelta(hours=12)
        ]
        for zone in near:
            assert zone.confirmation_timestamp - zone.formation_timestamp == timedelta(minutes=5)

    def test_zones_exist_on_both_sides_of_the_transition(self, detector, symbol):
        zones = detector.detect(load(symbol, Timeframe.M5), symbol, Timeframe.M5)
        before = [z for z in zones if pd.Timestamp(z.formation_timestamp) < US_DST]
        after = [z for z in zones if pd.Timestamp(z.formation_timestamp) > US_DST]
        assert before and after


class TestRealDataComposition:
    def test_composes_with_the_other_detectors(self, detector, symbol):
        from ict_kronos.ict import (
            LiquidityConfig,
            LiquidityDetector,
            StructureConfig,
            StructureDetector,
            SwingConfig,
        )

        frame = load(symbol, Timeframe.M5)
        swing = SwingConfig(left=3, right=3)
        combined = (
            detector.events(frame, symbol, Timeframe.M5)
            + StructureDetector(StructureConfig(), swing).events(frame, symbol, Timeframe.M5)
            + LiquidityDetector(LiquidityConfig(), swing).events(frame, symbol, Timeframe.M5)
        )
        assert_no_leakage(combined)

        as_of = datetime(2024, 3, 11, 12, 0, tzinfo=UTC)
        visible = filter_observable(combined, as_of)
        assert visible and len(visible) < len(combined)
        assert EventType.FVG_BULLISH in {e.event_type for e in combined}


class TestUnvalidatedTimeframes:
    """Records the dataset limit explicitly rather than leaving it implicit."""

    def test_daily_resamples_to_too_few_bars_in_this_window(self, symbol):
        store = ParquetCandleStore(DATA_ROOT)
        base = store.read(
            symbol, Timeframe.M1, start=pd.Timestamp(WINDOW_START), end=pd.Timestamp(WINDOW_END)
        )
        if len(base) == 0:
            pytest.skip("real data absent")
        daily = resample(base, Timeframe.M1, Timeframe.D1, symbol)
        assert len(daily) < 3, (
            "the four-day window now yields >=3 daily bars — 1D validation is newly "
            "possible and should be added"
        )

    def test_weekly_is_not_representable(self):
        """No W1 member exists in the Timeframe enum (R2-01's domain). Adding one to
        satisfy a vacuous test would be scope creep into an approved story."""
        assert "1w" not in {t.value for t in Timeframe}
