"""Backend selection — fixture (default) vs Dukascopy (live, opt-in).

Ported verbatim in spirit from ``Learnexia/python/curriculum_intelligence/parsers/factory.py``
(ADR-0001). The selection rule is identical, and it is the mechanism that keeps the
default test gate fast, offline and deterministic (CLAUDE.md rule 9):

- ``MARKET_DATA_BACKEND=fixture`` (default) → :class:`FixtureProvider`, dev + CI.
- ``MARKET_DATA_BACKEND=dukascopy`` → live :class:`DukascopyProvider`.
- ``MARKET_DATA_BACKEND=dukascopy`` but the cache directory cannot be created →
  fall back to the fixture backend **with a loud warning**, so a misconfigured
  deploy degrades rather than crashes.
"""

from __future__ import annotations

from ..app.config import MarketDataBackendKind, Settings
from ..app.logging import get_logger
from .base import MarketDataProvider
from .fixture_provider import FixtureProvider

logger = get_logger(__name__)


def build_market_data_provider(settings: Settings) -> MarketDataProvider:
    """Return the market-data backend selected by config."""

    config = settings.market_data

    if config.backend is MarketDataBackendKind.DUKASCOPY:
        try:
            config.download_cache.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "MARKET_DATA_BACKEND=dukascopy but the download cache %s is unusable (%s); "
                "falling back to the fixture provider",
                config.download_cache,
                exc,
            )
        else:
            # Imported here so the live module's optional deps are only touched when selected.
            from .dukascopy import DukascopyProvider

            logger.info("market data backend = dukascopy (live), cache=%s", config.download_cache)
            return DukascopyProvider(
                base_url=config.dukascopy_base_url,
                cache_dir=config.download_cache,
                timeout_seconds=config.request_timeout_seconds,
            )

    logger.info("market data backend = fixture (default), root=%s", config.fixture_root)
    return FixtureProvider(config.fixture_root)
