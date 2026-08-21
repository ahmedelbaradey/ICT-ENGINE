"""Native 1H / 1D ingestion and the one permitted production aggregation, 1H → 4H.

The production data architecture, and what each test is guarding:

.. code-block:: text

    Provider native 1H ──► Production 1H
                       └──► Production 4H   exactly four native 1H bars
    Provider native 1D ──► Production 1D

    ticks / 1M / 5M / 15M  ► never, at any point

Two facts about the provider make this delicate, and both are pinned below:

* **Dukascopy months are ZERO-BASED in the URL.** July is ``06``. Getting that wrong
  silently fetches the previous month and every downstream number is plausible.
* **The provider pads closed periods with flat zero-volume candles** carrying the prior
  close forward. Consuming them would feed forward-filled prices to the feature
  pipeline. They are identified, dropped and counted — never consumed, never repaired.
"""

from __future__ import annotations

import lzma
import struct
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from ict_kronos.data.coverage import GapCause, SessionProfile
from ict_kronos.data.dukascopy_candles import (
    KNOWN_ABSENT_NATIVE_FILES,
    NATIVE_CANDLE_FILES,
    NativeCandleError,
    decode_candles,
    is_provider_padding,
    months_in_window,
    native_candle_url,
)
from ict_kronos.data.production_ingest import (
    DERIVED_PRODUCTION_TIMEFRAME,
    HOURS_PER_H4,
    NATIVE_PRODUCTION_TIMEFRAMES,
    H4Disposition,
    build_h4_from_native_h1,
)
from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame

BASE = "https://datafeed.dukascopy.com/datafeed"
SYM = Symbol.EURUSD
POINT = SYM.spec.point_value


def encode(records, symbol=SYM, month=datetime(2026, 7, 1, tzinfo=UTC)):
    """Build a native-candle payload from ``(offset_seconds, o, h, l, c, volume)`` tuples."""
    point = symbol.spec.point_value
    raw = b"".join(
        struct.pack(
            ">iiiiif",
            offset,
            round(o / point),
            round(c / point),
            round(low / point),
            round(high / point),
            volume,
        )
        for offset, o, high, low, c, volume in records
    )
    return lzma.compress(raw, format=lzma.FORMAT_ALONE)


class TestTheProviderContract:
    def test_only_1h_and_1d_are_published_natively(self):
        assert set(NATIVE_CANDLE_FILES) == {Timeframe.H1, Timeframe.D1}

    def test_months_are_zero_based_in_the_url(self):
        """July is 06. This is the single easiest thing to get silently wrong."""
        url = native_candle_url(BASE, SYM, Timeframe.H1, datetime(2026, 7, 15, tzinfo=UTC))
        assert url.endswith("/EURUSD/2026/06/BID_candles_hour_1.bi5")

        january = native_candle_url(BASE, SYM, Timeframe.D1, datetime(2026, 1, 3, tzinfo=UTC))
        assert january.endswith("/EURUSD/2026/00/BID_candles_day_1.bi5")

    def test_asking_for_a_non_native_timeframe_is_refused(self):
        """4H has no native file. Requesting one must fail loudly, not fetch something else."""
        for timeframe in (Timeframe.H4, Timeframe.M1, Timeframe.M5, Timeframe.M15):
            with pytest.raises(NativeCandleError, match="not published natively"):
                native_candle_url(BASE, SYM, timeframe, datetime(2026, 7, 1, tzinfo=UTC))

    def test_the_absent_native_files_are_recorded_as_evidence(self):
        """Probed against the live feed; all 404. Recorded so it is evidence, not folklore."""
        assert "BID_candles_hour_4.bi5" in KNOWN_ABSENT_NATIVE_FILES
        assert "BID_candles_min_240.bi5" in KNOWN_ABSENT_NATIVE_FILES

    def test_months_in_window_covers_every_calendar_month(self):
        months = months_in_window(datetime(2026, 2, 1, tzinfo=UTC), datetime(2026, 8, 1, tzinfo=UTC))
        assert [f"{m:%Y-%m}" for m in months] == [
            "2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07",
        ]  # fmt: skip

    def test_a_year_boundary_is_crossed_correctly(self):
        months = months_in_window(datetime(2025, 12, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC))
        assert [f"{m:%Y-%m}" for m in months] == ["2025-12", "2026-01"]


