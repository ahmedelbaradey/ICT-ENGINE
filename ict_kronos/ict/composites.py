"""Shared machinery for composite ICT events (R2-05.2).

R2-01 … R2-05.1 were pure functions of bars. Most of the concepts in this phase are
**relationships between events that already exist** — an IFVG is a transition of an
FVG, a Breaker is a failed Order Block, a BPR is the intersection of two FVGs. That
moves the risk: the question is no longer "did we read the candles correctly" but

    **did the composite inherit its sources' observability correctly?**

A composite that becomes observable before its own inputs is a leak however carefully
each input was computed. So one rule governs every detector in this module's orbit:

    composite.confirmation_timestamp >= max(source.confirmation_timestamp)
                                        plus its own trigger's requirement

:func:`composite_confirmation` computes it, :func:`assert_sources_observable_first`
enforces it, and neither is re-implemented per detector.

**Provenance is an id, not a copy.** A composite stores ``source_fvg_id``, never a
duplicated geometry. Where the definition genuinely creates new geometry (a BPR's
intersection) that geometry is computed, but *identity* always points back — which is
what makes :func:`assert_provenance_resolves` possible at all.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import pandas as pd

from .contract import ContractViolation, Direction, is_observable_at


class ZoneStatus(StrEnum):
    """Lifecycle shared by every composite price zone.

    ``MITIGATED`` is terminal and IS invalidation-by-fill, the same convention R2-05
    adopted. Concepts with an additional *structural* invalidation (an Order Block
    closed through, a Breaker that fails) carry their own extra state.
    """

    ACTIVE = "active"
    PARTIALLY_FILLED = "partially_filled"
    MITIGATED = "mitigated"


@dataclass(frozen=True)
class ZoneFillUpdate:
    """One timestamped step in a zone's fill progression.

    Zones are immutable; progression is a stream of these. Confirmed records are never
    mutated — the R2-04 level/sweep separation, applied to every composite.
    """

    zone_id: str
    event_timestamp: datetime
    confirmation_timestamp: datetime
    fill_percentage: float
    deepest_price: float
    status_after: ZoneStatus

    def is_observable_at(self, as_of: datetime) -> bool:
        """Delegates to the ONE contract-level predicate — never a private copy."""
        return is_observable_at(self, as_of)

    def as_dict(self) -> dict:
        return {
            "zone_id": self.zone_id,
            "event_timestamp": self.event_timestamp.isoformat(),
            "confirmation_timestamp": self.confirmation_timestamp.isoformat(),
            "fill_percentage": self.fill_percentage,
            "deepest_price": self.deepest_price,
            "status_after": self.status_after.value,
        }


# ---------------------------------------------------------------------------
# Confirmation arithmetic
# ---------------------------------------------------------------------------


def composite_confirmation(
    source_confirmations: Iterable[datetime], own_trigger: datetime | None = None
) -> datetime:
    """The earliest instant a composite could be known.

    The maximum of its sources' confirmations and its own trigger. Stated as one
    function so no detector can quietly use the *earlier* of two sources — the single
    most likely way a composite leaks (publishing a BPR at the first gap's confirmation
    rather than the second's).
    """
    stamps = [s for s in source_confirmations]
    if own_trigger is not None:
        stamps.append(own_trigger)
    if not stamps:
        raise ContractViolation("a composite needs at least one source confirmation")
    return max(stamps)


def assert_sources_observable_first(composite, sources: Sequence, *, label: str = "composite") -> None:
    """Fail if any source is not observable by the time the composite claims to be.

    The mechanical form of the §1 rule. Called by every composite detector's tests and
    available as a defensive check inside feature assembly.
    """
    as_of = composite.confirmation_timestamp
    late = [s for s in sources if not is_observable_at(s, as_of)]
    if late:
        offender = late[0]
        raise ContractViolation(
            f"{label} claims confirmation at {as_of.isoformat()} but {len(late)} of its "
            f"source(s) are not observable then; first offender confirms at "
            f"{offender.confirmation_timestamp.isoformat()}"
        )


def assert_provenance_resolves(
    composites: Sequence, registry: dict[str, object], *, id_fields: Sequence[str]
) -> None:
    """Fail if any provenance id does not resolve to a real source event.

    Provenance that points at nothing is indistinguishable from provenance that points
    at something, right up until someone tries to use it. ``None`` is allowed —
    optional provenance is a legitimate absence — but a non-``None`` id that is not in
    ``registry`` is a defect.
    """
    for item in composites:
        for field_name in id_fields:
            value = getattr(item, field_name, None)
            if value is None:
                continue
            ids = value if isinstance(value, tuple | list) else [value]
            for source_id in ids:
                if source_id not in registry:
                    raise ContractViolation(
                        f"{type(item).__name__} references {field_name}={source_id!r} "
                        f"which resolves to no source event"
                    )


def confirmed_within(item, start: datetime, end: datetime) -> bool:
    """Whether ``item`` confirmed inside the window ``[start, end]``.

    A **windowing** question, not an observability one: "did this gap print inside the
    impulse leg?" rather than "may a decision at time t see it?". The two look alike in
    source and are entirely different in meaning, so this lives here beside the gate
    instead of being open-coded in a detector — the same reasoning that keeps
    :func:`contract.is_observable_at` in one place.
    """
    return start <= item.confirmation_timestamp <= end


def later_confirmed(first, second):
    """Whichever of two events confirmed later.

    Named because composites must take the LATER source, and an open-coded comparison
    is one typo away from taking the earlier — which is precisely the composite leak
    :func:`composite_confirmation` exists to prevent.
    """
    return first if first.confirmation_timestamp >= second.confirmation_timestamp else second


def structure_break_id(brk) -> str:
    """Stable id for an R2-03 ``StructureBreak``, computed WITHOUT modifying R2-03.

    R2-03 is approved and is not rewritten by this story, so the relationship layer
    derives an id here rather than adding a field there. Derived purely from values
    the break already carries, so it is stable across runs and across replay.
    """
    return f"break:{brk.symbol}:{brk.timeframe}:{brk.event_type.value}:{brk.event_timestamp.isoformat()}"


# ---------------------------------------------------------------------------
# Zone fill tracking
# ---------------------------------------------------------------------------


def zone_edges(top: float, bottom: float, direction: Direction) -> tuple[float, float]:
    """``(entry_edge, far_edge)`` for a zone of the given polarity.

    A **bullish** zone acts as support: price enters it from above, so it is entered at
    its top and fully filled at its bottom. A **bearish** zone is the mirror. Defined
    once here because getting this backwards silently inverts every fill percentage in
    the engine.
    """
    if direction is Direction.BULLISH:
        return top, bottom
    return bottom, top


def zone_fill_fraction(top: float, bottom: float, direction: Direction, extreme: float) -> float:
    """How much of the zone a penetrating extreme has retraced, in ``[0, 1]``.

    ``extreme`` is the lowest low seen for a bullish zone, the highest high for a
    bearish one. Touching the entry edge exactly gives 0 — a touch is not a fill, the
    rule R2-05 established.
    """
    span = top - bottom
    if span <= 0:  # pragma: no cover - construction refuses degenerate zones
        return 0.0
    raw = (top - extreme) / span if direction is Direction.BULLISH else (extreme - bottom) / span
    return float(min(max(raw, 0.0), 1.0))


def track_zone_fill(
    work: pd.DataFrame,
    *,
    zone_id: str,
    top: float,
    bottom: float,
    direction: Direction,
    start_timestamp: datetime,
    partial_threshold: float = 0.0,
    full_threshold: float = 1.0,
) -> list[ZoneFillUpdate]:
    """The fill progression of one zone, from ``start_timestamp`` onward.

    ``work`` must already carry ``close_time`` and be sorted by ``timestamp``. Only
    bars at or after ``start_timestamp`` are considered: a zone cannot be filled by
    the very bars that defined it, and cannot be filled before it was knowable.

    Emits an update only when the fill **deepens**, so the stream is a monotone record
    of progress rather than one row per bar. Stops at full mitigation — a mitigated
    zone receives no further updates.
    """
    if len(work) == 0:
        return []

    window = work[work["timestamp"] >= pd.Timestamp(start_timestamp)]
    if len(window) == 0:
        return []

    _, far = zone_edges(top, bottom, direction)
    updates: list[ZoneFillUpdate] = []
    best = 0.0
    extreme = far  # worst case until a bar penetrates
    started = False
    status = ZoneStatus.ACTIVE

    lows = window["low"].to_numpy(dtype="float64")
    highs = window["high"].to_numpy(dtype="float64")
    stamps = window["timestamp"].to_numpy()
    closes = window["close_time"].to_numpy()

    for i in range(len(window)):
        candidate = lows[i] if direction is Direction.BULLISH else highs[i]
        if not started:
            extreme = candidate
            started = True
        elif direction is Direction.BULLISH:
            extreme = min(extreme, candidate)
        else:
            extreme = max(extreme, candidate)

        fraction = zone_fill_fraction(top, bottom, direction, extreme)
        if fraction <= best:
            continue

        best = fraction
        if fraction >= full_threshold:
            status = ZoneStatus.MITIGATED
        elif fraction > partial_threshold:
            status = ZoneStatus.PARTIALLY_FILLED
        else:
            continue

        updates.append(
            ZoneFillUpdate(
                zone_id=zone_id,
                event_timestamp=pd.Timestamp(stamps[i]).to_pydatetime(),
                confirmation_timestamp=pd.Timestamp(closes[i]).to_pydatetime(),
                fill_percentage=float(fraction),
                deepest_price=float(extreme),
                status_after=status,
            )
        )
        if status is ZoneStatus.MITIGATED:
            break

    return updates


def first_close_beyond(
    work: pd.DataFrame, *, level: float, above: bool, start_timestamp: datetime
) -> tuple[int, pd.Series] | None:
    """The first bar at or after ``start_timestamp`` whose CLOSE is beyond ``level``.

    ``above=True`` finds a close strictly greater than ``level``; ``False`` finds a
    close strictly less. Strict on purpose — touching a level is not closing through
    it, consistent with every other boundary rule in this engine.

    Returns ``(positional index into work, row)`` or ``None``. Used by the Order Block
    (the qualifying close through the candidate's range), the IFVG (the inverting
    close) and the Breaker (the failing close), so the "a close, never a wick" rule
    has exactly one implementation.
    """
    window = work[work["timestamp"] >= pd.Timestamp(start_timestamp)]
    if len(window) == 0:
        return None

    closes = window["close"].to_numpy(dtype="float64")
    hits = closes > level if above else closes < level
    positions = hits.nonzero()[0]
    if len(positions) == 0:
        return None

    local = int(positions[0])
    return int(window.index[local]), window.iloc[local]
