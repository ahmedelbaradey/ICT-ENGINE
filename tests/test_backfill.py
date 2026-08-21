"""TickBackfill and the real-data pipeline — exercised entirely from a pre-seeded
cache, so these run offline in the default CI gate.

The cache is the raw immutable archive, so seeding it with known bytes is not a mock:
it is the same code path a resumed backfill takes.
"""

from __future__ import annotations

import hashlib
import lzma
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from ict_kronos.app.config import Settings
from ict_kronos.data.backfill import RawArchiveStats, TickBackfill, _day_slices, _rollup_raw
from ict_kronos.data.realdata import DERIVED_TIMEFRAMES, RealDataPipeline
from ict_kronos.domain import CANDLE_COLUMNS, Symbol, Timeframe
from ict_kronos.storage import ManifestStore, ParquetCandleStore


def tick_bytes(ms: int, ask_points: int, bid_points: int) -> bytes:
    return struct.pack(">IIIff", ms, ask_points, bid_points, 1.0, 1.0)


def seed_hour(cache: Path, symbol: Symbol, hour: datetime, ticks_per_hour: int = 60) -> None:
    """Write a synthetic .bi5 into the cache for one hour."""
    records = []
    for i in range(ticks_per_hour):
        ms = i * (3_600_000 // ticks_per_hour)
        bid = 108500 + (i % 17)
        records.append(tick_bytes(ms, bid + 10, bid))
    payload = lzma.compress(b"".join(records))

    path = (
        cache
        / symbol.dukascopy_code
        / f"{hour.year:04d}"
        / f"{hour.month:02d}"
        / f"{hour.day:02d}"
        / f"{hour.hour:02d}h_ticks.bi5"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def seed_day(cache: Path, symbol: Symbol, day: datetime, hours: int = 24) -> None:
    for h in range(hours):
        seed_hour(cache, symbol, day + timedelta(hours=h))


DAY = datetime(2024, 3, 8, tzinfo=UTC)


class TestDaySlices:
    def test_splits_on_utc_midnight(self):
        slices = _day_slices(DAY, DAY + timedelta(days=3))
        assert len(slices) == 3
        assert slices[0] == (DAY, DAY + timedelta(days=1))
        assert slices[-1][1] == DAY + timedelta(days=3)

    def test_partial_first_day_is_preserved(self):
        start = DAY + timedelta(hours=9)
        slices = _day_slices(start, DAY + timedelta(days=1, hours=3))
        assert slices[0] == (start, DAY + timedelta(days=1))
        assert slices[1] == (DAY + timedelta(days=1), DAY + timedelta(days=1, hours=3))

    def test_empty_window(self):
        assert _day_slices(DAY, DAY) == []

    def test_slices_tile_without_overlap(self):
        slices = _day_slices(DAY, DAY + timedelta(days=5))
        for earlier, later in zip(slices[:-1], slices[1:], strict=True):
            assert earlier[1] == later[0]


class TestRawRollup:
    def test_empty_archive_has_the_empty_digest(self):
        stats = _rollup_raw([])
        assert stats.file_count == 0
        assert stats.digest == hashlib.sha256(b"").hexdigest()

    def test_total_bytes_is_summed(self):
        entries = [f"a/1.bi5:{'0' * 64}:100", f"a/2.bi5:{'1' * 64}:250"]
        stats = _rollup_raw(entries)
        assert stats.file_count == 2
        assert stats.total_bytes == 350

    def test_zero_byte_files_are_counted_as_empty(self):
        entries = [f"a/1.bi5:{'0' * 64}:0", f"a/2.bi5:{'1' * 64}:5"]
        assert _rollup_raw(entries).empty_files == 1

    def test_digest_is_order_independent(self):
        a = [f"a/1.bi5:{'0' * 64}:1", f"a/2.bi5:{'1' * 64}:2"]
        assert _rollup_raw(a).digest == _rollup_raw(list(reversed(a))).digest

    def test_digest_changes_when_size_changes(self):
        """A truncated re-download must not masquerade as intact."""
        base = [f"a/1.bi5:{'0' * 64}:100"]
        truncated = [f"a/1.bi5:{'0' * 64}:50"]
        assert _rollup_raw(base).digest != _rollup_raw(truncated).digest

    def test_digest_changes_when_content_changes(self):
        a = [f"a/1.bi5:{'0' * 64}:100"]
        b = [f"a/1.bi5:{'f' * 64}:100"]
        assert _rollup_raw(a).digest != _rollup_raw(b).digest

    def test_as_dict(self):
        payload = RawArchiveStats(file_count=2, total_bytes=5, empty_files=1, digest="x").as_dict()
        assert payload == {"file_count": 2, "total_bytes": 5, "empty_files": 1, "digest": "x"}


class TestBackfillFromCache:
    @pytest.fixture
    def cache(self, tmp_path) -> Path:
        cache = tmp_path / "cache"
        seed_day(cache, Symbol.EURUSD, DAY)
        return cache

    def test_produces_one_minute_bars(self, cache):
        result = TickBackfill(cache).run(Symbol.EURUSD, DAY, DAY + timedelta(days=1))

        assert result.succeeded
        assert len(result.bars_1m) == 1440
        assert set(result.bars_1m["timeframe"].unique()) == {"1m"}
        assert list(result.bars_1m.columns) == list(CANDLE_COLUMNS)

    def test_no_network_is_touched_when_cache_is_warm(self, cache):
        """An unreachable base URL must not matter — everything is cached."""
        backfill = TickBackfill(cache, base_url="http://unreachable.invalid/datafeed")
        result = backfill.run(Symbol.EURUSD, DAY, DAY + timedelta(days=1))
        assert result.succeeded
        assert len(result.bars_1m) == 1440

    def test_tick_accounting(self, cache):
        result = TickBackfill(cache).run(Symbol.EURUSD, DAY, DAY + timedelta(days=1))

        assert result.tick_quality.input_ticks == 24 * 60
        assert result.tick_quality.output_ticks == 24 * 60
        assert result.tick_quality.rejected == 0
        assert result.tick_quality.hours_requested == 24
        assert result.tick_quality.hours_empty == 0

    def test_raw_stats_are_recorded(self, cache):
        result = TickBackfill(cache).run(Symbol.EURUSD, DAY, DAY + timedelta(days=1))

        assert result.raw.file_count == 24
        assert result.raw.total_bytes > 0
        assert len(result.raw.digest) == 64

    def test_missing_hour_is_a_download_failure_not_a_crash(self, cache, tmp_path):
        """An hour absent from the cache with no network must be recorded, and the
        remaining 23 hours must still produce bars."""
        target = cache / "EURUSD" / "2024" / "03" / "08" / "05h_ticks.bi5"
        target.unlink()

        backfill = TickBackfill(
            cache, base_url="http://unreachable.invalid/datafeed", max_retries=0, backoff_seconds=0.0
        )
        result = backfill.run(Symbol.EURUSD, DAY, DAY + timedelta(days=1))

        assert not result.succeeded
        assert len(result.download_failures) == 1
        assert len(result.bars_1m) == 23 * 60

    def test_empty_hour_file_is_a_closed_market(self, cache):
        """A zero-byte payload is a closed market, not a failure."""
        target = cache / "EURUSD" / "2024" / "03" / "08" / "05h_ticks.bi5"
        target.write_bytes(b"")

        result = TickBackfill(cache).run(Symbol.EURUSD, DAY, DAY + timedelta(days=1))
        assert result.succeeded
        assert result.tick_quality.hours_empty == 1
        assert len(result.bars_1m) == 23 * 60

    def test_multi_day_batching_does_not_change_the_result(self, cache):
        """Day-sized batches must be a pure implementation detail."""
        seed_day(cache, Symbol.EURUSD, DAY + timedelta(days=1))
        backfill = TickBackfill(cache)

        two_days = backfill.run(Symbol.EURUSD, DAY, DAY + timedelta(days=2))
        first = backfill.run(Symbol.EURUSD, DAY, DAY + timedelta(days=1))
        second = backfill.run(Symbol.EURUSD, DAY + timedelta(days=1), DAY + timedelta(days=2))

        stitched = pd.concat([first.bars_1m, second.bars_1m], ignore_index=True)
        pd.testing.assert_frame_equal(two_days.bars_1m, stitched, check_dtype=False)


class TestRealDataPipeline:
    @pytest.fixture
    def settings(self, tmp_path, monkeypatch) -> Settings:
        monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
        monkeypatch.setenv("DUKASCOPY_CACHE", str(tmp_path / "cache"))
        return Settings.from_env()

    @pytest.fixture
    def seeded(self, settings) -> Settings:
        for symbol in (Symbol.EURUSD, Symbol.XAUUSD):
            seed_day(settings.market_data.download_cache, symbol, DAY)
        return settings

    def test_builds_the_full_timeframe_stack(self, seeded):
        result = RealDataPipeline(seeded).run(
            [Symbol.EURUSD, Symbol.XAUUSD], DAY, DAY + timedelta(days=1), "v1"
        )

        assert result.succeeded, result.failures
        produced = {(s.symbol, s.timeframe) for s in result.series}
        expected = {
            (sym.value, tf.value)
            for sym in (Symbol.EURUSD, Symbol.XAUUSD)
            for tf in (Timeframe.M1, *DERIVED_TIMEFRAMES)
        }
        assert produced == expected

    def test_derived_series_record_their_source(self, seeded):
        result = RealDataPipeline(seeded).run([Symbol.EURUSD], DAY, DAY + timedelta(days=1), "v1")

        by_tf = {s.timeframe: s for s in result.series}
        assert by_tf["1m"].derived_from is None
        for tf in DERIVED_TIMEFRAMES:
            assert by_tf[tf.value].derived_from == "1m"

    def test_bar_counts_are_consistent(self, seeded):
        result = RealDataPipeline(seeded).run([Symbol.EURUSD], DAY, DAY + timedelta(days=1), "v1")

        by_tf = {s.timeframe: s.rows for s in result.series}
        # H4 and D1 joined DERIVED_TIMEFRAMES when the production universe was fixed to
        # 1H/4H/1D, so a full UTC day now materialises six series rather than four.
        assert by_tf == {"1m": 1440, "5m": 288, "15m": 96, "1h": 24, "4h": 6, "1d": 1}

    def test_manifest_carries_required_provenance(self, seeded):
        RealDataPipeline(seeded).run([Symbol.EURUSD], DAY, DAY + timedelta(days=1), "v1")

        manifest = ManifestStore(seeded.storage.manifest_root).read("v1")
        assert manifest.provider == "dukascopy"
        assert manifest.pipeline_version
        # One provenance entry per materialised series: 1m + 5m/15m/1h/4h/1d.
        assert len(manifest.datasets) == 6

        entry = manifest.datasets[0]
        for key in (
            "symbol",
            "source",
            "bar_timeframe",
            "download_period",
            "timezone",
            "raw_file_hash",
            "normalized_file_hash",
            "row_count",
            "first_timestamp",
            "last_timestamp",
            "creation_timestamp",
            "pipeline_version",
            "git_commit",
        ):
            assert key in entry, key
        assert entry["timezone"] == "UTC"

    def test_manifest_verifies_against_written_parquet(self, seeded):
        RealDataPipeline(seeded).run([Symbol.EURUSD], DAY, DAY + timedelta(days=1), "v1")
        assert ManifestStore(seeded.storage.manifest_root).verify("v1") == []

    def test_stored_bars_are_readable_and_canonical(self, seeded):
        RealDataPipeline(seeded).run([Symbol.EURUSD], DAY, DAY + timedelta(days=1), "v1")

        store = ParquetCandleStore(seeded.storage.normalized_root)
        for timeframe in (Timeframe.M1, *DERIVED_TIMEFRAMES):
            frame = store.read(Symbol.EURUSD, timeframe)
            assert list(frame.columns) == list(CANDLE_COLUMNS)
            assert "close_time" not in frame.columns
            assert frame["timestamp"].is_monotonic_increasing

    def test_rerun_without_overwrite_is_refused_gracefully(self, seeded):
        """Immutability must be reported as a failure, not raised through the run —
        otherwise one symbol's refusal would discard the other's results."""
        pipeline = RealDataPipeline(seeded)
        pipeline.run([Symbol.EURUSD], DAY, DAY + timedelta(days=1), "v1")

        again = pipeline.run([Symbol.EURUSD], DAY, DAY + timedelta(days=1), "v1")
        assert not again.succeeded
        assert any("ImmutableWriteError" in f for f in again.failures)

    def test_tick_quality_reaches_the_manifest_notes(self, seeded):
        RealDataPipeline(seeded).run([Symbol.EURUSD], DAY, DAY + timedelta(days=1), "v1")

        manifest = ManifestStore(seeded.storage.manifest_root).read("v1")
        assert "tick_quality" in manifest.notes
        assert manifest.notes["tick_quality"]["EURUSD"]["input_ticks"] == 1440
        assert "raw_archive" in manifest.notes
        assert manifest.notes["timezone"] == "UTC"
