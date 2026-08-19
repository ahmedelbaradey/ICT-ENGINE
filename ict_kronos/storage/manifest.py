"""Dataset version manifests (Master Plan §29, CLAUDE.md rule 8).

A manifest is the record that makes a result reproducible. It answers, for any
published metric: exactly which bytes was this computed from, produced by which
provider, normalized by which code, at which git commit — and does that data still
hash to the same values today?

Without this, "the 2024 out-of-sample profit factor was 1.4" is an unfalsifiable
claim. With it, anyone can re-derive the dataset and check.

Manifests are written to ``<data_root>/manifests/<dataset_version>.json`` and are
themselves immutable: writing a version that already exists is refused.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..app.logging import get_logger
from .parquet_store import PartitionInfo, sha256_of

logger = get_logger(__name__)

#: Bumped to 2 when the real-data proof added the per-series ``datasets`` block
#: (source, download_period, raw/normalized hashes, pipeline_version, timezone).
MANIFEST_SCHEMA_VERSION = 2

#: Version of the ingest/normalize/resample code path that produced a dataset.
#: Bump this whenever a change would alter the bars produced from identical raw
#: bytes — it is what distinguishes "same data" from "same data, same maths".
PIPELINE_VERSION = "1.1.0"


@dataclass
class SeriesProvenance:
    """Provenance for one (symbol, timeframe) series within a dataset version.

    Carries every field the real-data proof requires, so a reader can answer
    "where did this come from and can I rebuild it?" without opening any code.
    """

    symbol: str
    source: str
    bar_timeframe: str
    download_period_start: str
    download_period_end: str
    timezone: str
    raw_file_hash: str
    normalized_file_hash: str
    row_count: int
    first_timestamp: str | None
    last_timestamp: str | None
    creation_timestamp: str
    pipeline_version: str
    git_commit: str | None
    derived_from: str | None = None
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "source": self.source,
            "bar_timeframe": self.bar_timeframe,
            "download_period": {
                "start": self.download_period_start,
                "end": self.download_period_end,
            },
            "timezone": self.timezone,
            "raw_file_hash": self.raw_file_hash,
            "normalized_file_hash": self.normalized_file_hash,
            "row_count": self.row_count,
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
            "creation_timestamp": self.creation_timestamp,
            "pipeline_version": self.pipeline_version,
            "git_commit": self.git_commit,
            "derived_from": self.derived_from,
            **({"extra": self.extra} if self.extra else {}),
        }


@dataclass
class DatasetManifest:
    """Provenance for one versioned market-data build."""

    dataset_version: str
    created_at: str
    provider: str
    git_commit: str | None
    schema_version: int = MANIFEST_SCHEMA_VERSION
    pipeline_version: str = PIPELINE_VERSION
    partitions: list[dict] = field(default_factory=list)
    datasets: list[dict] = field(default_factory=list)
    normalization_reports: list[dict] = field(default_factory=list)
    notes: dict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        dataset_version: str,
        provider: str,
        *,
        partitions: list[PartitionInfo] | None = None,
        datasets: list[SeriesProvenance] | None = None,
        normalization_reports: list[dict] | None = None,
        notes: dict | None = None,
        repo_root: Path | None = None,
    ) -> DatasetManifest:
        return cls(
            dataset_version=dataset_version,
            created_at=datetime.now(UTC).isoformat(),
            provider=provider,
            git_commit=current_git_commit(repo_root),
            partitions=[p.as_dict() for p in (partitions or [])],
            datasets=[d.as_dict() for d in (datasets or [])],
            normalization_reports=list(normalization_reports or []),
            notes=dict(notes or {}),
        )

    @property
    def total_rows(self) -> int:
        return sum(int(p.get("rows", 0)) for p in self.partitions)

    def as_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "dataset_version": self.dataset_version,
            "created_at": self.created_at,
            "provider": self.provider,
            "pipeline_version": self.pipeline_version,
            "git_commit": self.git_commit,
            "total_rows": self.total_rows,
            "partition_count": len(self.partitions),
            "datasets": self.datasets,
            "partitions": self.partitions,
            "normalization_reports": self.normalization_reports,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> DatasetManifest:
        return cls(
            dataset_version=payload["dataset_version"],
            created_at=payload["created_at"],
            provider=payload["provider"],
            git_commit=payload.get("git_commit"),
            schema_version=payload.get("schema_version", MANIFEST_SCHEMA_VERSION),
            pipeline_version=payload.get("pipeline_version", PIPELINE_VERSION),
            partitions=payload.get("partitions", []),
            datasets=payload.get("datasets", []),
            normalization_reports=payload.get("normalization_reports", []),
            notes=payload.get("notes", {}),
        )


class ManifestStore:
    """Reads and writes dataset manifests."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def path_for(self, dataset_version: str) -> Path:
        return self._root / f"{dataset_version}.json"

    def write(self, manifest: DatasetManifest, *, overwrite: bool = False) -> Path:
        path = self.path_for(manifest.dataset_version)
        if path.exists() and not overwrite:
            raise FileExistsError(
                f"manifest {path} already exists. Dataset versions are immutable — "
                f"bump the version rather than rewriting history."
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest.as_dict(), indent=2, sort_keys=False), encoding="utf-8")
        logger.info(
            "wrote manifest %s (%d partitions, %d rows)",
            path.as_posix(),
            len(manifest.partitions),
            manifest.total_rows,
        )
        return path

    def read(self, dataset_version: str) -> DatasetManifest:
        path = self.path_for(dataset_version)
        if not path.is_file():
            raise FileNotFoundError(f"no manifest at {path}")
        return DatasetManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_versions(self) -> list[str]:
        if not self._root.is_dir():
            return []
        return sorted(p.stem for p in self._root.glob("*.json"))

    def verify(self, dataset_version: str, *, base_path: Path | None = None) -> list[str]:
        """Re-hash every partition and return a list of discrepancies.

        An empty list means the data on disk is byte-identical to what produced the
        recorded results. Anything else means a published result can no longer be
        trusted to correspond to this data, and says exactly which file changed.
        """
        manifest = self.read(dataset_version)
        problems: list[str] = []

        for entry in manifest.partitions:
            path = Path(entry["path"])
            if base_path is not None and not path.is_absolute():
                path = base_path / path

            if not path.is_file():
                problems.append(f"MISSING: {entry['path']}")
                continue

            actual = sha256_of(path)
            if actual != entry["sha256"]:
                problems.append(
                    f"CHANGED: {entry['path']} (manifest {entry['sha256'][:12]}, actual {actual[:12]})"
                )

        if problems:
            logger.warning("manifest %s failed verification: %d problem(s)", dataset_version, len(problems))
        else:
            logger.info(
                "manifest %s verified: %d partition(s) intact", dataset_version, len(manifest.partitions)
            )
        return problems


def current_git_commit(repo_root: Path | None = None) -> str | None:
    """The current git commit, or ``None`` outside a repository.

    Best-effort by design: a missing commit must not stop a research run, but its
    absence is recorded honestly rather than substituted with a placeholder.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root) if repo_root else None,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    commit = result.stdout.strip()
    return commit or None
