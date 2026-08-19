"""Real-data backfill orchestration: ticks → 1M → 5M/15M/1H → Parquet + manifest.

Composes :class:`TickBackfill` (download + tick validation + 1M aggregation), the
:class:`DataNormalizer` (grid alignment, dedup, invariants, gaps), the resampler
(1M → higher timeframes), and the immutable Parquet store + manifest.

**All higher timeframes are derived from the SAME 1-minute series**, never fetched
independently. That is what guarantees a 1H bar is exactly the sixty 1M bars beneath
it. Independently-built timeframes disagree at the margins, and that disagreement
surfaces downstream as phantom ICT structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from ..app.config import Settings
from ..app.logging import get_logger
from ..domain import Symbol, Timeframe
from ..storage.manifest import (
    PIPELINE_VERSION,
    DatasetManifest,
    ManifestStore,
    SeriesProvenance,
    current_git_commit,
)
from ..storage.parquet_store import ParquetCandleStore, PartitionInfo
from .backfill import BackfillResult, TickBackfill
from .normalizer import DataNormalizer, NormalizationReport
from .resampler import resample

logger = get_logger(__name__)

#: Derived from the 1M base. Ordered fastest-first so logs read naturally.
DERIVED_TIMEFRAMES: tuple[Timeframe, ...] = (Timeframe.M5, Timeframe.M15, Timeframe.H1)


@dataclass
class SeriesResult:
    """One (symbol, timeframe) series that landed on disk."""

    symbol: str
    timeframe: str
    rows: int
    partitions: list[PartitionInfo] = field(default_factory=list)
    report: NormalizationReport | None = None
    derived_from: str | None = None


@dataclass
class RealDataResult:
    """Everything one real-data run produced, ready for the proof document."""

    dataset_version: str
    manifest_path: Path | None = None
    backfills: dict[str, BackfillResult] = field(default_factory=dict)
    series: list[SeriesResult] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return not self.failures


class RealDataPipeline:
    """Backfill real ticks and materialise the full timeframe stack."""

    def __init__(
        self,
        settings: Settings,
        *,
        backfill: TickBackfill | None = None,
        normalizer: DataNormalizer | None = None,
        store: ParquetCandleStore | None = None,
        manifests: ManifestStore | None = None,
    ) -> None:
        self._settings = settings
        self._backfill = backfill or TickBackfill(
            settings.market_data.download_cache,
            base_url=settings.market_data.dukascopy_base_url,
            timeout_seconds=settings.market_data.request_timeout_seconds,
        )
        self._normalizer = normalizer or DataNormalizer(max_gap_bars=settings.market_data.max_gap_bars)
        self._store = store or ParquetCandleStore(settings.storage.normalized_root)
        self._manifests = manifests or ManifestStore(settings.storage.manifest_root)

    def run(
        self,
        symbols: list[Symbol],
        start: datetime,
        end: datetime,
        dataset_version: str,
        *,
        overwrite: bool = False,
    ) -> RealDataResult:
        result = RealDataResult(dataset_version=dataset_version)
        provenance: list[SeriesProvenance] = []

        for symbol in symbols:
            try:
                backfill = self._backfill.run(symbol, start, end)
            except Exception as exc:  # noqa: BLE001 - one symbol must not abort the run
                result.failures.append(f"{symbol.value}: backfill: {type(exc).__name__}: {exc}")
                continue

            result.backfills[symbol.value] = backfill
            result.failures.extend(backfill.download_failures[:20])
            if len(backfill.download_failures) > 20:
                result.failures.append(
                    f"{symbol.value}: ...and {len(backfill.download_failures) - 20} more download failures"
                )

            if len(backfill.bars_1m) == 0:
                result.failures.append(f"{symbol.value}: no 1M bars produced")
                continue

            try:
                provenance.extend(
                    self._materialise_symbol(symbol, backfill, start, end, result, overwrite=overwrite)
                )
            except Exception as exc:  # noqa: BLE001 - recorded so one symbol cannot abort the run
                # The common case is ImmutableWriteError: this dataset version already
                # exists. That is a legitimate refusal (CLAUDE.md rule 7), not a crash,
                # and the other symbol's results must still be reported.
                result.failures.append(f"{symbol.value}: persist: {type(exc).__name__}: {exc}")

        result.manifest_path = self._write_manifest(result, provenance, start, end, overwrite=overwrite)
        return result

    # --------------------------------------------------------------- internals

    def _materialise_symbol(
        self,
        symbol: Symbol,
        backfill: BackfillResult,
        start: datetime,
        end: datetime,
        result: RealDataResult,
        *,
        overwrite: bool,
    ) -> list[SeriesProvenance]:
        provenance: list[SeriesProvenance] = []

        base, base_report = self._normalizer.normalize(backfill.bars_1m, symbol, Timeframe.M1)
        base_partitions = self._store.write(base, symbol, Timeframe.M1, overwrite=overwrite)
        result.series.append(
            SeriesResult(
                symbol=symbol.value,
                timeframe=Timeframe.M1.value,
                rows=len(base),
                partitions=base_partitions,
                report=base_report,
            )
        )
        provenance.append(
            self._provenance(symbol, Timeframe.M1, base, base_partitions, backfill, start, end, None)
        )

        for timeframe in DERIVED_TIMEFRAMES:
            # Derived from the SAME 1M series — never fetched separately.
            derived = resample(base, Timeframe.M1, timeframe, symbol)
            if len(derived) == 0:
                result.failures.append(f"{symbol.value}/{timeframe.value}: resample produced no bars")
                continue

            # Drop close_time before persisting: it is a derived convenience column,
            # recomputable from (timestamp, timeframe), and the stored schema stays
            # exactly CANDLE_COLUMNS so every reader sees one shape.
            payload = derived.drop(columns=["close_time"])
            normalized, report = self._normalizer.normalize(payload, symbol, timeframe)
            partitions = self._store.write(normalized, symbol, timeframe, overwrite=overwrite)

            result.series.append(
                SeriesResult(
                    symbol=symbol.value,
                    timeframe=timeframe.value,
                    rows=len(normalized),
                    partitions=partitions,
                    report=report,
                    derived_from=Timeframe.M1.value,
                )
            )
            provenance.append(
                self._provenance(
                    symbol, timeframe, normalized, partitions, backfill, start, end, Timeframe.M1.value
                )
            )

        return provenance

    def _provenance(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        frame: pd.DataFrame,
        partitions: list[PartitionInfo],
        backfill: BackfillResult,
        start: datetime,
        end: datetime,
        derived_from: str | None,
    ) -> SeriesProvenance:
        return SeriesProvenance(
            symbol=symbol.value,
            source="dukascopy-ticks",
            bar_timeframe=timeframe.value,
            download_period_start=start.isoformat(),
            download_period_end=end.isoformat(),
            timezone="UTC",
            raw_file_hash=backfill.raw.digest,
            normalized_file_hash=_combine_partition_hashes(partitions),
            row_count=len(frame),
            first_timestamp=(frame["timestamp"].iloc[0].isoformat() if len(frame) else None),
            last_timestamp=(frame["timestamp"].iloc[-1].isoformat() if len(frame) else None),
            creation_timestamp=datetime.now(UTC).isoformat(),
            pipeline_version=PIPELINE_VERSION,
            git_commit=current_git_commit(),
            derived_from=derived_from,
            extra={"raw_files": backfill.raw.file_count, "raw_bytes": backfill.raw.total_bytes},
        )

    def _write_manifest(
        self,
        result: RealDataResult,
        provenance: list[SeriesProvenance],
        start: datetime,
        end: datetime,
        *,
        overwrite: bool,
    ) -> Path | None:
        if not result.series:
            logger.warning("no series produced for %s — no manifest written", result.dataset_version)
            return None

        partitions = [p for s in result.series for p in s.partitions]
        reports = [s.report.as_dict() for s in result.series if s.report is not None]

        manifest = DatasetManifest.create(
            dataset_version=result.dataset_version,
            provider="dukascopy",
            partitions=partitions,
            datasets=provenance,
            normalization_reports=reports,
            notes={
                "window": {"start": start.isoformat(), "end": end.isoformat()},
                "timezone": "UTC",
                "price_side": "bid",
                "volume_semantics": "tick count per bar (Dukascopy per-tick volumes are "
                "indicative broker volumes, not exchange volume)",
                "derivation": "1M built from ticks; 5M/15M/1H derived from the SAME 1M series",
                "tick_quality": {sym: bf.tick_quality.as_dict() for sym, bf in result.backfills.items()},
                "raw_archive": {sym: bf.raw.as_dict() for sym, bf in result.backfills.items()},
                "failures": result.failures,
                "max_gap_bars": self._settings.market_data.max_gap_bars,
            },
        )
        return self._manifests.write(manifest, overwrite=overwrite)


def _combine_partition_hashes(partitions: list[PartitionInfo]) -> str:
    """One stable hash across a series' year partitions.

    A single-partition series returns that partition's own SHA-256 unchanged, so the
    common case stays directly checkable by hand.
    """
    import hashlib

    if not partitions:
        return ""
    if len(partitions) == 1:
        return partitions[0].sha256
    digest = hashlib.sha256()
    for info in sorted(partitions, key=lambda p: p.year):
        digest.update(f"{p_year(info)}:{info.sha256}\n".encode())
    return digest.hexdigest()


def p_year(info: PartitionInfo) -> int:
    return info.year