class TestProviderPaddingIsRemoved:
    """The provider's forward-fill is the one thing that must never reach a feature."""

    def test_a_flat_zero_volume_candle_is_padding(self):
        assert is_provider_padding(1.14337, 1.14337, 1.14337, 1.14337, 0.0)

    def test_a_flat_candle_WITH_volume_is_real_and_kept(self):
        """A market can trade at one price. That is unusual, not fabricated."""
        assert not is_provider_padding(1.14337, 1.14337, 1.14337, 1.14337, 12.0)

    def test_a_zero_volume_candle_that_MOVED_is_kept(self):
        """Both conditions are required — a moving zero-volume bar is worth investigating."""
        assert not is_provider_padding(1.1430, 1.1440, 1.1420, 1.1435, 0.0)

    def test_padding_is_dropped_and_counted(self):
        payload = encode(
            [
                (0, 1.1400, 1.1410, 1.1390, 1.1405, 500.0),
                (3600, 1.1405, 1.1405, 1.1405, 1.1405, 0.0),  # padding
                (7200, 1.1405, 1.1405, 1.1405, 1.1405, 0.0),  # padding
                (10800, 1.1405, 1.1420, 1.1400, 1.1415, 640.0),
            ]
        )
        result = decode_candles(payload, SYM, Timeframe.H1, datetime(2026, 7, 1, tzinfo=UTC))
        assert result.record_count == 4
        assert result.padding_dropped == 2
        assert len(result.frame) == 2
        assert list(result.frame["timestamp"].dt.hour) == [0, 3]

    def test_dropping_padding_restores_absence_rather_than_creating_it(self):
        """The market really was shut; the gap is the truth and the flat bar was not."""
        payload = encode([(3600 * i, 1.14, 1.14, 1.14, 1.14, 0.0) for i in range(24)])
        result = decode_candles(payload, SYM, Timeframe.H1, datetime(2026, 7, 1, tzinfo=UTC))
        assert result.padding_dropped == 24
        assert len(result.frame) == 0

    def test_prices_are_decoded_from_integer_points(self):
        payload = encode([(0, 1.14128, 1.14139, 1.13617, 1.13774, 126671.0)])
        row = decode_candles(payload, SYM, Timeframe.H1, datetime(2026, 7, 1, tzinfo=UTC)).frame.iloc[0]
        assert row["open"] == pytest.approx(1.14128)
        assert row["high"] == pytest.approx(1.14139)
        assert row["low"] == pytest.approx(1.13617)
        assert row["close"] == pytest.approx(1.13774)
        assert row["volume"] == pytest.approx(126671.0)

    def test_offsets_are_seconds_from_the_month_start(self):
        payload = encode([(0, 1.14, 1.14, 1.14, 1.14, 1.0), (86400, 1.15, 1.15, 1.15, 1.15, 1.0)])
        frame = decode_candles(payload, SYM, Timeframe.D1, datetime(2026, 7, 1, tzinfo=UTC)).frame
        assert list(frame["timestamp"]) == [
            pd.Timestamp("2026-07-01", tz="UTC"),
            pd.Timestamp("2026-07-02", tz="UTC"),
        ]

    def test_an_empty_payload_is_absence_not_failure(self):
        result = decode_candles(b"", SYM, Timeframe.H1, datetime(2026, 7, 1, tzinfo=UTC))
        assert len(result.frame) == 0
        assert result.record_count == 0

    def test_a_corrupt_payload_raises_rather_than_returning_nothing(self):
        """Silent emptiness would be indistinguishable from a closed market."""
        with pytest.raises(NativeCandleError, match="not valid LZMA"):
            decode_candles(b"not-lzma", SYM, Timeframe.H1, datetime(2026, 7, 1, tzinfo=UTC))


def hours(count, *, start=datetime(2026, 6, 1, tzinfo=UTC), skip=(), symbol=SYM):
    """``count`` consecutive native 1H bars, omitting the offsets in ``skip``."""
    omitted = set(skip)
    return candles_to_frame(
        [
            MarketCandle(
                timestamp=start + timedelta(hours=i),
                symbol=symbol,
                timeframe=Timeframe.H1,
                open=1.1000 + 0.0010 * i,
                high=1.1000 + 0.0010 * i + 0.0006,
                low=1.1000 + 0.0010 * i - 0.0004,
                close=1.1000 + 0.0010 * i + 0.0002,
                volume=100.0 + i,
            )
            for i in range(count)
            if i not in omitted
        ]
    )


