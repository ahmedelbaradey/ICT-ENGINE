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
    events_to_frame,
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
    "assert_no_leakage",
    "events_to_frame",
    "load_definitions",
    "resolve_window",
    "resolve_windows",
]
