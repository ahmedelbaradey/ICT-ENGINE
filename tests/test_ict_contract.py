"""The shared Phase 2 detector event contract.

These invariants are enforced once, here, so every later detector inherits them
rather than re-deriving (or quietly weakening) them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from ict_kronos.ict import (
    ContractViolation,
    Direction,
    EventStatus,
    EventType,
    IctEvent,
    assert_no_leakage,
    events_to_frame,
)

T0 = datetime(2024, 3, 8, 9, 0, tzinfo=UTC)


def make_event(**overrides) -> IctEvent:
    payload = {
        "symbol": "EURUSD",
        "timeframe": "5m",
        "event_type": EventType.SWING_HIGH,
        "direction": Direction.BULLISH,
        "event_timestamp": T0,
        "confirmation_timestamp": T0 + timedelta(minutes=15),
        "price_level": 1.0850,
    }
    payload.update(overrides)
    return IctEvent(**payload)


class TestContractInvariants:
    def test_valid_event_constructs(self):
        event = make_event()
        assert event.price_level == 1.0850
        assert event.status is EventStatus.ACTIVE

    def test_confirmation_may_not_precede_the_event(self):
        """The invariant that makes look-ahead structurally impossible to express."""
        with pytest.raises(ContractViolation, match="precedes"):
            make_event(confirmation_timestamp=T0 - timedelta(minutes=5))

    def test_simultaneous_confirmation_is_allowed(self):
        """Legitimate: an event confirmed by its own bar's close."""
        event = make_event(confirmation_timestamp=T0)
        assert event.confirmation_lag == timedelta(0)

    def test_naive_event_timestamp_is_rejected(self):
        with pytest.raises(ContractViolation, match="timezone-aware"):
            make_event(event_timestamp=datetime(2024, 3, 8, 9, 0))  # noqa: DTZ001

    def test_naive_confirmation_timestamp_is_rejected(self):
        with pytest.raises(ContractViolation, match="timezone-aware"):
            make_event(confirmation_timestamp=datetime(2024, 3, 8, 9, 30))  # noqa: DTZ001

    def test_events_are_immutable(self):
        with pytest.raises(Exception):  # noqa: B017 - FrozenInstanceError
            make_event().price_level = 2.0


class TestObservability:
    def test_not_observable_before_confirmation(self):
        event = make_event()
        assert not event.is_observable_at(T0)
        assert not event.is_observable_at(T0 + timedelta(minutes=14))

    def test_observable_exactly_at_confirmation(self):
        """The information IS known at that instant."""
        event = make_event()
        assert event.is_observable_at(T0 + timedelta(minutes=15))

    def test_observable_after_confirmation(self):
        assert make_event().is_observable_at(T0 + timedelta(hours=5))

    def test_naive_as_of_is_rejected(self):
        with pytest.raises(ContractViolation, match="timezone-aware"):
            make_event().is_observable_at(datetime(2024, 3, 8, 10, 0))  # noqa: DTZ001

    def test_confirmation_lag_is_reported(self):
        assert make_event().confirmation_lag == timedelta(minutes=15)


class TestSerialization:
    def test_as_dict_has_every_contract_field(self):
        payload = make_event().as_dict()
        for key in (
            "symbol",
            "timeframe",
            "event_type",
            "direction",
            "event_timestamp",
            "confirmation_timestamp",
            "price_level",
            "reference_level",
            "strength",
        ):
            assert key in payload

    def test_timestamps_serialise_as_iso_utc(self):
        payload = make_event().as_dict()
        assert payload["event_timestamp"].endswith("+00:00")
        assert payload["confirmation_timestamp"].endswith("+00:00")

    def test_events_to_frame_keeps_real_datetimes(self):
        frame = events_to_frame([make_event(), make_event(event_type=EventType.SWING_LOW)])
        assert len(frame) == 2
        assert isinstance(frame["event_timestamp"].dtype, pd.DatetimeTZDtype)
        assert str(frame["confirmation_timestamp"].dtype.tz) == "UTC"

    def test_empty_frame_keeps_the_schema(self):
        frame = events_to_frame([])
        assert len(frame) == 0
        assert "confirmation_timestamp" in frame.columns


