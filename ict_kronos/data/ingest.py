"""IngestPipeline — fetch → normalize → persist → manifest.

The one place the Phase 1 stages are composed. Each stage is independently tested;
this module's job is only to sequence them and to make sure nothing lands on disk
without provenance.

Ported shape: ``Learnexia/python/curriculum_intelligence/ingestion/pipeline.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..app.config import Settings
from ..app.logging import get_logger
from ..domain import Symbol, Timeframe
from ..storage.manifest import DatasetManifest, ManifestStore
from ..storage.parquet_store import ParquetCandleStore, PartitionInfo
from .base import MarketDataProvider, require_utc
from .normalizer import DataNormalizer, NormalizationReport

logger = get_logger(__name__)


@dataclass
class IngestResult:
    """Outcome of one ingest run, ready to be summarised or written to the outbox."""

    dataset_version: str
    manifest_path: Path | None = None
    partitions: list[PartitionInfo] = field(default_factory=list)
    reports: list[NormalizationReport] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return sum(p.rows for p in self.partitions)

    @property
    def succeeded(self) -> bool:
        return not self.failures


class IngestPipeline:
    """Composes provider, normalizer, store and manifest into one ingest run."""

    def __init__(
        self,
        provider: MarketDataProvider,
        settings: Settings,
        *,
        normalizer: DataNormalizer | None = None,
        store: ParquetCandleStore | None = None,
        manifests: ManifestStore | None = None,
    ) -> None:
        self._provider = provider
        self._settings = settings
        self._normalizer = normalizer or DataNormalizer(max_gap_bars=settings.market_data.max_gap_bars)
        self._store = store or ParquetCandleStore(settings.storage.normalized_root)
        self._manifests = manifests or ManifestStore(settings.storage.manifest_root)

    def run(
        self,
        pairs: list[tuple[Symbol, Timeframe]],
        start: datetime,
        end: datetime,
        dataset_version: str,
        *,
        overwrite: bool = False,
    ) -> IngestResult:
        """Ingest every (symbol, timeframe) pair over ``[start, end)``.

        A failure on one pair does not abort the run — the remaining pairs are still
        ingested and the failure is recorded. A partial dataset that says which parts
        are missing is more useful than no dataset and a stack trace.
        """
        require_utc("start", start)
        require_utc("end", end)

        result = IngestResult(dataset_version=dataset_version)

        for symbol, timeframe in pairs:
            try:
                partitions, report = self._ingest_one(symbol, timeframe, start, end, overwrite=overwrite)
                result.partitions.extend(partitions)
                result.reports.append(report)
            except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
                message = f"{symbol.value}/{timeframe.value}: {type(exc).__name__}: {exc}"
                logger.error("ingest failed for %s", message)
                result.failures.append(message)

        result.manifest_path = self._write_manifest(result, overwrite=overwrite)
        logger.info(
            "ingest %s complete: %d partition(s), %d row(s), %d failure(s)",
            dataset_version,
            len(result.partitions),
            result.total_rows,
            len(result.failures),
        )
        return result

    def _ingest_one(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        *,
        overwrite: bool,
    ) -> tuple[list[PartitionInfo], NormalizationReport]:
        raw: pd.DataFrame = self._provider.fetch(symbol, timeframe, start, end)
        normalized, report = self._normalizer.normalize(raw, symbol, timeframe)
        partitions = self._store.write(normalized, symbol, timeframe, overwrite=overwrite)
        return partitions, report

    def _write_manifest(self, result: IngestResult, *, overwrite: bool) -> Path | None:
        if not result.partitions and not result.reports:
            logger.warning("no data ingested for %s — no manifest written", result.dataset_version)
            return None

        manifest = DatasetManifest.create(
            dataset_version=result.dataset_version,
            provider=self._provider.name,
            partitions=result.partitions,
            normalization_reports=[r.as_dict() for r in result.reports],
            notes={
                "failures": result.failures,
                # Recorded explicitly so no downstream consumer mistakes a tick count
                # for exchange volume (see dukascopy.py).
                "volume_semantics": (
                    "tick count per bar when sourced from Dukascopy ticks; "
                    "provider-supplied volume otherwise"
                ),
                "max_gap_bars": self._settings.market_data.max_gap_bars,
            },
        )
        return self._manifests.write(manifest, overwrite=overwrite)
