"""Market-data ingestion: providers, normalization, resampling, ingest pipeline."""

from .base import MarketDataError, MarketDataProvider, require_utc
from .coverage import (
    BarCoverage,
    BarQuality,
    CoverageReport,
    GapCause,
    SessionProfile,
    coverage_report,
)
from .factory import build_market_data_provider
from .fixture_provider import FixtureProvider
from .ingest import IngestPipeline, IngestResult
from .normalizer import DataNormalizer, Gap, NormalizationReport
from .resampler import (
    RESAMPLED_COLUMNS,
    ResampleError,
    align_htf_context,
    build_timeframe_stack,
    latest_closed_bar,
    resample,
    with_close_time,
)

__all__ = [
    "RESAMPLED_COLUMNS",
    "BarCoverage",
    "BarQuality",
    "CoverageReport",
    "DataNormalizer",
    "GapCause",
    "FixtureProvider",
    "Gap",
    "IngestPipeline",
    "IngestResult",
    "MarketDataError",
    "MarketDataProvider",
    "NormalizationReport",
    "ResampleError",
    "SessionProfile",
    "align_htf_context",
    "build_market_data_provider",
    "build_timeframe_stack",
    "coverage_report",
    "latest_closed_bar",
    "require_utc",
    "resample",
    "with_close_time",
]
