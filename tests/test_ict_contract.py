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
