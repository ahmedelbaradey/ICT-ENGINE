"""Configuration is env-only, typed, and defaults to the safe/mock posture."""

from __future__ import annotations

from pathlib import Path

import pytest

from ict_kronos.app.config import (
    CompositeIctConfig,
    DealingRangeConfig,
    KronosBackendKind,
    KronosConfig,
    LlmBackendKind,
    LlmConfig,
    MarketDataBackendKind,
    MarketDataConfig,
    PollerConfig,
    Settings,
    StorageConfig,
)

_ENV_VARS = (
    "MARKET_DATA_BACKEND",
    "KRONOS_BACKEND",
    "LLM_BACKEND",
    "DATA_ROOT",
    "DATABASE_URL",
    "ANTHROPIC_API_KEY",
    "KRONOS_WEIGHTS_DIR",
    "MARKET_DATA_MAX_GAP_BARS",
    "LOG_LEVEL",
    "ICT_OB_REQUIRE_FVG",
    "ICT_BREAKER_REQUIRE_STRUCTURE_BREAK",
    "ICT_RDRB_WICK_TOLERANCE_POINTS",
    "ICT_UNICORN_MAX_BARS_FROM_BREAKER",
    "ICT_UNICORN_MIN_OVERLAP_POINTS",
    "ICT_UNICORN_REQUIRE_FULL_CONTAINMENT",
    "ICT_EQUILIBRIUM_TOLERANCE_POINTS",
    "ICT_DEALING_RANGE_CLASSIFY_BARS",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


class TestSafeDefaults:
    def test_every_expensive_backend_defaults_to_mock(self):
        """CLAUDE.md rule 9 — the default posture must never reach the network or
        load multi-GB weights."""
        settings = Settings.from_env()
        assert settings.market_data.backend is MarketDataBackendKind.FIXTURE
        assert settings.kronos.backend is KronosBackendKind.MOCK
        assert settings.llm.backend is LlmBackendKind.MOCK

    def test_no_secret_is_baked_in(self):
        settings = Settings.from_env()
        assert settings.llm.api_key is None
        assert not settings.llm.is_configured

    def test_kronos_is_unconfigured_without_weights(self):
        assert not KronosConfig.from_env().is_configured

    def test_default_log_level(self):
        assert Settings.from_env().log_level == "INFO"


class TestEnvOverrides:
    def test_backend_selection(self, monkeypatch):
        monkeypatch.setenv("MARKET_DATA_BACKEND", "dukascopy")
        assert MarketDataConfig.from_env().backend is MarketDataBackendKind.DUKASCOPY

    def test_backend_parsing_is_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("KRONOS_BACKEND", "  LOCAL  ")
        assert KronosConfig.from_env().backend is KronosBackendKind.LOCAL

    def test_unknown_backend_falls_back_to_the_safe_default(self, monkeypatch):
        """A typo must degrade to the safe backend, not crash or silently go live."""
        monkeypatch.setenv("MARKET_DATA_BACKEND", "dukascpoy")
        assert MarketDataConfig.from_env().backend is MarketDataBackendKind.FIXTURE

    def test_numeric_override(self, monkeypatch):
        monkeypatch.setenv("MARKET_DATA_MAX_GAP_BARS", "12")
        assert MarketDataConfig.from_env().max_gap_bars == 12

    def test_blank_numeric_uses_the_default(self, monkeypatch):
        monkeypatch.setenv("MARKET_DATA_MAX_GAP_BARS", "   ")
        assert MarketDataConfig.from_env().max_gap_bars == 3

    def test_path_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_ROOT", str(tmp_path / "elsewhere"))
        storage = StorageConfig.from_env()
        assert storage.data_root == tmp_path / "elsewhere"
        assert storage.raw_root == tmp_path / "elsewhere" / "raw"
        assert storage.manifest_root == tmp_path / "elsewhere" / "manifests"

    def test_api_key_is_read_from_env_only(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        config = LlmConfig.from_env()
        assert config.is_configured
        assert config.api_key == "sk-test"


class TestImmutability:
    def test_settings_are_frozen(self):
        settings = Settings.from_env()
        with pytest.raises(Exception):  # noqa: B017 - dataclasses raise FrozenInstanceError
            settings.log_level = "DEBUG"

    def test_nested_configs_are_frozen(self):
        with pytest.raises(Exception):  # noqa: B017
            MarketDataConfig.from_env().max_gap_bars = 99


class TestPollerLanes:
    def test_lane_defaults_to_its_own_job_type(self):
        assert PollerConfig.for_lane("forecast").job_type == "forecast"

    def test_lane_reads_its_own_prefixed_env(self, monkeypatch):
        monkeypatch.setenv("FORECAST_POLL_INTERVAL_SECONDS", "0.5")
        monkeypatch.setenv("FORECAST_POLL_BATCH_SIZE", "8")
        config = PollerConfig.for_lane("forecast")
        assert config.interval_seconds == 0.5
        assert config.batch_size == 8

    def test_lanes_do_not_read_each_others_env(self, monkeypatch):
        """Two lanes sharing a poll interval by accident would be a silent
        misconfiguration."""
        monkeypatch.setenv("INGEST_POLL_BATCH_SIZE", "50")
        assert PollerConfig.for_lane("ingest").batch_size == 50
        assert PollerConfig.for_lane("forecast").batch_size == 1


class TestKronosContextGuard:
    def test_max_context_is_configurable_not_hardcoded(self, monkeypatch):
        """The 512-bar limit is an UNVERIFIED assumption from the Master Plan
        (see IMPLEMENTATION_ROADMAP Phase 5). It must stay configurable until it is
        confirmed against the upstream model card."""
        monkeypatch.setenv("KRONOS_MAX_CONTEXT_BARS", "256")
        assert KronosConfig.from_env().max_context_bars == 256

    def test_weights_dir_must_exist_to_count_as_configured(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KRONOS_WEIGHTS_DIR", str(tmp_path / "absent"))
        assert not KronosConfig.from_env().is_configured

        real = tmp_path / "present"
        real.mkdir()
        monkeypatch.setenv("KRONOS_WEIGHTS_DIR", str(real))
        config = KronosConfig.from_env()
        assert config.is_configured
        assert config.weights_dir == Path(real)


class TestCompositeIctDefaultsAreTheDefinitionOfRecord:
    """These defaults are not tuning knobs — each one encodes a decided semantic.

    Asserted here so a future edit that flips one has to argue with a test rather
    than slip through as a "reasonable default".
    """

    def test_an_order_block_never_silently_requires_a_gap(self):
        """56% of real EURUSD 5m Order Blocks have no FVG at all (R2-05.3 audit)."""
        assert CompositeIctConfig.from_env().ob_require_fvg is False

    def test_not_every_broken_order_block_is_a_breaker(self):
        assert CompositeIctConfig.from_env().breaker_require_structure_break is True

    def test_rdrb_equality_is_a_violation(self):
        """Tolerance 0 — C4 REACHING C2's protected wick violates it."""
        assert CompositeIctConfig.from_env().rdrb_wick_tolerance_points == 0.0

    def test_a_unicorn_needs_overlap_not_containment(self):
        config = CompositeIctConfig.from_env()
        assert config.unicorn_require_full_containment is False
        assert config.unicorn_max_bars_from_breaker == 50
        assert config.unicorn_min_overlap_points == 0.0


class TestCompositeIctEnvOverrides:
    def test_the_unicorn_window_is_env_overridable(self, monkeypatch):
        monkeypatch.setenv("ICT_UNICORN_MAX_BARS_FROM_BREAKER", "8")
        assert CompositeIctConfig.from_env().unicorn_max_bars_from_breaker == 8

    def test_the_unicorn_containment_qualifier_is_env_overridable(self, monkeypatch):
        monkeypatch.setenv("ICT_UNICORN_REQUIRE_FULL_CONTAINMENT", "true")
        assert CompositeIctConfig.from_env().unicorn_require_full_containment is True

    def test_the_minimum_overlap_is_env_overridable(self, monkeypatch):
        monkeypatch.setenv("ICT_UNICORN_MIN_OVERLAP_POINTS", "25.5")
        assert CompositeIctConfig.from_env().unicorn_min_overlap_points == 25.5

    def test_composite_config_is_frozen(self):
        config = CompositeIctConfig.from_env()
        with pytest.raises(Exception):  # noqa: B017 - FrozenInstanceError
            config.unicorn_max_bars_from_breaker = 3


class TestDealingRangeConfig:
    """R2-06. The range DEFINITION is deliberately absent from this surface."""

    def test_the_equilibrium_band_defaults_to_half_a_tick(self):
        """Finer than the instrument can express: numerically safe without being a
        claim about how wide an equilibrium zone should be."""
        assert DealingRangeConfig.from_env().equilibrium_tolerance_points == 0.5

    def test_bar_classification_is_on_by_default(self):
        assert DealingRangeConfig.from_env().classify_bars is True

    def test_the_tolerance_is_env_overridable(self, monkeypatch):
        monkeypatch.setenv("ICT_EQUILIBRIUM_TOLERANCE_POINTS", "25")
        assert DealingRangeConfig.from_env().equilibrium_tolerance_points == 25.0

    def test_classification_can_be_disabled_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("ICT_DEALING_RANGE_CLASSIFY_BARS", "false")
        assert DealingRangeConfig.from_env().classify_bars is False

    def test_there_is_no_range_definition_setting(self):
        """Four candidates were evaluated and one implemented. A knob here would make
        every downstream result ambiguous about which range produced it."""
        fields = set(DealingRangeConfig().__dataclass_fields__)
        assert fields == {"equilibrium_tolerance_points", "classify_bars"}

    def test_it_is_wired_into_settings(self):
        assert isinstance(Settings.from_env().dealing_range, DealingRangeConfig)

    def test_it_is_frozen(self):
        with pytest.raises(Exception):  # noqa: B017 - FrozenInstanceError
            DealingRangeConfig().classify_bars = False
