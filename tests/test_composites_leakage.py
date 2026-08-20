"""R2-05.2 leakage, replay and provenance — applied uniformly to all six detectors.

Written once and parametrised rather than copied six times: the whole point of the
shared composite machinery is that these properties hold *by construction*, so they
should be expressible as one set of tests over a table of detectors.

The six proofs, per the concept map:

    L1  future-bar mutation      appending/wrecking later bars changes nothing
    L2  boundary mutation        + a CONTROL that the read field DOES matter
    L3  prefix equivalence       detect(prefix) == filter_observable(detect(full), t)
    L4  naive divergence         per-detector, in each detector's own test file
    L5  timestamp invariant      confirmation >= event, enforced by the contract
    L6  provenance invariant     ids resolve, and sources confirm no later
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ict_kronos.domain import MarketCandle, Symbol, Timeframe, candles_to_frame
from ict_kronos.ict import (
    BprDetector,
    BreakerConfig,
    BreakerDetector,
    CisdDetector,
    ContractViolation,
    FvgDetector,
    IfvgDetector,
    OrderBlockDetector,
    RdrbDetector,
    assert_provenance_resolves,
    assert_sources_observable_first,
    composite_confirmation,
    filter_observable,
)

START = datetime(2024, 3, 8, 9, 0, tzinfo=UTC)
M5 = Timeframe.M5
SYM = Symbol.EURUSD

#: The six detector modules. Neither of them may own confirmation arithmetic.
DETECTOR_MODULES = (
    "ict_kronos/ict/ifvg.py",
    "ict_kronos/ict/order_blocks.py",
    "ict_kronos/ict/breakers.py",
    "ict_kronos/ict/bpr.py",
    "ict_kronos/ict/rdrb.py",
    "ict_kronos/ict/cisd.py",
)

#: Every module added by this story, including the one that DOES own the primitives.
NEW_MODULES = (
    "ict_kronos/ict/composites.py",
    "ict_kronos/ict/ifvg.py",
    "ict_kronos/ict/order_blocks.py",
    "ict_kronos/ict/breakers.py",
    "ict_kronos/ict/bpr.py",
    "ict_kronos/ict/rdrb.py",
    "ict_kronos/ict/cisd.py",
)


def walk(n: int = 240) -> list[tuple[float, float, float, float]]:
    """A deterministic pseudo-random walk rich enough to trigger every detector.

    A fixed linear congruential generator rather than ``random`` — the series must be
    identical on every run and on every machine, or a leakage failure would be
    unreproducible.
    """
    spec: list[tuple[float, float, float, float]] = []
    price = 1.1000
    seed = 20240308
    for _ in range(n):
        seed = (seed * 1103515245 + 12345) % (2**31)
        drift = ((seed >> 16) % 201 - 100) / 100_000.0
        seed = (seed * 1103515245 + 12345) % (2**31)
        wick = ((seed >> 16) % 60) / 100_000.0

        open_ = price
        close = round(price + drift, 6)
        high = round(max(open_, close) + wick, 6)
        low = round(min(open_, close) - wick, 6)
        spec.append((open_, high, low, close))
        price = close
    return spec


def bars(spec, *, start=START, timeframe=M5):
    candles = [
        MarketCandle(
            timestamp=start + timedelta(minutes=timeframe.minutes * i),
            symbol=SYM,
            timeframe=timeframe,
            open=o,
            high=h,
            low=low,
            close=c,
            volume=1.0,
        )
        for i, (o, h, low, c) in enumerate(spec)
    ]
    return candles_to_frame(candles)


#: ``(name, detector, attribute holding the confirmed records)``.
DETECTORS = [
    ("ifvg", IfvgDetector(), None),
    ("order_block", OrderBlockDetector(), None),
    ("breaker", BreakerDetector(BreakerConfig(require_structure_break=False)), None),
    ("bpr", BprDetector(), None),
    ("rdrb", RdrbDetector(), None),
    ("cisd", CisdDetector(), None),
]


@pytest.fixture(scope="module")
def frame():
    return bars(walk())


@pytest.fixture(params=DETECTORS, ids=lambda d: d[0])
def detector(request):
    return request.param[1]


@pytest.fixture(params=DETECTORS, ids=lambda d: d[0])
def named_detector(request):
    return request.param[0], request.param[1]


class TestTheFixtureIsUseful:
    def test_every_detector_finds_something(self, named_detector, frame):
        """A leakage suite over empty result sets proves nothing."""
        name, det = named_detector
        assert det.detect(frame, SYM, M5), f"{name} found nothing — the fixture is inadequate"


class TestL1FutureBarMutation:
    def test_wrecking_later_bars_changes_nothing(self, detector, frame):
        events = detector.detect(frame, SYM, M5)
        cutoff = events[len(events) // 2].confirmation_timestamp

        mutated = frame.copy()
        later = mutated["timestamp"] > cutoff
        mutated.loc[later, "high"] = mutated.loc[later, "high"] * 3
        mutated.loc[later, "low"] = mutated.loc[later, "low"] / 3
        mutated.loc[later, "close"] = mutated.loc[later, "close"] * 2
        mutated.loc[later, "volume"] = 1e9

        before = [e for e in events if e.confirmation_timestamp <= cutoff]
        after = [e for e in detector.detect(mutated, SYM, M5) if e.confirmation_timestamp <= cutoff]
        assert after == before

    def test_appending_bars_cannot_alter_existing_events(self, detector, frame):
        half = len(frame) // 2
        early = detector.detect(frame.iloc[:half], SYM, M5)
        late = detector.detect(frame, SYM, M5)

        assert early == late[: len(early)]

    def test_appended_records_are_byte_identical(self, detector, frame):
        half = len(frame) // 2
        early = detector.detect(frame.iloc[:half], SYM, M5)
        late = detector.detect(frame, SYM, M5)

        for a, b in zip(early, late, strict=False):
            assert a.as_dict() == b.as_dict()


class TestL2BoundaryMutationWithControl:
    def test_mutating_the_final_unclosed_bar_adds_nothing_retroactively(self, detector, frame):
        """The last bar may add NEW events; it must never change older ones."""
        events = detector.detect(frame, SYM, M5)
        boundary = frame["timestamp"].iloc[-1].to_pydatetime()

        mutated = frame.copy()
        last = mutated["timestamp"] == boundary
        mutated.loc[last, "high"] = 9.0
        mutated.loc[last, "low"] = 0.5

        after = detector.detect(mutated, SYM, M5)
        untouched = [e for e in events if e.confirmation_timestamp <= boundary]
        assert [e for e in after if e.confirmation_timestamp <= boundary] == untouched

    def test_the_control_mutating_price_broadly_DOES_change_results(self, named_detector, frame):
        """Without this, the mutation tests above could pass on a detector that
        ignores prices entirely."""
        name, det = named_detector
        before = det.detect(frame, SYM, M5)

        mutated = frame.copy()
        for column in ("open", "high", "low", "close"):
            mutated[column] = mutated[column] * 1.05

        assert det.detect(mutated, SYM, M5) != before, f"{name} appears insensitive to price"


class TestL3PrefixEquivalence:
    def test_batch_equals_prefix_replay(self, detector, frame):
        full = detector.detect(frame, SYM, M5)

        for cut in (len(frame) // 4, len(frame) // 2, 3 * len(frame) // 4, len(frame)):
            prefix_frame = frame.iloc[:cut]
            as_of = prefix_frame["timestamp"].iloc[-1].to_pydatetime() + M5.duration

            assert detector.detect(prefix_frame, SYM, M5) == filter_observable(full, as_of)

    def test_batch_equals_bar_by_bar_streaming(self, detector, frame):
        """True replay over a shorter window — one detect() call per bar."""
        window = frame.iloc[:80]
        streamed: list = []
        for cut in range(1, len(window) + 1):
            for event in detector.detect(window.iloc[:cut], SYM, M5):
                if event not in streamed:
                    streamed.append(event)

        assert streamed == detector.detect(window, SYM, M5)

    def test_no_partially_formed_event_is_ever_emitted(self, detector, frame):
        """Every emitted event's confirmation lies within the data that produced it."""
        for cut in range(4, len(frame), 37):
            prefix = frame.iloc[:cut]
            horizon = prefix["timestamp"].iloc[-1].to_pydatetime() + M5.duration
            for event in detector.detect(prefix, SYM, M5):
                assert event.confirmation_timestamp <= horizon


