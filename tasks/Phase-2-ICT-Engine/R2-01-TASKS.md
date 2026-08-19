# R2-01 — Session detector — tasks

Story: [R2-01](../../user-stories/Phase-2-ICT-Engine/R2-01-session-detector.md)

| ID | Task | Notes | Status |
|---|---|---|---|
| R2-01-1 | `ict/contract.py` — the shared Phase 2 detector event contract (`IctEvent`, `EventType`, `Direction`) | Used by every later detector; get it right once | ✅ |
| R2-01-2 | `ict/sessions.py` — `SessionDefinition` (name, tz, local start/end, kind) + documented defaults | Config, never literals in logic | ✅ |
| R2-01-3 | DST-aware UTC window resolution, incl. sessions crossing midnight | `zoneinfo`; anchor on local date of session START | ✅ |
| R2-01-4 | `SessionDetector` — occurrences from actual bars; open/close/high/low; no bars ⇒ no occurrence | Weekend/holiday judgement lives here | ✅ |
| R2-01-5 | Point-in-time `session_state_at(t)` running state | Only bars with `close_time <= t` | ✅ |
| R2-01-6 | Emit `IctEvent`s with `confirmation_timestamp` = last in-session bar's close | Never the extreme-setting bar | ✅ |
| R2-01-7 | Config wiring — `SessionConfig` in `app/config.py`, env-overridable | CLAUDE.md rule 4 | ✅ |
| R2-01-8 | Unit tests: normal days, midnight crossing, both DST transitions, weekend | | ✅ |
| R2-01-9 | **Batch vs streaming-replay test** | Instruction §10 | ✅ |
| R2-01-10 | **Leakage tests** — running state and completed-session confirmation | Mandatory | ✅ |
| R2-01-11 | **Real-data acceptance** — EURUSD + XAUUSD 2024-03-08 → 2024-03-12, incl. the differing DST reopen | Skips cleanly if data absent | ✅ |
| R2-01-12 | **Documentation** — `docs/ict/sessions.md` incl. chosen definitions + ambiguities | Mandatory | ✅ |
