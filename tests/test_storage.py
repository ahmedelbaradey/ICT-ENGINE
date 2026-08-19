"""Parquet store immutability and dataset manifest provenance."""

from __future__ import annotations

import json
from datetime import timedelta

import pandas as pd
import pytest

from ict_kronos.domain import CANDLE_COLUMNS, Symbol, Timeframe
from ict_kronos.storage import (
    DatasetManifest,
    ImmutableWriteError,
    ManifestStore,
    ParquetCandleStore,
    sha256_of,
)

from .conftest import FIXTURE_START, make_frame


@pytest.fixture
def store(tmp_path) -> ParquetCandleStore:
    return ParquetCandleStore(tmp_path / "normalized")


class TestParquetWrite:
    def test_writes_one_partition_per_year(self, store):
        frame = make_frame(10, timeframe=Timeframe.H1)
        infos = store.write(frame, Symbol.EURUSD, Timeframe.H1)

        assert len(infos) == 1
        assert infos[0].year == 2024
        assert infos[0].rows == 10
        assert infos[0].path.is_file()

    def test_splits_across_year_boundary(self, store):
        # 48 hourly bars starting 2023-12-31 12:00 UTC straddles New Year.
        frame = make_frame(48, timeframe=Timeframe.H1, start=pd.Timestamp("2023-12-31T12:00:00Z"))
        infos = store.write(frame, Symbol.EURUSD, Timeframe.H1)

        assert sorted(i.year for i in infos) == [2023, 2024]
        assert sum(i.rows for i in infos) == 48

    def test_records_a_content_hash(self, store):
        infos = store.write(make_frame(5), Symbol.EURUSD, Timeframe.M5)
        assert infos[0].sha256 == sha256_of(infos[0].path)
        assert len(infos[0].sha256) == 64

    def test_records_the_partition_window(self, store):
        frame = make_frame(6, timeframe=Timeframe.H1)
        info = store.write(frame, Symbol.EURUSD, Timeframe.H1)[0]

        assert info.first_timestamp == frame["timestamp"].iloc[0].isoformat()
        assert info.last_timestamp == frame["timestamp"].iloc[-1].isoformat()

    def test_empty_frame_writes_nothing(self, store):
        assert store.write(make_frame(0), Symbol.EURUSD, Timeframe.M5) == []

    def test_rejects_frame_missing_columns(self, store):
        with pytest.raises(ValueError, match="missing columns"):
            store.write(make_frame(5).drop(columns=["volume"]), Symbol.EURUSD, Timeframe.M5)


class TestImmutability:
    def test_second_write_is_refused(self, store):
        """CLAUDE.md rule 7 — raw market data is never overwritten."""
        frame = make_frame(5)
        store.write(frame, Symbol.EURUSD, Timeframe.M5)

        with pytest.raises(ImmutableWriteError, match="immutable"):
            store.write(frame, Symbol.EURUSD, Timeframe.M5)

    def test_overwrite_requires_an_explicit_opt_in(self, store):
        frame = make_frame(5)
        store.write(frame, Symbol.EURUSD, Timeframe.M5)

        infos = store.write(make_frame(7), Symbol.EURUSD, Timeframe.M5, overwrite=True)
        assert infos[0].rows == 7

    def test_different_symbols_do_not_collide(self, store):
        frame = make_frame(5)
        store.write(frame, Symbol.EURUSD, Timeframe.M5)
        store.write(frame, Symbol.XAUUSD, Timeframe.M5)  # must not raise

    def test_different_timeframes_do_not_collide(self, store):
        store.write(make_frame(5, timeframe=Timeframe.M5), Symbol.EURUSD, Timeframe.M5)
        store.write(make_frame(5, timeframe=Timeframe.H1), Symbol.EURUSD, Timeframe.H1)