class TestL5TimestampInvariant:
    def test_confirmation_never_precedes_the_event(self, detector, frame):
        for event in detector.detect(frame, SYM, M5):
            assert event.confirmation_timestamp >= event.event_timestamp

    def test_contract_events_are_constructible_and_ordered(self, detector, frame):
        events = detector.events(frame, SYM, M5)
        stamps = [e.confirmation_timestamp for e in events]
        assert stamps == sorted(stamps)


class TestL6Provenance:
    def test_ifvg_sources_resolve_and_confirm_first(self, frame):
        zones = IfvgDetector().detect(frame, SYM, M5)
        registry = {z.zone_id: z for z in FvgDetector().detect(frame, SYM, M5)}

        assert zones
        assert_provenance_resolves(zones, registry, id_fields=["source_fvg_id"])
        for zone in zones:
            assert_sources_observable_first(zone, [registry[zone.source_fvg_id]], label="ifvg")

    def test_bpr_sources_resolve_and_confirm_first(self, frame):
        ranges = BprDetector().detect(frame, SYM, M5)
        registry = {z.zone_id: z for z in FvgDetector().detect(frame, SYM, M5)}

        assert ranges
        assert_provenance_resolves(ranges, registry, id_fields=["source_fvg_ids"])
        for item in ranges:
            sources = [registry[i] for i in item.source_fvg_ids]
            assert_sources_observable_first(item, sources, label="bpr")
            assert item.confirmation_timestamp == max(s.confirmation_timestamp for s in sources)

    def test_breaker_sources_resolve_and_confirm_first(self, frame):
        detector = BreakerDetector(BreakerConfig(require_structure_break=False))
        breakers = detector.detect(frame, SYM, M5)
        registry = {b.order_block_id: b for b in OrderBlockDetector().detect(frame, SYM, M5)}

        assert breakers
        assert_provenance_resolves(breakers, registry, id_fields=["source_order_block_id"])
        for breaker in breakers:
            source = registry[breaker.source_order_block_id]
            assert_sources_observable_first(breaker, [source], label="breaker")

    def test_order_block_source_candles_all_precede_confirmation(self, frame):
        for block in OrderBlockDetector().detect(frame, SYM, M5):
            assert max(block.source_candle_timestamps) < block.confirmation_timestamp

    def test_rdrb_source_candles_all_precede_confirmation(self, frame):
        for zone in RdrbDetector().detect(frame, SYM, M5):
            assert len(zone.source_candle_timestamps) == 4
            assert max(zone.source_candle_timestamps) < zone.confirmation_timestamp

    def test_cisd_leg_precedes_the_transition(self, frame):
        for cisd in CisdDetector().detect(frame, SYM, M5):
            assert cisd.leg_end_timestamp < cisd.event_timestamp

    def test_a_dangling_provenance_id_is_caught(self, frame):
        zones = IfvgDetector().detect(frame, SYM, M5)
        with pytest.raises(ContractViolation, match="resolves to no source event"):
            assert_provenance_resolves(zones, {}, id_fields=["source_fvg_id"])

    def test_a_source_confirming_late_is_caught(self, frame):
        from dataclasses import replace as dc_replace

        zones = IfvgDetector().detect(frame, SYM, M5)
        registry = {z.zone_id: z for z in FvgDetector().detect(frame, SYM, M5)}
        zone = zones[0]
        forged = dc_replace(
            registry[zone.source_fvg_id],
            confirmation_timestamp=zone.confirmation_timestamp + timedelta(days=1),
        )

        with pytest.raises(ContractViolation, match="not observable"):
            assert_sources_observable_first(zone, [forged])


