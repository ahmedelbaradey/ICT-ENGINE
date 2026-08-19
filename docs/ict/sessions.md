# SessionDetector (R2-01)

Story: [R2-01](../../user-stories/Phase-2-ICT-Engine/R2-01-session-detector.md) · Code: [`ict_kronos/ict/sessions.py`](../../ict_kronos/ict/sessions.py)

---

## 1. Definition

A **trading session** is a recurring window of the trading day, defined in the *local* time of the market that owns it. A **kill zone** is a narrower ICT-specific window inside the day where directional moves are held to cluster.

Both are modelled by the same type. Kill zones are first-class windows, not a boolean on a session, because they have their own high, low and confirmation timing.

A session is **configuration**, never a constant in detector logic:

```python
SessionDefinition(name="london", timezone="Europe/London",
                  start_local=time(8, 0), end_local=time(16, 30))
```

**Never a UTC hour.** That single choice is what makes DST handling automatic rather than a special case.

## 2. Algorithmic rule

For a definition *D* and a local calendar date *d*:

1. `start_naive = combine(d, D.start_local)`; `end_naive = combine(d', D.end_local)` where `d' = d + 1 day` if the window crosses local midnight (`end_local <= start_local`), else `d`.
2. Convert each to UTC in `D.timezone`, recording any DST anomaly (§7).
3. The window is the half-open UTC interval `[start_utc, end_utc)`.
4. A bar **belongs** to the window when it is *fully contained*:
   `window.start <= bar.timestamp` **and** `bar.close_time <= window.end`.
5. An **occurrence** exists only if at least one bar belongs **and** the window has fully elapsed within the observed data (`max(close_time) >= window.end`).
6. Occurrence OHLC: open = first member's open, close = last member's close, high = max member high, low = min member low. Extremes tie-break to the **earliest** bar.

Windows are pure calendar arithmetic — they never depend on the data, so a future bar can never move a past session's boundary.

## 3. Input

| | |
|---|---|
| `frame` | Canonical candle frame (`CANDLE_COLUMNS`), UTC, sorted |
| `symbol` | `Symbol` — supplies `point_value` for `strength` |
| `timeframe` | `Timeframe` — supplies bar duration, hence `close_time` |
| `definitions` | `tuple[SessionDefinition, ...]`, default `DEFAULT_SESSIONS` |

## 4. Output

**`SessionOccurrence`** — window, bar count, OHLC with the timestamps of each extreme, and `confirmation_timestamp`.

**`IctEvent`** — four per occurrence (`SESSION_HIGH`, `SESSION_LOW`, `SESSION_OPEN`, `SESSION_CLOSE`), carrying the full Phase 2 contract.

**`RunningSessionState`** — the point-in-time API: in-progress open/high/low/last, `bar_count`, `is_active`, `is_complete`, `position_in_range`.

### Conventions

- **Direction:** `SESSION_HIGH` → `BULLISH` (buy-side liquidity rests **above** highs); `SESSION_LOW` → `BEARISH` (sell-side rests **below** lows); open/close → `NEUTRAL`.
- **`reference_level`:** the session open price, so every extreme can be read relative to where the session began.
- **`strength`:** the session range in instrument points, `(high - low) / point_value`. A deterministic magnitude — *not* a tuned score. Documented here so nobody mistakes it for a quality rating.

## 5. Event timestamp

The instant the reported price actually occurred:

| Event | `event_timestamp` |
|---|---|
| `SESSION_HIGH` | open time of the bar that printed the high (earliest on ties) |
| `SESSION_LOW` | open time of the bar that printed the low |
| `SESSION_OPEN` | open time of the first in-session bar |
| `SESSION_CLOSE` | open time of the last in-session bar |

## 6. Confirmation timestamp

**All four events confirm at `window.end_utc`.**

The reasoning matters more than the rule. At the instant the session high printed you could **not** know it was the session high — a later in-window bar might still exceed it. The earliest moment that fact is knowable is when the window closes.

Stamping a session high at the bar that printed it would leak up to a full session of future information. That is the same class of error found in `ForexQuant`'s FVG detector ([LEGACY_RESEARCH.md §5.1](../financial-ai/LEGACY_RESEARCH.md)), and the contract's constructor makes it impossible to express here.

**Running state is the separate, honest answer** for in-session features: `session_state_at(t)` uses only bars with `close_time <= t`. It is explicitly *not* final, and `is_complete` says so.

## 7. Edge cases

| Case | Behaviour |
|---|---|
| Window with no bars (weekend, holiday) | **No occurrence.** Absence is preserved, never fabricated |
| Window not yet elapsed in the data | **Not emitted.** Otherwise batch would disagree with streaming replay |
| Bar straddling a boundary | **Excluded** (fully-contained rule), so out-of-session price can never set a session extreme |
| Tie for the extreme | **Earliest** bar wins — documented, and the honest choice |
| Empty frame | Empty result, not an error |
| Sessions crossing midnight | Anchored to the local date of the **start** |
| Overlapping windows (London ∩ New York) | All reported independently; overlap is real, not an error |
| DST: nonexistent local time | Flagged `NONEXISTENT`; window is genuinely shorter that day |
| DST: ambiguous local time | Flagged `AMBIGUOUS`; the **first** (still-DST, `fold=0`) occurrence is used |
| Bad configuration | Raises at load. Never silently falls back to defaults |

