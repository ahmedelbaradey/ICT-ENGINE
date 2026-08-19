"""Market-data providers: the fixture backend, the factory, and Dukascopy decoding.

The Dukascopy tests exercise the PURE functions (URL construction, bi5 decoding,
tick aggregation) against synthetic payloads built in-test. No network is touched,
so these run in the default CI gate — which matters, because the parsing is where
all the correctness risk lives.
"""

from __future__ import annotations

import lzma
import struct
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from ict_kronos.app.config import MarketDataBackendKind, MarketDataConfig, Settings
from ict_kronos.data import FixtureProvider, MarketDataError, build_market_data_provider
from ict_kronos.data.dukascopy import (
    TICK_RECORD_BYTES,
    DukascopyProvider,
    PriceSide,
    aggregate_ticks_to_bars,
    bi5_url,
    decode_bi5,
    hours_in_window,
    ticks_to_frame,
)
from ict_kronos.domain import CANDLE_COLUMNS, Symbol, Timeframe

from .conftest import FIXTURE_BAR_COUNT, FIXTURE_END, FIXTURE_START

# ------------------------------------------------------------- FixtureProvider


class TestFixtureProvider:
    def test_name_and_support(self, fixture_root):
        provider = FixtureProvider(fixture_root)
        assert provider.name == "fixture"
        assert provider.supports(Symbol.EURUSD, Timeframe.M5)
        assert not provider.supports(Symbol.EURUSD, Timeframe.H4)

    def test_fetch_returns_canonical_schema(self, fixture_root):
        provider = FixtureProvider(fixture_root)
        frame = provider.fetch(Symbol.EURUSD, Timeframe.M5, FIXTURE_START, FIXTURE_END)
        assert list(frame.columns) == list(CANDLE_COLUMNS)
        assert len(frame) == FIXTURE_BAR_COUNT
        assert isinstance(frame["timestamp"].dtype, pd.DatetimeTZDtype)

    def test_symbol_and_timeframe_come_from_the_request_not_the_file(self, fixture_root):
        provider = FixtureProvider(fixture_root)
        frame = provider.fetch(Symbol.XAUUSD, Timeframe.M5, FIXTURE_START, FIXTURE_END)
        assert set(frame["symbol"].unique()) == {"XAUUSD"}
        assert set(frame["timeframe"].unique()) == {"5m"}

    def test_window_is_half_open(self, fixture_root):
        """[start, end) — so adjacent windows tile with no overlap and no gap."""
        provider = FixtureProvider(fixture_root)
        boundary = FIXTURE_START + timedelta(hours=12)

        first = provider.fetch(Symbol.EURUSD, Timeframe.M5, FIXTURE_START, boundary)
        second = provider.fetch(Symbol.EURUSD, Timeframe.M5, boundary, FIXTURE_END)

        assert len(first) + len(second) == FIXTURE_BAR_COUNT
        assert first["timestamp"].max() < boundary
        assert second["timestamp"].min() == pd.Timestamp(boundary)

    def test_empty_window_returns_empty_canonical_frame(self, fixture_root):
        provider = FixtureProvider(fixture_root)
        far_future = datetime(2030, 1, 1, tzinfo=UTC)
        frame = provider.fetch(Symbol.EURUSD, Timeframe.M5, far_future, far_future + timedelta(days=1))
        assert len(frame) == 0
        assert list(frame.columns) == list(CANDLE_COLUMNS)

    def test_missing_fixture_raises(self, fixture_root):
        provider = FixtureProvider(fixture_root)
        with pytest.raises(MarketDataError, match="no fixture"):
            provider.fetch(Symbol.EURUSD, Timeframe.H4, FIXTURE_START, FIXTURE_END)

    def test_naive_bounds_are_rejected(self, fixture_root):
        provider = FixtureProvider(fixture_root)
        with pytest.raises(ValueError, match="timezone-aware"):
            provider.fetch(
                Symbol.EURUSD,
                Timeframe.M5,
                datetime(2024, 3, 4),  # noqa: DTZ001 - deliberate
                FIXTURE_END,
            )


# --------------------------------------------------------------------- factory