class TestCompositeConfirmationHelper:
    def test_it_takes_the_maximum_not_the_first(self):
        early = datetime(2024, 3, 8, 9, 0, tzinfo=UTC)
        late = datetime(2024, 3, 8, 11, 0, tzinfo=UTC)
        assert composite_confirmation([early, late]) == late
        assert composite_confirmation([late, early]) == late

    def test_the_own_trigger_can_push_it_later(self):
        early = datetime(2024, 3, 8, 9, 0, tzinfo=UTC)
        trigger = datetime(2024, 3, 8, 12, 0, tzinfo=UTC)
        assert composite_confirmation([early], own_trigger=trigger) == trigger

    def test_an_empty_composite_is_refused(self):
        with pytest.raises(ContractViolation, match="at least one source"):
            composite_confirmation([])


class TestSourceLevelGuards:
    def test_no_module_compares_a_confirmation_against_an_as_of(self):
        """The tight guard: an observability check is confirmation vs a decision time."""
        from pathlib import Path

        offenders = {}
        for module in DETECTOR_MODULES:
            source = Path(module).read_text(encoding="utf-8")
            hits = [
                line.strip()
                for line in source.splitlines()
                if "confirmation_timestamp" in line and "as_of" in line and "is_observable_at" not in line
            ]
            if hits:
                offenders[module] = hits

        assert offenders == {}, f"the observability rule is re-implemented: {offenders}"

    def test_only_composites_owns_raw_confirmation_comparisons(self):
        """Detectors must route windowing questions through the named helpers.

        ``confirmed_within`` and ``later_confirmed`` are genuinely NOT observability
        checks, but they look identical in source. Keeping them in one module — the way
        ``contract.py`` owns the gate — is what stops a real leak hiding among them.
        """
        from pathlib import Path

        offenders = {}
        for module in DETECTOR_MODULES:
            source = Path(module).read_text(encoding="utf-8")
            code = [ln for ln in source.splitlines() if not ln.lstrip().startswith("#")]
            hits = [
                ln.strip()
                for ln in code
                if "confirmation_timestamp <=" in ln or "confirmation_timestamp >=" in ln
            ]
            if hits:
                offenders[module] = hits

        assert offenders == {}, f"raw confirmation comparisons outside composites.py: {offenders}"

    def test_composites_never_re_detect_their_sources(self):
        """Each composite consumes its upstream detector; none reimplements it."""
        from pathlib import Path

        forbidden = {
            "ict_kronos/ict/ifvg.py": ("def detect_gaps", "reference_zones"),
            "ict_kronos/ict/bpr.py": ("def detect_gaps", "reference_zones"),
            "ict_kronos/ict/breakers.py": ("def _runs", "reference_pivots"),
            "ict_kronos/ict/cisd.py": ("from .structure import", "from .swings import"),
        }
        for module, names in forbidden.items():
            source = Path(module).read_text(encoding="utf-8")
            for name in names:
                assert name not in source, f"{module} appears to reimplement {name}"

    def test_composites_routes_observability_through_the_shared_gate(self):
        """The one module allowed raw confirmation arithmetic still must not
        re-implement the observability RULE — it delegates to the contract."""
        from pathlib import Path

        source = Path("ict_kronos/ict/composites.py").read_text(encoding="utf-8")
        assert "from .contract import" in source
        assert "is_observable_at" in source
        # Every raw comparison must be the RETURN of one of the two named helpers.
        # Prose in the module docstring is not code and is excluded by that rule.
        raw = [
            ln.strip()
            for ln in source.splitlines()
            if ("confirmation_timestamp <=" in ln or "confirmation_timestamp >=" in ln)
            and not ln.lstrip().startswith("#")
        ]
        executable = [ln for ln in raw if ln.startswith("return ")]
        assert len(executable) == 2, f"expected exactly the two helpers, got: {executable}"
        assert len(raw) - len(executable) <= 1, f"unexplained comparisons in composites.py: {raw}"

    def test_every_new_module_uses_the_shared_gate(self):
        from pathlib import Path

        for module in NEW_MODULES:
            source = Path(module).read_text(encoding="utf-8")
            assert "is_observable_at" in source or "filter_observable" in source, module


class TestDeterminism:
    def test_repeated_detection_is_identical(self, detector, frame):
        first = detector.detect(frame, SYM, M5)
        assert detector.detect(frame, SYM, M5) == first

    def test_shuffled_input_produces_the_same_result(self, detector, frame):
        shuffled = frame.iloc[::-1].reset_index(drop=True)
        assert detector.detect(shuffled, SYM, M5) == detector.detect(frame, SYM, M5)