class TestParquetRead:
    def test_roundtrip_preserves_values_and_schema(self, store):
        frame = make_frame(20, timeframe=Timeframe.M15)
        store.write(frame, Symbol.EURUSD, Timeframe.M15)

        out = store.read(Symbol.EURUSD, Timeframe.M15)
        assert list(out.columns) == list(CANDLE_COLUMNS)
        assert len(out) == 20
        pd.testing.assert_series_equal(
            out["close"].reset_index(drop=True), frame["close"].reset_index(drop=True)
        )

    def test_roundtrip_preserves_utc(self, store):
        frame = make_frame(5)
        store.write(frame, Symbol.EURUSD, Timeframe.M5)
        out = store.read(Symbol.EURUSD, Timeframe.M5)
        assert str(out["timestamp"].dtype.tz) == "UTC"

    def test_read_window_is_half_open(self, store):
        frame = make_frame(24, timeframe=Timeframe.H1)
        store.write(frame, Symbol.EURUSD, Timeframe.H1)

        start = FIXTURE_START + timedelta(hours=4)
        end = FIXTURE_START + timedelta(hours=8)
        out = store.read(Symbol.EURUSD, Timeframe.H1, start=pd.Timestamp(start), end=pd.Timestamp(end))

        assert len(out) == 4
        assert out["timestamp"].min() == pd.Timestamp(start)
        assert out["timestamp"].max() == pd.Timestamp(end - timedelta(hours=1))

    def test_read_spans_year_partitions_in_order(self, store):
        frame = make_frame(48, timeframe=Timeframe.H1, start=pd.Timestamp("2023-12-31T12:00:00Z"))
        store.write(frame, Symbol.EURUSD, Timeframe.H1)

        out = store.read(Symbol.EURUSD, Timeframe.H1)
        assert len(out) == 48
        assert out["timestamp"].is_monotonic_increasing

    def test_missing_data_reads_as_empty(self, store):
        out = store.read(Symbol.XAUUSD, Timeframe.D1)
        assert len(out) == 0
        assert list(out.columns) == list(CANDLE_COLUMNS)

    def test_available_years(self, store):
        store.write(
            make_frame(48, timeframe=Timeframe.H1, start=pd.Timestamp("2023-12-31T12:00:00Z")),
            Symbol.EURUSD,
            Timeframe.H1,
        )
        assert store.available_years(Symbol.EURUSD, Timeframe.H1) == [2023, 2024]


class TestManifest:
    def test_write_and_read_roundtrip(self, tmp_path, store):
        manifests = ManifestStore(tmp_path / "manifests")
        infos = store.write(make_frame(10), Symbol.EURUSD, Timeframe.M5)

        manifest = DatasetManifest.create("v1", "fixture", partitions=infos)
        path = manifests.write(manifest)

        assert path.is_file()
        restored = manifests.read("v1")
        assert restored.dataset_version == "v1"
        assert restored.provider == "fixture"
        assert restored.total_rows == 10

    def test_manifest_is_valid_json_with_expected_keys(self, tmp_path, store):
        manifests = ManifestStore(tmp_path / "manifests")
        infos = store.write(make_frame(4), Symbol.EURUSD, Timeframe.M5)
        manifests.write(DatasetManifest.create("v1", "fixture", partitions=infos))

        payload = json.loads((tmp_path / "manifests" / "v1.json").read_text(encoding="utf-8"))
        for key in (
            "schema_version",
            "dataset_version",
            "created_at",
            "provider",
            "git_commit",
            "total_rows",
            "partitions",
            "normalization_reports",
        ):
            assert key in payload

    def test_versions_are_immutable(self, tmp_path):
        manifests = ManifestStore(tmp_path / "manifests")
        manifests.write(DatasetManifest.create("v1", "fixture"))

        with pytest.raises(FileExistsError, match="immutable"):
            manifests.write(DatasetManifest.create("v1", "fixture"))

    def test_list_versions(self, tmp_path):
        manifests = ManifestStore(tmp_path / "manifests")
        manifests.write(DatasetManifest.create("v2", "fixture"))
        manifests.write(DatasetManifest.create("v1", "fixture"))
        assert manifests.list_versions() == ["v1", "v2"]

    def test_verify_passes_on_untouched_data(self, tmp_path, store):
        manifests = ManifestStore(tmp_path / "manifests")
        infos = store.write(make_frame(10), Symbol.EURUSD, Timeframe.M5)
        manifests.write(DatasetManifest.create("v1", "fixture", partitions=infos))

        assert manifests.verify("v1") == []

    def test_verify_detects_modified_data(self, tmp_path, store):
        """This is what makes a published metric falsifiable: if the bytes changed,
        the result can no longer be attributed to this dataset."""
        manifests = ManifestStore(tmp_path / "manifests")
        infos = store.write(make_frame(10), Symbol.EURUSD, Timeframe.M5)
        manifests.write(DatasetManifest.create("v1", "fixture", partitions=infos))

        store.write(make_frame(11), Symbol.EURUSD, Timeframe.M5, overwrite=True)

        problems = manifests.verify("v1")
        assert len(problems) == 1
        assert problems[0].startswith("CHANGED:")

    def test_verify_detects_missing_data(self, tmp_path, store):
        manifests = ManifestStore(tmp_path / "manifests")
        infos = store.write(make_frame(10), Symbol.EURUSD, Timeframe.M5)
        manifests.write(DatasetManifest.create("v1", "fixture", partitions=infos))

        infos[0].path.unlink()

        problems = manifests.verify("v1")
        assert len(problems) == 1
        assert problems[0].startswith("MISSING:")

    def test_records_the_git_commit(self, tmp_path):
        """Reproducibility needs the code version, not only the data version."""
        manifest = DatasetManifest.create("v1", "fixture", repo_root=tmp_path.parent)
        # Either a real 40-char SHA, or an honest None outside a repository.
        assert manifest.git_commit is None or len(manifest.git_commit) == 40

    def test_read_unknown_version_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            ManifestStore(tmp_path / "manifests").read("nope")
