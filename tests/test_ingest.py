"""IngestPipeline — end-to-end composition of fetch, normalize, persist, manifest.

Uses the real fixture provider and a real temporary store, so this is a genuine
integration test of the Phase 1 slice rather than a mock handshake.
"""

from __future__ import annotations

import pytest

from ict_kronos.app.config import Settings
from ict_kronos.data import FixtureProvider, IngestPipeline, MarketDataError
from ict_kronos.domain import Symbol, Timeframe
from ict_kronos.storage import ManifestStore, ParquetCandleStore

from .conftest import FIXTURE_BAR_COUNT, FIXTURE_END, FIXTURE_START


@pytest.fixture
def settings(tmp_data_root, fixture_root, monkeypatch) -> Settings:
    monkeypatch.setenv("DATA_ROOT", str(tmp_data_root))
    monkeypatch.setenv("MARKET_DATA_FIXTURE_ROOT", str(fixture_root))
    monkeypatch.setenv("MARKET_DATA_BACKEND", "fixture")
    return Settings.from_env()


@pytest.fixture
def pipeline(settings) -> IngestPipeline:
    return IngestPipeline(FixtureProvider(settings.market_data.fixture_root), settings)


class TestIngestHappyPath:
    def test_ingests_both_mvp_symbols(self, pipeline, settings):
        result = pipeline.run(
            [(Symbol.EURUSD, Timeframe.M5), (Symbol.XAUUSD, Timeframe.M5)],
            FIXTURE_START,
            FIXTURE_END,
            dataset_version="v1",
        )

        assert result.succeeded
        assert result.total_rows == FIXTURE_BAR_COUNT * 2
        assert len(result.partitions) == 2
        assert len(result.reports) == 2

    def test_data_is_readable_back_from_the_store(self, pipeline, settings):
        pipeline.run([(Symbol.EURUSD, Timeframe.M5)], FIXTURE_START, FIXTURE_END, "v1")

        store = ParquetCandleStore(settings.storage.normalized_root)
        out = store.read(Symbol.EURUSD, Timeframe.M5)
        assert len(out) == FIXTURE_BAR_COUNT
        assert out["timestamp"].is_monotonic_increasing

    def test_manifest_is_written_with_provenance(self, pipeline, settings):
        result = pipeline.run([(Symbol.EURUSD, Timeframe.M5)], FIXTURE_START, FIXTURE_END, "v1")

        assert result.manifest_path is not None
        manifest = ManifestStore(settings.storage.manifest_root).read("v1")
        assert manifest.provider == "fixture"
        assert manifest.total_rows == FIXTURE_BAR_COUNT
        assert manifest.normalization_reports[0]["symbol"] == "EURUSD"

    def test_manifest_verifies_against_the_written_data(self, pipeline, settings):
        pipeline.run(
            [(Symbol.EURUSD, Timeframe.M5), (Symbol.XAUUSD, Timeframe.M5)],
            FIXTURE_START,
            FIXTURE_END,
            "v1",
        )
        assert ManifestStore(settings.storage.manifest_root).verify("v1") == []

    def test_manifest_records_volume_semantics(self, pipeline, settings):
        """Tick counts must not be mistaken for exchange volume downstream."""
        pipeline.run([(Symbol.EURUSD, Timeframe.M5)], FIXTURE_START, FIXTURE_END, "v1")
        manifest = ManifestStore(settings.storage.manifest_root).read("v1")
        assert "volume_semantics" in manifest.notes


class TestIngestResilience:
    def test_one_failing_pair_does_not_abort_the_run(self, pipeline, settings):
        """A partial dataset that says what is missing beats no dataset and a stack
        trace."""
        result = pipeline.run(
            [
                (Symbol.EURUSD, Timeframe.M5),
                (Symbol.EURUSD, Timeframe.H4),  # no fixture exists
                (Symbol.XAUUSD, Timeframe.M5),
            ],
            FIXTURE_START,
            FIXTURE_END,
            "v1",
        )

        assert not result.succeeded
        assert len(result.failures) == 1
        assert "EURUSD/4h" in result.failures[0]
        # The two good pairs still landed.
        assert result.total_rows == FIXTURE_BAR_COUNT * 2

    def test_failures_are_recorded_in_the_manifest(self, pipeline, settings):
        pipeline.run(
            [(Symbol.EURUSD, Timeframe.M5), (Symbol.EURUSD, Timeframe.H4)],
            FIXTURE_START,
            FIXTURE_END,
            "v1",
        )
        manifest = ManifestStore(settings.storage.manifest_root).read("v1")
        assert len(manifest.notes["failures"]) == 1

    def test_rerunning_the_same_version_is_refused(self, pipeline):
        """Immutability applies to the whole run, not just individual partitions."""
        pipeline.run([(Symbol.EURUSD, Timeframe.M5)], FIXTURE_START, FIXTURE_END, "v1")

        result = pipeline.run([(Symbol.EURUSD, Timeframe.M5)], FIXTURE_START, FIXTURE_END, "v1")
        assert not result.succeeded
        assert "ImmutableWriteError" in result.failures[0]

    def test_naive_bounds_are_rejected(self, pipeline):
        from datetime import datetime

        with pytest.raises(ValueError, match="timezone-aware"):
            pipeline.run(
                [(Symbol.EURUSD, Timeframe.M5)],
                datetime(2024, 3, 4),  # noqa: DTZ001
                FIXTURE_END,
                "v1",
            )

    def test_empty_window_produces_no_partitions(self, pipeline, settings):
        from datetime import UTC, datetime, timedelta

        far = datetime(2030, 1, 1, tzinfo=UTC)
        result = pipeline.run([(Symbol.EURUSD, Timeframe.M5)], far, far + timedelta(days=1), "v-empty")

        assert result.succeeded
        assert result.partitions == []
        # A report is still written — "we looked and found nothing" is a finding.
        assert len(result.reports) == 1
        assert result.reports[0].input_rows == 0


class TestProviderErrorSurfacing:
    def test_provider_errors_are_typed(self, settings):
        provider = FixtureProvider(settings.market_data.fixture_root)
        with pytest.raises(MarketDataError):
            provider.fetch(Symbol.XAUUSD, Timeframe.D1, FIXTURE_START, FIXTURE_END)
