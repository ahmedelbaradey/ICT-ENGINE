"""Command-line entry point for the Phase 1 ingest lane.

Deliberately thin: it parses arguments, builds settings, and delegates. All logic
lives in tested modules — a CLI that contains logic is a CLI whose logic is untested.

Usage::

    python -m ict_kronos.cli ingest --symbol EURUSD --symbol XAUUSD \\
        --timeframe 5m --start 2024-03-04 --end 2024-03-05 --version v1

    python -m ict_kronos.cli verify --version v1
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from .app.config import get_settings
from .app.logging import configure_logging, get_logger
from .data import IngestPipeline, build_market_data_provider
from .domain import Symbol, Timeframe
from .storage import ManifestStore

logger = get_logger(__name__)


def _parse_date(raw: str) -> datetime:
    """Parse a CLI date/datetime as UTC.

    A bare date means midnight UTC. Naive input is interpreted as UTC rather than
    rejected, because at the CLI boundary there is no other sensible reading — but
    everything downstream of here is strictly timezone-aware.
    """
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid date {raw!r}; expected ISO-8601 (2024-03-04 or 2024-03-04T12:00:00Z)"
        ) from exc
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ict-kronos", description="ICT-Kronos research platform")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="fetch, normalize and persist market data")
    ingest.add_argument("--symbol", action="append", required=True, help="repeatable, e.g. EURUSD")
    ingest.add_argument("--timeframe", action="append", required=True, help="repeatable, e.g. 5m")
    ingest.add_argument("--start", type=_parse_date, required=True)
    ingest.add_argument("--end", type=_parse_date, required=True, help="exclusive")
    ingest.add_argument("--version", required=True, help="dataset version — immutable once written")
    ingest.add_argument(
        "--overwrite",
        action="store_true",
        help="replace existing partitions; breaks reproducibility of published results",
    )

    verify = sub.add_parser("verify", help="re-hash a dataset version against its manifest")
    verify.add_argument("--version", required=True)

    return parser


def command_ingest(args: argparse.Namespace) -> int:
    settings = get_settings()
    provider = build_market_data_provider(settings)
    pipeline = IngestPipeline(provider, settings)

    pairs = [(Symbol.from_string(s), Timeframe.from_string(t)) for s in args.symbol for t in args.timeframe]

    result = pipeline.run(pairs, args.start, args.end, dataset_version=args.version, overwrite=args.overwrite)

    print(f"dataset      : {result.dataset_version}")
    print(f"provider     : {provider.name}")
    print(f"partitions   : {len(result.partitions)}")
    print(f"rows         : {result.total_rows}")
    print(f"manifest     : {result.manifest_path}")

    for report in result.reports:
        print(
            f"  {report.symbol}/{report.timeframe}: {report.output_rows} bars, "
            f"{report.duplicates_removed} dupes, {report.invalid_removed} invalid, "
            f"{report.missing_bars} missing across {len(report.gaps)} gap(s) "
            f"({len(report.significant_gaps)} significant)"
        )

    for failure in result.failures:
        print(f"  FAILED {failure}", file=sys.stderr)

    return 0 if result.succeeded else 1


def command_verify(args: argparse.Namespace) -> int:
    settings = get_settings()
    problems = ManifestStore(settings.storage.manifest_root).verify(args.version)

    if not problems:
        print(f"{args.version}: OK - every partition matches its recorded hash")
        return 0

    print(f"{args.version}: {len(problems)} problem(s)", file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(get_settings().log_level)

    if args.command == "ingest":
        return command_ingest(args)
    if args.command == "verify":
        return command_verify(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