### A real DST artefact, observed in the validated data

The London Kill Zone is `02:00–05:00 America/New_York`. On **2024-03-10** the US clocks jump `02:00 → 03:00`, so **02:00 local does not exist that day**. The window is genuinely **two hours** (07:00–09:00 UTC), not three, and is flagged `NONEXISTENT`.

This is surfaced rather than normalised because anyone comparing kill-zone ranges across days needs to know one of them was short. It is pinned by `test_london_kill_zone_is_short_on_us_spring_forward_day`.

### Instruments do not share one reopen time

After the same weekend closure in the Phase 1.5 data:

| Instrument | First post-weekend bar |
|---|---|
| EURUSD | 2024-03-10 **21:00 UTC** |
| XAUUSD | 2024-03-10 **22:00 UTC** |

**No fixed UTC opening time is assumed for any instrument.** Sessions come from local-time definitions plus the bars that actually exist, so the same configuration handles both without special-casing.

## 8. Known ambiguities — documented, not silently resolved

Session boundaries are genuinely contested. We state our defaults and make every one configurable via `ICT_SESSIONS_JSON`.

| Concept | Our default | Alternatives in circulation |
|---|---|---|
| Asian session | `09:00–18:00 Asia/Tokyo` | `00:00–09:00 UTC`; `20:00–00:00 New York` (the ICT "Asian range"); Sydney-inclusive `22:00–08:00 UTC` |
| London session | `08:00–16:30 Europe/London` | `07:00–16:00 London`; `03:00–12:00 New York` |
| New York session | `08:00–17:00 America/New_York` | `09:30–16:00` (equity cash hours); `08:00–12:00` (ICT's "AM session") |
| London Kill Zone | `02:00–05:00 America/New_York` | `07:00–10:00 London`; `02:00–04:00 New York` |
| NY Kill Zone | `07:00–10:00 America/New_York` | `08:30–11:00`; separate AM/PM kill zones |

**Deliberately not chosen for you:**

- **Asian range vs Asian session.** ICT's "Asian range" (`20:00–00:00 NY`) is an accumulation concept, narrower than the Tokyo trading session. We default to the *session*; the range is one config line away.
- **Kill-zone overlap.** Our London Kill Zone (`02:00–05:00 NY` = `07:00–10:00 UTC` in winter) partly precedes our London session (`08:00 London`). That is faithful to the ICT convention, not a bug.
- **Asian Kill Zone** is not in the defaults — out of R2-01 scope, trivially addable.
- **Bar membership** could plausibly be "bar opens inside the window" instead of fully-contained. We chose containment so an extreme can never come from outside the session; the cost is that a coarse bar straddling a boundary is dropped, which matters mainly for 1H bars against half-hour boundaries.

### Overriding

```bash
export ICT_SESSIONS_JSON='[
  {"name":"asian_range","timezone":"America/New_York","start_local":"20:00","end_local":"00:00"},
  {"name":"london_kz","timezone":"America/New_York","start_local":"02:00","end_local":"05:00","kind":"kill_zone"}
]'
```

Inline JSON or a file path. Times are **local to the named timezone**. Validation is strict: an unknown timezone, a malformed time, a missing field or a duplicate name raises at load — a misconfiguration must surface immediately, not produce quietly wrong sessions for months.

## 9. Test coverage

**136 tests** across four files (19 + 57 + 23 + 37).

| File | Tests | Covers |
|---|---|---|
| `tests/test_ict_contract.py` | 19 | Contract invariants, observability, serialisation, event-type coverage |
| `tests/test_sessions.py` | 57 | Definitions, config loading + rejection, window resolution, DST, detection, events, running state, configurability |
| `tests/test_sessions_leakage.py` | 23 | Confirmation timing, point-in-time running state, batch == streaming replay, no future contamination |
| `tests/test_sessions_real_data.py` | 37 | Real EURUSD + XAUUSD, 2024-03-08 → 2024-03-12 |

Specifically required by the story:

- Normal trading days ✅ · weekend boundaries ✅ · US DST ✅ · London DST ✅ · midnight crossing ✅ · EURUSD ✅ · XAUUSD ✅
- Batch vs streaming replay ✅ (synthetic and real data)
- Leakage ✅ (confirmation timing and running state)
- The Phase 1.5 differing-reopen observation ✅ (`test_instruments_reopen_at_different_utc_hours`)

Real-data tests **skip cleanly** when `data/` is absent — the offline gate never depends on them.

## 10. Known limitations

1. **No holiday calendar.** Christmas or a bank holiday looks like any other empty window: no occurrence, correctly, but the detector cannot yet say *why* it was empty. A named holiday calendar is a candidate for R2-04, where "previous day" needs it.
2. **`session_state_at` is O(bars) per call.** Fine for feature assembly at selected instants; a running scan over millions of bars would want an incremental accumulator. Deferred until R2-07 shows it matters.
3. **No Asian Kill Zone** in the defaults (out of scope; one config line).
4. **Coarse bars can be excluded** by the fully-contained rule when a boundary falls mid-bar — e.g. a 1H bar against London's 16:30 close. Documented above; visible in `test_finer_timeframe_sees_at_least_as_many_sessions`.
5. **Sessions are calendar windows, not liquidity windows.** A session can be "open" while the instrument is not trading. The occurrence rule (needs real bars) covers this, but no explicit "market open" concept exists yet.