class TestLeakageHelper:
    def test_passes_on_valid_events(self):
        assert_no_leakage([make_event(), make_event(event_type=EventType.BOS)])

    def test_empty_list_passes(self):
        assert_no_leakage([])


class TestEventTypeCoverage:
    def test_every_phase_2_concept_has_an_event_type(self):
        """Declared up front so R2-07's encoding stays stable as detectors land."""
        expected = {
            "session_high",
            "session_low",
            "session_open",
            "session_close",
            "swing_high",
            "swing_low",
            "higher_high",
            "higher_low",
            "lower_high",
            "lower_low",
            "bos",
            "mss",
            "choch",
            "equal_highs",
            "equal_lows",
            "previous_day_high",
            "previous_day_low",
            "previous_week_high",
            "previous_week_low",
            "liquidity_sweep",
            "fvg_bullish",
            "fvg_bearish",
            "true_daily_open",
            "ifvg_bullish",
            "ifvg_bearish",
            "order_block_bullish",
            "order_block_bearish",
            "breaker_bullish",
            "breaker_bearish",
            "balanced_price_range",
            "rdrb_bullish",
            "rdrb_bearish",
            "cisd_bullish",
            "cisd_bearish",
            "unicorn_bullish",
            "unicorn_bearish",
            "dealing_range",
        }
        assert {e.value for e in EventType} == expected

    def test_the_true_daily_open_is_not_a_trading_day_boundary(self):
        """R2-05.1 (00:00 NY) and R2-04's day rollover (17:00 NY) are distinct concepts.

        Declared side by side here because collapsing them is the likely future
        mistake: both are "daily", and only one is a price level.
        """
        assert EventType.TRUE_DAILY_OPEN not in {
            EventType.PREVIOUS_DAY_HIGH,
            EventType.PREVIOUS_DAY_LOW,
            EventType.SESSION_OPEN,
        }
        assert EventType.TRUE_DAILY_OPEN.value == "true_daily_open"

    def test_direction_neutral_is_a_real_value(self):
        event = make_event(event_type=EventType.DEALING_RANGE, direction=Direction.NEUTRAL)
        assert event.direction is Direction.NEUTRAL