class TestProviderFactory:
    def test_defaults_to_fixture(self, monkeypatch):
        monkeypatch.delenv("MARKET_DATA_BACKEND", raising=False)
        provider = build_market_data_provider(Settings.from_env())
        assert provider.name == "fixture"

    def test_selects_dukascopy_when_configured(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MARKET_DATA_BACKEND", "dukascopy")
        monkeypatch.setenv("DUKASCOPY_CACHE", str(tmp_path / "cache"))
        provider = build_market_data_provider(Settings.from_env())
        assert provider.name == "dukascopy"

    def test_unknown_backend_falls_back_to_fixture(self, monkeypatch):
        monkeypatch.setenv("MARKET_DATA_BACKEND", "not-a-backend")
        assert MarketDataConfig.from_env().backend is MarketDataBackendKind.FIXTURE

    def test_degrades_to_fixture_when_cache_is_unusable(self, tmp_path, monkeypatch):
        """A misconfigured live deploy must degrade with a warning, not crash."""
        blocker = tmp_path / "blocker"
        blocker.write_text("i am a file, not a directory", encoding="utf-8")
        monkeypatch.setenv("MARKET_DATA_BACKEND", "dukascopy")
        monkeypatch.setenv("DUKASCOPY_CACHE", str(blocker / "cache"))

        provider = build_market_data_provider(Settings.from_env())
        assert provider.name == "fixture"


# ------------------------------------------------------------------- Dukascopy


def _tick_bytes(ms: int, ask_points: int, bid_points: int, ask_vol: float, bid_vol: float) -> bytes:
    return struct.pack(">IIIff", ms, ask_points, bid_points, ask_vol, bid_vol)


def _bi5(records: list[bytes]) -> bytes:
    return lzma.compress(b"".join(records))


class TestBi5Url:
    def test_month_is_zero_indexed(self):
        """Dukascopy months are 0-indexed. Getting this wrong silently fetches the
        wrong month's data, which is far worse than a 404."""
        url = bi5_url(
            "https://datafeed.dukascopy.com/datafeed",
            Symbol.EURUSD,
            datetime(2024, 3, 4, 9, tzinfo=UTC),
        )
        assert url == "https://datafeed.dukascopy.com/datafeed/EURUSD/2024/02/04/09h_ticks.bi5"

    def test_january_maps_to_00(self):
        url = bi5_url("http://x", Symbol.XAUUSD, datetime(2023, 1, 31, 23, tzinfo=UTC))
        assert url == "http://x/XAUUSD/2023/00/31/23h_ticks.bi5"

    def test_trailing_slash_is_tolerated(self):
        url = bi5_url("http://x/", Symbol.EURUSD, datetime(2024, 6, 1, 0, tzinfo=UTC))
        assert url == "http://x/EURUSD/2024/05/01/00h_ticks.bi5"

    def test_naive_hour_is_rejected(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            bi5_url("http://x", Symbol.EURUSD, datetime(2024, 6, 1, 0))  # noqa: DTZ001


class TestDecodeBi5:
    def test_decodes_records_and_scales_prices(self):
        hour = datetime(2024, 3, 4, 9, tzinfo=UTC)
        payload = _bi5(
            [
                _tick_bytes(0, 108505, 108500, 1.5, 2.0),
                _tick_bytes(60_000, 108515, 108510, 1.0, 1.25),
            ]
        )
        result = decode_bi5(payload, Symbol.EURUSD, hour)

        assert result.record_count == 2
        assert result.truncated_bytes == 0
        assert len(result.frame) == 2
        # EURUSD has 5 decimals: 108500 points -> 1.08500
        assert result.frame["bid"].iloc[0] == pytest.approx(1.08500)
        assert result.frame["ask"].iloc[0] == pytest.approx(1.08505)
        assert result.frame["timestamp"].iloc[0] == pd.Timestamp(hour)
        assert result.frame["timestamp"].iloc[1] == pd.Timestamp(hour + timedelta(minutes=1))

    def test_scales_xauusd_by_its_own_precision(self):
        hour = datetime(2024, 3, 4, 9, tzinfo=UTC)
        payload = _bi5([_tick_bytes(0, 2082510, 2082500, 1.0, 1.0)])
        result = decode_bi5(payload, Symbol.XAUUSD, hour)
        # XAUUSD has 3 decimals: 2082500 points -> 2082.500
        assert result.frame["bid"].iloc[0] == pytest.approx(2082.500)

    def test_empty_payload_is_a_closed_market_not_an_error(self):
        """404/empty is normal for weekends and holidays. Conflating it with failure
        would make the gap report meaningless."""
        result = decode_bi5(b"", Symbol.EURUSD, datetime(2024, 3, 3, 5, tzinfo=UTC))
        assert result.record_count == 0
        assert len(result.frame) == 0

    def test_truncated_trailing_record_is_reported_not_hidden(self):
        hour = datetime(2024, 3, 4, 9, tzinfo=UTC)
        whole = _tick_bytes(0, 108505, 108500, 1.0, 1.0)
        payload = lzma.compress(whole + b"\x00\x00\x00")  # 3 stray bytes
        result = decode_bi5(payload, Symbol.EURUSD, hour)
        assert result.record_count == 1
        assert result.truncated_bytes == 3

    def test_corrupt_payload_raises(self):
        with pytest.raises(MarketDataError, match="corrupt bi5"):
            decode_bi5(b"not lzma at all", Symbol.EURUSD, datetime(2024, 3, 4, 9, tzinfo=UTC))

    def test_record_size_is_twenty_bytes(self):
        assert TICK_RECORD_BYTES == 20


class TestAggregateTicksToBars:
    def _ticks(self, offsets_and_bids: list[tuple[int, float]]) -> pd.DataFrame:
        base = datetime(2024, 3, 4, 9, tzinfo=UTC)
        return pd.DataFrame(
            {
                "timestamp": [pd.Timestamp(base + timedelta(seconds=s)) for s, _ in offsets_and_bids],
                "bid": [b for _, b in offsets_and_bids],
                "ask": [b + 0.0001 for _, b in offsets_and_bids],
                "bid_volume": [1.0] * len(offsets_and_bids),
                "ask_volume": [1.0] * len(offsets_and_bids),
            }
        )

    def test_ohlc_is_first_max_min_last(self):
        ticks = self._ticks([(0, 1.0850), (60, 1.0860), (120, 1.0840), (180, 1.0855)])
        bars = aggregate_ticks_to_bars(ticks, Symbol.EURUSD, Timeframe.M5)

        assert len(bars) == 1
        row = bars.iloc[0]
        assert row["open"] == pytest.approx(1.0850)
        assert row["high"] == pytest.approx(1.0860)
        assert row["low"] == pytest.approx(1.0840)
        assert row["close"] == pytest.approx(1.0855)
        assert row["volume"] == 4.0  # tick count

    def test_bars_are_labelled_by_open_time_and_left_closed(self):
        # A tick at exactly 09:05:00 belongs to the 09:05 bar, not the 09:00 bar.
        ticks = self._ticks([(0, 1.0850), (299, 1.0851), (300, 1.0900)])
        bars = aggregate_ticks_to_bars(ticks, Symbol.EURUSD, Timeframe.M5)

        assert len(bars) == 2
        assert bars["timestamp"].iloc[0] == pd.Timestamp("2024-03-04T09:00:00Z")
        assert bars["timestamp"].iloc[1] == pd.Timestamp("2024-03-04T09:05:00Z")
        assert bars["volume"].iloc[0] == 2.0
        assert bars["open"].iloc[1] == pytest.approx(1.0900)

    def test_empty_periods_produce_no_bar(self):
        """A synthetic flat bar would be fabricated price action that every ICT
        detector downstream would read as real structure."""
        ticks = self._ticks([(0, 1.0850), (900, 1.0860)])  # 09:00 and 09:15, nothing between
        bars = aggregate_ticks_to_bars(ticks, Symbol.EURUSD, Timeframe.M5)

        assert len(bars) == 2
        assert list(bars["timestamp"]) == [
            pd.Timestamp("2024-03-04T09:00:00Z"),
            pd.Timestamp("2024-03-04T09:15:00Z"),
        ]

    def test_side_selection_changes_the_prices(self):
        ticks = self._ticks([(0, 1.0850), (60, 1.0860)])
        bid_bars = aggregate_ticks_to_bars(ticks, Symbol.EURUSD, Timeframe.M5, side=PriceSide.BID)
        ask_bars = aggregate_ticks_to_bars(ticks, Symbol.EURUSD, Timeframe.M5, side=PriceSide.ASK)
        mid_bars = aggregate_ticks_to_bars(ticks, Symbol.EURUSD, Timeframe.M5, side=PriceSide.MID)

        assert ask_bars["open"].iloc[0] > bid_bars["open"].iloc[0]
        assert mid_bars["open"].iloc[0] == pytest.approx(
            (bid_bars["open"].iloc[0] + ask_bars["open"].iloc[0]) / 2
        )

    def test_empty_ticks_return_canonical_empty_frame(self):
        empty = pd.DataFrame(
            {
                "timestamp": pd.Series(dtype="datetime64[ns, UTC]"),
                "bid": pd.Series(dtype="float64"),
                "ask": pd.Series(dtype="float64"),
            }
        )
        bars = aggregate_ticks_to_bars(empty, Symbol.EURUSD, Timeframe.M5)
        assert len(bars) == 0
        assert list(bars.columns) == list(CANDLE_COLUMNS)

    def test_output_satisfies_ohlc_invariants(self):
        ticks = self._ticks([(s, 1.0850 + (s % 7) * 0.0001) for s in range(0, 600, 10)])
        bars = aggregate_ticks_to_bars(ticks, Symbol.EURUSD, Timeframe.M5)
        assert (bars["high"] >= bars[["open", "close"]].max(axis=1)).all()
        assert (bars["low"] <= bars[["open", "close"]].min(axis=1)).all()


class TestHoursInWindow:
    def test_enumerates_half_open_window(self):
        hours = hours_in_window(
            datetime(2024, 3, 4, 9, 0, tzinfo=UTC), datetime(2024, 3, 4, 12, 0, tzinfo=UTC)
        )
        assert hours == [
            datetime(2024, 3, 4, 9, tzinfo=UTC),
            datetime(2024, 3, 4, 10, tzinfo=UTC),
            datetime(2024, 3, 4, 11, tzinfo=UTC),
        ]

    def test_partial_start_hour_is_included(self):
        hours = hours_in_window(
            datetime(2024, 3, 4, 9, 30, tzinfo=UTC), datetime(2024, 3, 4, 10, 15, tzinfo=UTC)
        )
        assert hours == [
            datetime(2024, 3, 4, 9, tzinfo=UTC),
            datetime(2024, 3, 4, 10, tzinfo=UTC),
        ]

    def test_empty_window(self):
        t = datetime(2024, 3, 4, 9, tzinfo=UTC)
        assert hours_in_window(t, t) == []


class TestDukascopyProviderCache:
    def test_cache_path_uses_real_month_not_zero_indexed(self, tmp_path):
        """The URL is 0-indexed; the local cache is human-readable 1-indexed. Mixing
        the two up would make cached files impossible to find by hand."""
        provider = DukascopyProvider("http://x", tmp_path)
        path = provider._cache_path(Symbol.EURUSD, datetime(2024, 3, 4, 9, tzinfo=UTC))
        assert path == tmp_path / "EURUSD" / "2024" / "03" / "04" / "09h_ticks.bi5"

    def test_cached_payload_is_reused_without_network(self, tmp_path):
        provider = DukascopyProvider("http://unreachable.invalid", tmp_path)
        hour = datetime(2024, 3, 4, 9, tzinfo=UTC)
        cache_path = provider._cache_path(Symbol.EURUSD, hour)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = _bi5([_tick_bytes(0, 108505, 108500, 1.0, 1.0)])
        cache_path.write_bytes(payload)

        # If this touched the network it would fail: the host does not resolve.
        assert provider._payload_for(Symbol.EURUSD, hour) == payload

    def test_fetch_from_cache_produces_bars(self, tmp_path):
        provider = DukascopyProvider("http://unreachable.invalid", tmp_path)
        hour = datetime(2024, 3, 4, 9, tzinfo=UTC)
        cache_path = provider._cache_path(Symbol.EURUSD, hour)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(
            _bi5(
                [
                    _tick_bytes(0, 108505, 108500, 1.0, 1.0),
                    _tick_bytes(120_000, 108615, 108610, 1.0, 1.0),
                    _tick_bytes(299_000, 108405, 108400, 1.0, 1.0),
                ]
            )
        )

        bars = provider.fetch(Symbol.EURUSD, Timeframe.M5, hour, hour + timedelta(minutes=5))
        assert len(bars) == 1
        assert bars["open"].iloc[0] == pytest.approx(1.08500)
        assert bars["high"].iloc[0] == pytest.approx(1.08610)
        assert bars["low"].iloc[0] == pytest.approx(1.08400)
        assert bars["close"].iloc[0] == pytest.approx(1.08400)


class TestTicksToFrame:
    def test_concatenates_and_sorts(self):
        h1 = datetime(2024, 3, 4, 9, tzinfo=UTC)
        h2 = datetime(2024, 3, 4, 10, tzinfo=UTC)
        later = decode_bi5(_bi5([_tick_bytes(0, 2, 1, 1.0, 1.0)]), Symbol.EURUSD, h2)
        earlier = decode_bi5(_bi5([_tick_bytes(0, 2, 1, 1.0, 1.0)]), Symbol.EURUSD, h1)

        combined = ticks_to_frame([later, earlier])  # deliberately out of order
        assert list(combined["timestamp"]) == [pd.Timestamp(h1), pd.Timestamp(h2)]

    def test_all_empty_returns_empty(self):
        empty = decode_bi5(b"", Symbol.EURUSD, datetime(2024, 3, 3, 5, tzinfo=UTC))
        assert len(ticks_to_frame([empty, empty])) == 0