class TestFourHourAggregation:
    """§12 — prove the relationship, do not assume it."""

    def test_the_derived_timeframe_is_4h_built_from_four_hours(self):
        assert DERIVED_PRODUCTION_TIMEFRAME is Timeframe.H4
        assert HOURS_PER_H4 == 4
        assert Timeframe.H4 not in NATIVE_PRODUCTION_TIMEFRAMES

    def test_ohlc_comes_from_exactly_the_four_source_hours(self):
        source = hours(8)
        built = build_h4_from_native_h1(source, SYM)
        assert len(built.frame) == 2

        for index, bar in built.frame.iterrows():
            window = source[
                (source["timestamp"] >= bar["timestamp"])
                & (source["timestamp"] < bar["timestamp"] + Timeframe.H4.duration)
            ]
            assert len(window) == HOURS_PER_H4
            assert bar["open"] == pytest.approx(window["open"].iloc[0]), f"row {index}"
            assert bar["high"] == pytest.approx(window["high"].max())
            assert bar["low"] == pytest.approx(window["low"].min())
            assert bar["close"] == pytest.approx(window["close"].iloc[-1])
            assert bar["volume"] == pytest.approx(window["volume"].sum())

    def test_the_bar_is_stamped_with_its_OPEN_time(self):
        built = build_h4_from_native_h1(hours(8), SYM)
        assert list(built.frame["timestamp"]) == [
            pd.Timestamp("2026-06-01 00:00", tz="UTC"),
            pd.Timestamp("2026-06-01 04:00", tz="UTC"),
        ]

    def test_windows_align_to_the_4h_grid(self):
        built = build_h4_from_native_h1(hours(24), SYM)
        assert all(ts.hour % 4 == 0 for ts in built.frame["timestamp"])

    def test_every_emitted_window_records_its_four_source_hours(self):
        built = build_h4_from_native_h1(hours(8), SYM)
        for window in built.emitted():
            assert len(window.present_hours) == HOURS_PER_H4
            assert window.missing_hours == ()
            assert window.cause is GapCause.NONE

    def test_the_result_is_marked_as_derived_from_native_1h(self):
        built = build_h4_from_native_h1(hours(8), SYM)
        assert set(built.frame["timeframe"]) == {"4h"}
        assert set(built.frame["symbol"]) == {SYM.value}


