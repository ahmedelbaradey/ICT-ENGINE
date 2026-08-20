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
class SessionConfig:
    """Trading-session definitions (R2-01).

    CLAUDE.md rule 4 — session boundaries are configuration, never literals inside
    detector logic. ``ICT_SESSIONS_JSON`` accepts either an inline JSON array or a
    path to a JSON file, each element being::

        {"name": "london", "timezone": "Europe/London",
         "start_local": "08:00", "end_local": "16:30", "kind": "session"}

    Times are **local to the named timezone**, never UTC — that is what makes DST
    handling automatic. Unset means the documented defaults in
    ``ict/sessions.py`` / ``docs/ict/sessions.md``.
    """

    definitions_json: str | None = None

    @classmethod
    def from_env(cls) -> SessionConfig:
        return cls(definitions_json=os.environ.get("ICT_SESSIONS_JSON") or None)

    @property
    def is_overridden(self) -> bool:
        return self.definitions_json is not None


@dataclass(frozen=True)
class SwingDetectionConfig:
    """Fractal swing parameters (R2-02).

    ``left``/``right`` are BAR COUNTS. ``right`` is the confirmation lag: a swing at
    bar *i* is not knowable until bar ``i + right`` closes. It must be >= 1 — a
    zero-lag pivot is not a swing.

    ``tie_policy`` resolves plateaus (consecutive bars sharing the extreme):
    ``first`` | ``last`` | ``strict`` | ``all`` — see ``docs/ict/swings.md``.
    """

    left: int = 2
    right: int = 2
    tie_policy: str = "first"

    @classmethod
    def from_env(cls) -> SwingDetectionConfig:
        return cls(
            left=_get_int("ICT_SWING_LEFT", 2),
            right=_get_int("ICT_SWING_RIGHT", 2),
            tie_policy=os.environ.get("ICT_SWING_TIE_POLICY", "first").strip().lower(),
        )


@dataclass(frozen=True)
class StructureDetectionConfig:
    """Market-structure parameters (R2-03). See ``docs/ict/structure.md``.

    ``break_mode`` defaults to ``close`` because a wick break fires on every stop-run,
    which R2-04 models as a liquidity SWEEP rather than a structural break.

    ``choch_policy`` defaults to ``synonym``: CHoCH and MSS are the same event and only
    MSS is emitted. Stated explicitly rather than faked into two code paths.
    """

    break_mode: str = "close"
    break_tolerance_points: float = 0.0
    equal_level_tolerance_points: float = 0.0
    min_swing_strength_points: float = 0.0
    choch_policy: str = "synonym"
    displacement_lookback: int = 20
    displacement_factor: float = 1.5

    @classmethod
    def from_env(cls) -> StructureDetectionConfig:
        return cls(
            break_mode=os.environ.get("ICT_STRUCTURE_BREAK_MODE", "close").strip().lower(),
            break_tolerance_points=_get_float("ICT_STRUCTURE_BREAK_TOLERANCE_POINTS", 0.0),
            equal_level_tolerance_points=_get_float("ICT_STRUCTURE_EQUAL_TOLERANCE_POINTS", 0.0),
            min_swing_strength_points=_get_float("ICT_STRUCTURE_MIN_SWING_STRENGTH", 0.0),
            choch_policy=os.environ.get("ICT_STRUCTURE_CHOCH_POLICY", "synonym").strip().lower(),
            displacement_lookback=_get_int("ICT_STRUCTURE_DISPLACEMENT_LOOKBACK", 20),
            displacement_factor=_get_float("ICT_STRUCTURE_DISPLACEMENT_FACTOR", 1.5),
        )


@dataclass(frozen=True)
class LiquidityDetectionConfig:
    """Liquidity parameters (R2-04). See ``docs/ict/liquidity.md``.

    ``day_timezone``/``day_boundary_local`` define the TRADING day, not the UTC
    calendar day. The default 17:00 America/New_York is the FX/broker day and matches
    the instrument reopen times observed in the Phase 1.5 data.
    """

    equal_tolerance_points: float = 1.0
    equal_max_swing_distance: int = 1
    sweep_tolerance_points: float = 0.0
    require_rejection: bool = False
    approach_tolerance_points: float = 0.0
    day_timezone: str = "America/New_York"
    day_boundary_local: str = "17:00"
    include_swing_levels: bool = True

    @classmethod
    def from_env(cls) -> LiquidityDetectionConfig:
        return cls(
            equal_tolerance_points=_get_float("ICT_LIQUIDITY_EQUAL_TOLERANCE_POINTS", 1.0),
            equal_max_swing_distance=_get_int("ICT_LIQUIDITY_EQUAL_MAX_SWING_DISTANCE", 1),
            sweep_tolerance_points=_get_float("ICT_LIQUIDITY_SWEEP_TOLERANCE_POINTS", 0.0),
            require_rejection=_get_bool("ICT_LIQUIDITY_REQUIRE_REJECTION", False),
            approach_tolerance_points=_get_float("ICT_LIQUIDITY_APPROACH_TOLERANCE_POINTS", 0.0),
            day_timezone=os.environ.get("ICT_LIQUIDITY_DAY_TIMEZONE", "America/New_York"),
            day_boundary_local=os.environ.get("ICT_LIQUIDITY_DAY_BOUNDARY_LOCAL", "17:00"),
            include_swing_levels=_get_bool("ICT_LIQUIDITY_INCLUDE_SWING_LEVELS", True),
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
    sessions: SessionConfig = field(default_factory=SessionConfig.from_env)
    swings: SwingDetectionConfig = field(default_factory=SwingDetectionConfig.from_env)
    structure: StructureDetectionConfig = field(default_factory=StructureDetectionConfig.from_env)
    liquidity: LiquidityDetectionConfig = field(default_factory=LiquidityDetectionConfig.from_env)
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
            sessions=SessionConfig.from_env(),
            swings=SwingDetectionConfig.from_env(),
            structure=StructureDetectionConfig.from_env(),
            liquidity=LiquidityDetectionConfig.from_env(),
            kronos=KronosConfig.from_env(),
            llm=LlmConfig.from_env(),
            ingest_poller=PollerConfig.for_lane("ingest"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )


def get_settings() -> Settings:
    """Build settings from the current environment (call once at startup)."""

    return Settings.from_env()
