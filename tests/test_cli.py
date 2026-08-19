"""CLI argument parsing and command wiring."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ict_kronos.cli import _parse_date, build_parser, main

from .conftest import FIXTURE_BAR_COUNT


@pytest.fixture(autouse=True)
def cli_env(tmp_path, fixture_root, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("MARKET_DATA_FIXTURE_ROOT", str(fixture_root))
    monkeypatch.setenv("MARKET_DATA_BACKEND", "fixture")


class TestParseDate:
    def test_bare_date_becomes_midnight_utc(self):
        assert _parse_date("2024-03-04") == datetime(2024, 3, 4, tzinfo=UTC)

    def test_explicit_offset_is_converted_to_utc(self):
        assert _parse_date("2024-03-04T02:00:00+02:00") == datetime(2024, 3, 4, 0, 0, tzinfo=UTC)

    def test_z_suffix_is_accepted(self):
        assert _parse_date("2024-03-04T12:00:00Z") == datetime(2024, 3, 4, 12, tzinfo=UTC)

    def test_garbage_is_rejected(self):
        import argparse

        with pytest.raises(argparse.ArgumentTypeError, match="invalid date"):
            _parse_date("last tuesday")


class TestParser:
    def test_symbol_and_timeframe_are_repeatable(self):
        args = build_parser().parse_args(
            [
                "ingest",
                "--symbol",
                "EURUSD",
                "--symbol",
                "XAUUSD",
                "--timeframe",
                "5m",
                "--start",
                "2024-03-04",
                "--end",
                "2024-03-05",
                "--version",
                "v1",
            ]
        )
        assert args.symbol == ["EURUSD", "XAUUSD"]
        assert args.timeframe == ["5m"]

    def test_overwrite_defaults_off(self):
        args = build_parser().parse_args(
            [
                "ingest",
                "--symbol",
                "EURUSD",
                "--timeframe",
                "5m",
                "--start",
                "2024-03-04",
                "--end",
                "2024-03-05",
                "--version",
                "v1",
            ]
        )
        assert args.overwrite is False

    def test_command_is_required(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args([])


class TestIngestCommand:
    def test_successful_run_returns_zero(self, capsys):
        code = main(
            [
                "ingest",
                "--symbol",
                "EURUSD",
                "--timeframe",
                "5m",
                "--start",
                "2024-03-04",
                "--end",
                "2024-03-05",
                "--version",
                "v1",
            ]
        )
        assert code == 0
        out = capsys.readouterr().out
        assert f"rows         : {FIXTURE_BAR_COUNT}" in out
        assert "EURUSD/5m" in out

    def test_failed_pair_returns_nonzero(self, capsys):
        code = main(
            [
                "ingest",
                "--symbol",
                "EURUSD",
                "--timeframe",
                "4h",
                "--start",
                "2024-03-04",
                "--end",
                "2024-03-05",
                "--version",
                "v1",
            ]
        )
        assert code == 1
        assert "FAILED" in capsys.readouterr().err

    def test_unknown_symbol_is_rejected(self):
        with pytest.raises(ValueError, match="unknown symbol"):
            main(
                [
                    "ingest",
                    "--symbol",
                    "GBPUSD",
                    "--timeframe",
                    "5m",
                    "--start",
                    "2024-03-04",
                    "--end",
                    "2024-03-05",
                    "--version",
                    "v1",
                ]
            )


class TestVerifyCommand:
    def test_verify_passes_after_ingest(self, capsys):
        main(
            [
                "ingest",
                "--symbol",
                "EURUSD",
                "--timeframe",
                "5m",
                "--start",
                "2024-03-04",
                "--end",
                "2024-03-05",
                "--version",
                "v1",
            ]
        )
        capsys.readouterr()

        assert main(["verify", "--version", "v1"]) == 0
        assert "OK" in capsys.readouterr().out

    def test_verify_reports_missing_partitions(self, capsys, tmp_path):
        main(
            [
                "ingest",
                "--symbol",
                "EURUSD",
                "--timeframe",
                "5m",
                "--start",
                "2024-03-04",
                "--end",
                "2024-03-05",
                "--version",
                "v1",
            ]
        )
        capsys.readouterr()

        for parquet in (tmp_path / "data" / "normalized").rglob("*.parquet"):
            parquet.unlink()

        assert main(["verify", "--version", "v1"]) == 1
        assert "MISSING:" in capsys.readouterr().err