class TestIncompleteFourHourWindows:
    """§7 — never compress a partial window into a shorter candle."""

    def test_a_window_missing_an_unexplained_hour_is_WITHHELD(self):
        source = hours(8, skip=(5,))
        built = build_h4_from_native_h1(source, SYM)
        assert len(built.frame) == 1
        withheld = built.withheld()
        assert len(withheld) == 1
        assert withheld[0].timestamp == datetime(2026, 6, 1, 4, tzinfo=UTC)
        assert withheld[0].cause is GapCause.UNDETERMINED
        assert "no proven cause" in withheld[0].reason

    def test_three_hours_are_never_relabelled_as_four(self):
        """The failure this exists to prevent: a 3-hour candle wearing a 4H name."""
        built = build_h4_from_native_h1(hours(8, skip=(5,)), SYM)
        for bar in built.frame.itertuples():
            window = [w for w in built.emitted() if w.timestamp == bar.timestamp.to_pydatetime()]
            assert window and len(window[0].present_hours) == HOURS_PER_H4

    def test_a_PROVEN_closure_explains_the_absence_but_still_withholds(self):
        """Proving the market was shut does not restore the missing hour.

        A 20:00 window whose 21:00 hour was provably closed still holds only three
        traded hours. Emitting it as ``4h`` would be exactly the silent compression
        this builder refuses — so the cause is recorded and the window is withheld
        under its own disposition, distinct from an unexplained absence.
        """
        closed = frozenset({(weekday, 21 * 60) for weekday in range(7)})
        profile = SessionProfile(
            symbol=SYM.value, closed_slots=closed, weekday_occurrences=dict.fromkeys(range(7), 5)
        )
        source = hours(28, skip=(21,))
        built = build_h4_from_native_h1(source, SYM, profile=profile)
        window = next(w for w in built.windows if w.timestamp.hour == 20)
        assert window.disposition is H4Disposition.WITHHELD_MARKET_CLOSED
        assert window.emitted is False
        assert window.cause is GapCause.MARKET_CLOSED
        assert "a 4H bar needs four" in window.reason

    def test_a_proven_closure_is_distinguishable_from_an_unexplained_absence(self):
        """Both are withheld; conflating WHY would throw away the only useful signal."""
        source = hours(28, skip=(21,))
        closed = frozenset({(weekday, 21 * 60) for weekday in range(7)})
        proven = build_h4_from_native_h1(
            source,
            SYM,
            profile=SessionProfile(
                symbol=SYM.value, closed_slots=closed, weekday_occurrences=dict.fromkeys(range(7), 5)
            ),
        )
        unproven = build_h4_from_native_h1(source, SYM)
        at_20 = lambda built: next(w for w in built.windows if w.timestamp.hour == 20)  # noqa: E731
        assert at_20(proven).disposition is H4Disposition.WITHHELD_MARKET_CLOSED
        assert at_20(unproven).disposition is H4Disposition.WITHHELD_UNDETERMINED
        assert at_20(proven).emitted is at_20(unproven).emitted is False

    def test_an_unproven_absence_is_never_called_a_closure(self):
        """Never silently treat an outage as a market closure."""
        source = hours(28, skip=(21,))
        built = build_h4_from_native_h1(source, SYM)  # no profile proves anything
        window = next(w for w in built.windows if w.timestamp.hour == 20)
        assert window.disposition is H4Disposition.WITHHELD_UNDETERMINED
        assert window.cause is GapCause.UNDETERMINED

    def test_a_boundary_truncated_window_is_withheld(self):
        source = hours(6, start=datetime(2026, 6, 1, 2, tzinfo=UTC))
        built = build_h4_from_native_h1(source, SYM)
        first = built.windows[0]
        assert first.disposition is H4Disposition.WITHHELD_BOUNDARY
        assert first.emitted is False

    def test_nothing_is_fabricated_to_fill_a_withheld_window(self):
        source = hours(8, skip=(5,))
        built = build_h4_from_native_h1(source, SYM)
        assert pd.Timestamp("2026-06-01 04:00", tz="UTC") not in set(built.frame["timestamp"])

    def test_the_account_of_every_window_is_complete(self):
        built = build_h4_from_native_h1(hours(24, skip=(5, 13)), SYM)
        counts = built.counts()
        assert counts["windows"] == len(built.windows)
        buckets = sum(counts[d.value] for d in H4Disposition)
        assert buckets == counts["windows"], "every window lands in exactly one bucket"
        assert counts["emitted"] == len(built.emitted())

    def test_an_empty_source_produces_nothing_rather_than_erroring(self):
        built = build_h4_from_native_h1(candles_to_frame([]), SYM)
        assert len(built.frame) == 0 and built.windows == ()


class TestNoLowerTimeframeAnywhere:
    """§13 / §22 — the production path may not reach for ticks or minutes."""

    @staticmethod
    def _code(path):
        from tests.test_market_state import _code_of

        return _code_of(path)

    def test_the_production_ingest_never_mentions_a_minute_timeframe(self):
        code = self._code("ict_kronos/data/production_ingest.py")
        for banned in ("Timeframe.M1", "Timeframe.M5", "Timeframe.M15", "TickBackfill", "decode_bi5"):
            assert banned not in code, f"production_ingest.py reaches for lower data: {banned!r}"

    def test_the_native_candle_client_never_fetches_ticks(self):
        code = self._code("ict_kronos/data/dukascopy_candles.py")
        for banned in ("ticks.bi5", "bi5_url", "Timeframe.M1", "Timeframe.M5"):
            assert banned not in code, f"dukascopy_candles.py reaches for ticks: {banned!r}"

    def test_the_production_path_never_forward_fills(self):
        for path in (
            "ict_kronos/data/production_ingest.py",
            "ict_kronos/data/dukascopy_candles.py",
        ):
            code = self._code(path)
            for banned in ("ffill", "bfill", "fillna", "interpolate", "pad("):
                assert banned not in code, f"{path} repairs data: {banned!r}"

    def test_the_guards_actually_read_code(self):
        code = self._code("ict_kronos/data/production_ingest.py")
        assert "def build_h4_from_native_h1(" in code
        assert '"""' not in code
