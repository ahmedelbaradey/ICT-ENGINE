"""Configuration — env-only. No secrets are ever hardcoded or committed.

Ported from ``Learnexia/python/curriculum_intelligence/app/config.py`` (ADR-0001):
frozen dataclass aggregates with ``from_env()`` classmethods, typed getters, and a
single top-level :class:`Settings` built once at startup by :func:`get_settings`.

CLAUDE.md rule 4 — no hardcoded trading assumptions — is enforced here: every
threshold, session boundary and cost lives in a config field with a documented
default, never as a literal inside detection or execution logic.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class MarketDataBackendKind(StrEnum):
    """Which market-data provider the ingest lane uses.

    ``fixture`` is the DEFAULT in dev + CI (deterministic, file-driven, no network).
    ``dukascopy`` is the live backend — only selected once ``MARKET_DATA_BACKEND=dukascopy``
    is set AND the download cache directory is configured. Mirrors the
    mock-by-default/flip-later posture of every other backend (CLAUDE.md rule 9).
    """

    FIXTURE = "fixture"
    DUKASCOPY = "dukascopy"


class KronosBackendKind(StrEnum):
    """Which Kronos backend the forecast lane uses (Phase 5).

    ``mock`` is the DEFAULT in dev + CI — deterministic, zero-dependency, and it keeps
    PyTorch out of the default test gate. ``local`` loads real weights and is opt-in.
    """

    MOCK = "mock"
    LOCAL = "local"


class LlmBackendKind(StrEnum):
    """Which LLM backend the reporting/narration lane uses.

    NOTE (CLAUDE.md rule 3): the LLM never produces a feature, prediction or decision.
    This backend exists only for interpreting results and generating reports.
    """

    MOCK = "mock"
    CLAUDE = "claude"


def _get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _get_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _get_path(name: str, default: str) -> Path:
    raw = os.environ.get(name)
    return Path(raw.strip() if raw and raw.strip() else default)


def _get_enum(name: str, enum_cls: type, default):
    raw = os.environ.get(name, default.value)
    try:
        return enum_cls(raw.strip().lower())
    except ValueError:
        return default


@dataclass(frozen=True)
class DatabaseConfig:
    """Postgres — the job outbox, experiment registry and model registry.

    This is ICT-Kronos's OWN database (ADR-0001 rejected sharing Learnexia's).
    Phase 1 does not require it; the ingest lane can run file-only.
    """

    dsn: str
    schema: str = "ict_kronos"

    @classmethod
    def from_env(cls) -> DatabaseConfig:
        dsn = os.environ.get("DATABASE_URL", "").strip()
        if not dsn:
            # Dev default matching docker-compose. Never a secret in prod.
            dsn = "postgresql://postgres:postgres@postgres:5432/ict_kronos"
        return cls(dsn=dsn, schema=os.environ.get("ICT_SCHEMA", "ict_kronos"))


@dataclass(frozen=True)
class StorageConfig:
    """Where the immutable Parquet market-data store lives.

    CLAUDE.md rule 7 — raw data is immutable. ``raw_root`` is written once per
    (symbol, timeframe, year) partition and never overwritten in place.
    """

    data_root: Path = Path("data")

    @classmethod
    def from_env(cls) -> StorageConfig:
        return cls(data_root=_get_path("DATA_ROOT", "data"))

    @property
    def raw_root(self) -> Path:
        return self.data_root / "raw"

    @property
    def normalized_root(self) -> Path:
        return self.data_root / "normalized"

    @property
    def manifest_root(self) -> Path:
        return self.data_root / "manifests"


@dataclass(frozen=True)
class MarketDataConfig:
    """Ingest-lane tuning.

    ``max_gap_bars`` is the number of consecutive missing bars above which a gap is
    reported as a DATA QUALITY event rather than treated as a normal weekend/holiday
    break. Gaps are always RECORDED and never silently filled (Phase 1 exit gate).
    """

    backend: MarketDataBackendKind = MarketDataBackendKind.FIXTURE
    fixture_root: Path = Path("tests/fixtures/market_data")
    download_cache: Path = Path("data/cache/dukascopy")
    dukascopy_base_url: str = "https://datafeed.dukascopy.com/datafeed"
    request_timeout_seconds: float = 30.0
    max_gap_bars: int = 3

    @classmethod
    def from_env(cls) -> MarketDataConfig:
        return cls(
            backend=_get_enum("MARKET_DATA_BACKEND", MarketDataBackendKind, MarketDataBackendKind.FIXTURE),
            fixture_root=_get_path("MARKET_DATA_FIXTURE_ROOT", "tests/fixtures/market_data"),
            download_cache=_get_path("DUKASCOPY_CACHE", "data/cache/dukascopy"),
            dukascopy_base_url=os.environ.get(
                "DUKASCOPY_BASE_URL", "https://datafeed.dukascopy.com/datafeed"
            ),
            request_timeout_seconds=_get_float("MARKET_DATA_TIMEOUT_SECONDS", 30.0),
            max_gap_bars=_get_int("MARKET_DATA_MAX_GAP_BARS", 3),
        )


@dataclass(frozen=True)
class KronosConfig:
    """Phase 5 — devops/opt-in gated. Empty until weights are provisioned."""

    backend: KronosBackendKind = KronosBackendKind.MOCK
    model_name: str = "kronos-small"
    weights_dir: Path | None = None
    # UNVERIFIED (see IMPLEMENTATION_ROADMAP Phase 5): the Master Plan claims 512 for
    # small/base. Treated as a configurable assumption until confirmed against the
    # upstream repo + model card. The forecast lane MUST NOT silently exceed it.
    max_context_bars: int = 512
    forecast_horizon: int = 12
    sample_paths: int = 1

    @classmethod
    def from_env(cls) -> KronosConfig:
        weights = os.environ.get("KRONOS_WEIGHTS_DIR", "").strip()
        return cls(
            backend=_get_enum("KRONOS_BACKEND", KronosBackendKind, KronosBackendKind.MOCK),
            model_name=os.environ.get("KRONOS_MODEL", "kronos-small"),
            weights_dir=Path(weights) if weights else None,
            max_context_bars=_get_int("KRONOS_MAX_CONTEXT_BARS", 512),
            forecast_horizon=_get_int("KRONOS_FORECAST_HORIZON", 12),
            sample_paths=_get_int("KRONOS_SAMPLE_PATHS", 1),
        )

    @property
    def is_configured(self) -> bool:
        return self.weights_dir is not None and self.weights_dir.exists()


@dataclass(frozen=True)
class LlmConfig:
    """Reporting/narration only — never on the prediction path (CLAUDE.md rule 3)."""

    backend: LlmBackendKind = LlmBackendKind.MOCK
    model: str = "claude-sonnet-5"
    api_key: str | None = None

    @classmethod
    def from_env(cls) -> LlmConfig:
        return cls(
            backend=_get_enum("LLM_BACKEND", LlmBackendKind, LlmBackendKind.MOCK),
            model=os.environ.get("LLM_MODEL", "claude-sonnet-5"),
            api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class PollerConfig:
    """Outbox poll-loop tuning. One instance per lane."""

    interval_seconds: float = 5.0
    job_type: str = "ingest"
    batch_size: int = 1

    @classmethod
    def for_lane(cls, lane: str) -> PollerConfig:
        prefix = lane.upper()
        return cls(
            interval_seconds=_get_float(f"{prefix}_POLL_INTERVAL_SECONDS", 5.0),
            job_type=os.environ.get(f"{prefix}_POLL_JOB_TYPE", lane),
            batch_size=_get_int(f"{prefix}_POLL_BATCH_SIZE", 1),
        )


@dataclass(frozen=True)
class Settings:
    """Top-level settings aggregate."""

    database: DatabaseConfig = field(default_factory=DatabaseConfig.from_env)
    storage: StorageConfig = field(default_factory=StorageConfig.from_env)
    market_data: MarketDataConfig = field(default_factory=MarketDataConfig.from_env)
    kronos: KronosConfig = field(default_factory=KronosConfig.from_env)
    llm: LlmConfig = field(default_factory=LlmConfig.from_env)
    ingest_poller: PollerConfig = field(default_factory=lambda: PollerConfig.for_lane("ingest"))
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database=DatabaseConfig.from_env(),
            storage=StorageConfig.from_env(),
            market_data=MarketDataConfig.from_env(),
            kronos=KronosConfig.from_env(),
            llm=LlmConfig.from_env(),
            ingest_poller=PollerConfig.for_lane("ingest"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )


def get_settings() -> Settings:
    """Build settings from the current environment (call once at startup)."""

    return Settings.from_env()