class TestTheSingleObservabilityGateEngineWide:
    """Every detector in the engine must route observability through the contract.

    The R2-04 audit established this rule and fixed ``liquidity.py``; the R2-05.2 audit
    found ``structure.py`` (3 sites) and ``swings.py`` (1 site) had never been swept.
    This guard covers **every** module in ``ict_kronos/ict`` so the next one cannot be
    missed either — five private copies of a rule are five places it can silently drift.
    """

    #: The one module allowed raw confirmation arithmetic, in two named helpers whose
    #: question is WINDOWING ("did this confirm inside the leg?"), not observability.
    OWNERS = {"contract.py", "composites.py"}

    def _modules(self):
        from pathlib import Path as _P

        return sorted(_P("ict_kronos/ict").glob("*.py"))

    @staticmethod
    def _code_lines(module) -> list[str]:
        """Executable lines only — comments AND docstrings stripped.

        ``market_state.py`` states in its docstring that it contains no
        ``confirmation_timestamp <= as_of`` comparison, precisely so a reader knows the
        rule. A guard that scanned raw text would flag that promise as a violation of
        itself. Stripping docstrings is what makes this a guard on CODE.
        """
        from tests.test_market_state import _code_of

        return _code_of(str(module)).splitlines()

    @staticmethod
    def _is_gate_line(line: str) -> bool:
        """A hand-rolled observability gate: a confirmation COMPARED against a decision time."""
        return (
            "confirmation_timestamp" in line
            and "as_of" in line
            and "is_observable_at" not in line
            and any(op in line.replace("->", "") for op in ("<", ">", "=="))
        )

    def test_the_reversed_spelling_is_still_caught(self):
        """Proof the operator requirement narrowed the guard without blinding it."""
        assert self._is_gate_line("if as_of >= event.confirmation_timestamp:")
        assert self._is_gate_line("ok = event.confirmation_timestamp <= as_of")
        assert self._is_gate_line("hit = as_of == event.confirmation_timestamp")
        assert not self._is_gate_line("n = self._bars_since(e.confirmation_timestamp, as_of)")
        assert not self._is_gate_line("def _f(self, as_of: datetime) -> int | None:")

    def test_the_guard_actually_reads_code(self):
        """A stripper returning nothing would make both guards below vacuous."""
        by_name = {m.name: m for m in self._modules()}
        code = self._code_lines(by_name["market_state.py"])
        assert any("def state_at(" in line for line in code)
        assert not any('"""' in line for line in code)

    def test_no_detector_hand_rolls_the_observability_comparison(self):
        offenders = {}
        for module in self._modules():
            if module.name in self.OWNERS:
                continue
            code = self._code_lines(module)
            hits = [
                line
                for line in code
                if "confirmation_timestamp <=" in line
                or "confirmation_timestamp >=" in line
                or "confirmation_timestamp <" in line
            ]
            if hits:
                offenders[module.name] = hits

        assert offenders == {}, (
            "these modules re-implement the observability rule instead of calling the "
            f"shared gate: {offenders}"
        )

    def test_no_detector_compares_a_confirmation_against_an_as_of(self):
        """The tight form: an observability check is confirmation COMPARED to a decision time.

        This catches the reversed spelling (``as_of >= e.confirmation_timestamp``) that the
        guard above cannot see, because there the operator precedes the attribute.

        A comparison operator is required. Naming both on one line is not a gate —
        ``_bars_since(e.confirmation_timestamp, as_of)`` *measures* the distance between two
        instants for an event the detector already admitted; banning that would ban
        arithmetic rather than the rule it is meant to protect.
        """
        offenders = {}
        for module in self._modules():
            if module.name in self.OWNERS:
                continue
            hits = [line for line in self._code_lines(module) if self._is_gate_line(line)]
            if hits:
                offenders[module.name] = hits

        assert offenders == {}, f"the observability rule is re-implemented: {offenders}"

    def test_the_gate_is_actually_reachable_from_every_point_in_time_api(self):
        """Behavioural companion: the R2-02/R2-03 APIs that were fixed still filter."""
        from datetime import UTC, datetime, timedelta

        from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame
        from ict_kronos.ict import StructureDetector, SwingDetector

        start = datetime(2024, 3, 8, 9, 0, tzinfo=UTC)
        prices = [1.0, 1.2, 1.1, 1.4, 1.3, 1.6, 1.2, 1.5, 1.1, 1.7, 1.0, 1.8]
        frame = candles_to_frame(
            [
                MarketCandle(
                    timestamp=start + timedelta(minutes=5 * i),
                    symbol=Symbol.EURUSD,
                    timeframe=Timeframe.M5,
                    open=p,
                    high=p + 0.05,
                    low=p - 0.05,
                    close=p,
                    volume=1.0,
                )
                for i, p in enumerate(prices)
            ]
        )
        swings = SwingDetector().detect(frame, Symbol.EURUSD, Timeframe.M5)
        assert swings, "fixture must produce swings for this test to mean anything"

        as_of = swings[0].confirmation_timestamp
        visible = SwingDetector().observable_at(frame, as_of, Symbol.EURUSD, Timeframe.M5)
        assert all(s.confirmation_timestamp <= as_of for s in visible)
        assert len(visible) < len(swings) or len(swings) == 1

        limited = StructureDetector().observable_at(frame, as_of, Symbol.EURUSD, Timeframe.M5)
        assert all(b.confirmation_timestamp <= as_of for b in limited.breaks)

    def test_a_naive_as_of_is_still_rejected_by_the_fixed_apis(self):
        """The gate raises ContractViolation, which IS a ValueError — behaviour kept."""
        from datetime import datetime

        from ict_kronos.ict import ContractViolation, StructureAnalysis

        naive = datetime(2024, 3, 8, 12, 0)  # noqa: DTZ001
        with pytest.raises((ContractViolation, ValueError), match="timezone-aware"):
            StructureAnalysis().state_at(naive)
