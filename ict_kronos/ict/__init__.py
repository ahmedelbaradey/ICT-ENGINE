"""Deterministic ICT market-structure detection (Phase 2).

Every detector here is a pure function of observed bars. No LLM ever decides
whether a pattern exists (CLAUDE.md rules 2 and 3).
"""

from .contract import (
    ContractViolation,
    Direction,
    EventStatus,
    EventType,
    IctEvent,
    assert_no_leakage,
    assert_observable,
    events_to_frame,
    filter_observable,
)
from .sessions import (
    DEFAULT_SESSIONS,
    BoundaryAnomaly,
    RunningSessionState,
    SessionDefinition,
    SessionDetector,
    SessionKind,
    SessionOccurrence,
    SessionWindow,
    load_definitions,
    resolve_window,
    resolve_windows,
)
from .swings import SwingConfig, SwingDetector, SwingPoint, TiePolicy, reference_pivots

__all__ = [
    "DEFAULT_SESSIONS",
    "BoundaryAnomaly",
    "ContractViolation",
    "Direction",
    "EventStatus",
    "EventType",
    "IctEvent",
    "RunningSessionState",
    "SessionDefinition",
    "SessionDetector",
    "SessionKind",
    "SessionOccurrence",
    "SessionWindow",
    "SwingConfig",
    "SwingDetector",
    "SwingPoint",
    "TiePolicy",
    "assert_no_leakage",
    "assert_observable",
    "events_to_frame",
    "filter_observable",
    "load_definitions",
    "reference_pivots",
    "resolve_window",
    "resolve_windows",
]
