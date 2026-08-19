# R2-01 — Session detector (Asian / London / New York + kill zones)

- **Project:** ICT-Kronos
- **Phase:** Phase 2 — ICT Engine
- **Epic:** Deterministic ICT representation
- **Issue type:** Story
- **Story points:** 5 — timezone/DST correctness is the whole cost; the arithmetic is trivial
- **Labels:** `ict`, `sessions`, `timestamps`, `leakage`
- **Depends on:** Phase 1.5 (validated real data)
- **Blocks:** R2-02 … R2-07 (every other detector needs session context)

## Description

As a quantitative researcher, I want deterministic, timezone- and DST-aware trading-session detection, so that every downstream ICT feature can be conditioned on *when* in the trading day price action occurred — and so that the engine can tell a routine weekend closure apart from a data fault.

This is the first Phase 2 story because everything else depends on session context: liquidity uses session highs/lows, kill zones gate setup quality, and the normalizer deliberately withheld the weekend/holiday judgement for this layer to own.

## Scope

Implement:

- **Sessions:** Asian, London, New York
- **Kill zones:** London Kill Zone, New York Kill Zone
- **Per occurrence:** `session_start`, `session_end`, session open, session close, session high, session low
- Timezone-aware and DST-aware boundary computation
- Point-in-time (running) session state for feature use

## Acceptance criteria

1. Session definitions are **configuration, not literals** — a session is `(name, tz, local start, local end)` and can be overridden without touching detector logic (CLAUDE.md rule 4).
2. Boundaries are computed in the session's **local timezone and converted to UTC**, so a DST transition moves the UTC boundary automatically. Stored timestamps remain UTC throughout; nothing is ever converted in storage.
3. **No fixed UTC opening time is assumed for any instrument.** Sessions are derived from local-time definitions plus the bars actually present, never from a hardcoded UTC hour.
4. Sessions that **cross midnight** local time (e.g. Asian) are handled correctly, anchored to the local date of the session **start**.
5. A session occurrence with **no bars** (weekend, holiday) yields **no session event** — absence is preserved, never fabricated.
6. Every emitted event carries the Phase 2 detector contract: `symbol`, `timeframe`, `event_type`, `direction`, `event_timestamp`, `confirmation_timestamp`, `price_level`, `reference_level`, `strength`.
7. **`confirmation_timestamp` semantics:** a *completed* session's high/low/close is only knowable at **session end** (specifically, at the close of the last bar within the session). It is NEVER stamped at the bar that happened to set the extreme.
8. **Running state is separately available and point-in-time safe:** `session_state_at(t)` uses only bars whose `close_time <= t`.
9. **LEAKAGE CRITERION (mandatory):** a test proves that the running session high/low at time *t* never reflects a bar closing after *t*, and that a completed-session event is never observable before its `confirmation_timestamp`. Streaming replay must equal batch computation.
10. Real-data acceptance on `EURUSD` + `XAUUSD`, `2024-03-08 → 2024-03-12`, including the weekend boundary and the 2024-03-10 US DST transition.
11. **The DST observation from Phase 1.5 is an explicit acceptance test:** EURUSD's first post-weekend bar is `2024-03-10 21:00 UTC` while XAUUSD's is `22:00 UTC`. The detector must handle instruments whose UTC session/open behaviour differs, and must not assume one fixed reopen time.

## Test coverage required

- Normal trading days (all three sessions, both kill zones)
- Weekend boundaries (Friday close, Sunday reopen)
- US DST transition (2024-03-10)
- London/EU DST transition (2024-03-31)
- Sessions crossing midnight
- Both instruments: EURUSD and XAUUSD
- Batch vs streaming-replay equivalence
- Leakage: running state and completed-session confirmation

## Notes and decisions

- **Session definitions are contested in the ICT community.** Per the documentation rule, the chosen defaults are documented in `docs/ict/sessions.md` with their source, and every boundary is configurable. We do not silently pick one interpretation.
- Kill zones are modelled as **first-class session-like windows**, not as a boolean flag on a session, because they have their own high/low and their own confirmation semantics.
- The weekend/holiday judgement lives here, not in the normalizer — the normalizer reports all gaps without interpretation, deliberately.

## Out of scope

Asian kill zone, session liquidity sweeps (R2-04), any setup-quality scoring (later phase).
