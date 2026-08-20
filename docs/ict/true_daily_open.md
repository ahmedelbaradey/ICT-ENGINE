# True Daily Open

**Story:** [R2-05.1](../../user-stories/Phase-2-ICT-Engine/R2-05.1-true-daily-open.md) ·
**Module:** [`ict_kronos/ict/true_daily_open.py`](../../ict_kronos/ict/true_daily_open.py)

> Written **before** the implementation, as the story requires. Where the
> implementation and this document disagree, this document is the defect report.

---

## 0. The one-line definition

```
TRUE_DAILY_OPEN  =  00:00 America/New_York  =  the OPEN price of the bar
                                               that begins exactly at that instant
```

Nothing else. Not the high, not the low, not the close, not the first bar after a
weekend, not a price derived from any other bar.

---

## 1. Why this is a separate primitive

The repository already has a daily boundary — **17:00 America/New_York**, the
FX/broker trading-day rollover used by R2-04 for `previous_day_high` /
`previous_day_low` and for the trading-week span. That boundary is untouched by this
story.

True Daily Open is a **different concept that happens to also be daily**, and
conflating the two is a classic source of silently wrong ICT features:

| | True Daily Open | Trading Day boundary | Session open |
|---|---|---|---|
| Local time | **00:00** America/New_York | **17:00** America/New_York | per-session, per-zone |
| Owner | R2-05.1 (this) | R2-04 | R2-01 |
| What it is | a **price level** | a **period delimiter** | a **window edge** |
| Used for | above/below bias, daily reference | PDH/PDL, PWH/PWL, week span | kill zones, session H/L |
| Cardinality | one price per NY calendar date | one rollover per trading day | one window per session per day |

They are never equal, never interchangeable, and are deliberately kept in separate
modules. R2-05.1 adds one primitive and changes none of R2-01…R2-05.

Note the two also disagree about *which date a given instant belongs to*: at
20:00 NY on Monday the trading day is already **Tuesday**'s (it rolled at 17:00), but
the True Daily Open in force is still **Monday**'s. Downstream must not assume one
date label serves both.

---

## 2. The boundary instant

The boundary is defined in **local New York time** and converted to UTC with
`zoneinfo`, using the same `_local_to_utc` helper R2-01 uses for every session
boundary. There is exactly one DST implementation in this codebase and this module
does not add a second.

**A fixed UTC hour is never written down anywhere.** `05:00 UTC` and `04:00 UTC` are
*consequences* of the conversion on particular dates, not definitions:

| NY date | US clock | 00:00 America/New_York in UTC |
|---|---|---|
| 2024-03-08 | EST (UTC−5) | `2024-03-08T05:00:00Z` |
| 2024-03-11 | EDT (UTC−4) | `2024-03-11T04:00:00Z` |
| 2024-11-01 | EDT (UTC−4) | `2024-11-01T04:00:00Z` |
| 2024-11-04 | EST (UTC−5) | `2024-11-04T05:00:00Z` |

**The invariant is local, not UTC.** Tests assert
`event_timestamp.astimezone(ZoneInfo("America/New_York")).time() == 00:00` for every
emitted record. They must never assert a constant UTC hour — that assertion would pass
for eight months a year and silently encode the bug the story exists to prevent.

### 2.1 Midnight and DST anomalies

US DST transitions occur at 02:00 local, so 00:00 New York is never skipped and never
repeated. The implementation nonetheless records the R2-01 `BoundaryAnomaly` rather
than assuming this, because the boundary timezone is **configuration** (CLAUDE.md rule
4) and midnight transitions are real in other zones — Brazil and Chile have both run
DST changes at local midnight. Under `America/New_York` the recorded anomaly is always
`NONE`; that is an observation, not an assumption.

Ambiguity policy, inherited unchanged from R2-01: `fold=0` — the first of two
occurrences.

---

## 3. Which bar, and which price

A record is emitted for NY calendar date *d* **if and only if** the observed frame
contains a bar whose `timestamp` equals the boundary instant **exactly**.

```
price_level = bar.open        of that bar
```

`timestamp` is the bar's **open time** (CLAUDE.md timestamp convention), so a bar
timestamped at the boundary is the bar that opens at the boundary, and its `open` is
the opening price at 00:00 New York by definition.

`high`, `low` and `close` are never read. The module does not reference them.

### 3.1 No exact bar → no record

There is no fallback. Specifically the implementation will **not**:

- take the nearest bar before or after the boundary;
- take the previous bar's `close` as a stand-in for the open;
- interpolate anything from OHLC;
- carry the previous date's True Daily Open forward;
- use the Sunday-evening FX reopen as a substitute for Sunday 00:00;
- synthesise a level for a date whose bar is missing for any reason.

A missing True Daily Open is **information** — the market was shut, or the dataset has
a hole. Manufacturing a price would convert a known unknown into a plausible-looking
lie, and every downstream distance-from-TDO feature would inherit it.

### 3.2 Coarse timeframes and the straddling bar

Whether an exact boundary bar exists is a property of the timeframe's grid, not a
judgement call:

| Timeframe | Grid (UTC) | EST boundary 05:00 | EDT boundary 04:00 |
|---|---|---|---|
| 1m, 5m, 15m, 1h | every 1/5/15/60 min | lands on the grid | lands on the grid |
| 4h | 00, 04, 08, 12, 16, 20 | **05:00 is not a boundary** | 04:00 lands on the grid |
| 1d | 00:00 UTC | never | never |

So on 4H the True Daily Open exists during EDT and does not exist during EST, and on
1D (a UTC-midnight-anchored grid) it never exists at all. The 05:00 instant falls
*inside* the 04:00–08:00 4H bar, and that bar's `open` is the price at 04:00 UTC —
**a different price at a different time**. Returning it would be a fabrication, so
nothing is returned.

This is the repository's existing fully-contained-bar policy applied to a point rather
than a window: R2-01 excludes bars that straddle a session boundary for the same
reason. The consequence — that the True Daily Open is a property of a *source
timeframe* — is stated as a limitation in §9, not hidden.

**Landing on the grid is necessary, not sufficient.** The bar must also exist. On the
Phase 1.5 fixture, 2024-03-11 04:00 UTC is a valid 4H grid point for both instruments,
yet only XAUUSD produces a level: EURUSD is missing one of the sixty 1m bars in that
hour, so the resampler's `require_complete=True` policy drops the 4H (and 1H) bar
upstream, and no boundary bar means no level. Same rule, applied one layer earlier —
the detector is not making a judgement, it is reporting an absence.

---

## 4. Timestamps and confirmation

```
event_timestamp        = the 00:00 NY boundary instant, in UTC
created_timestamp      = the same instant
confirmation_timestamp = the same instant
```

**Confirmation lag is exactly zero, and this is the correct answer** — but it is worth
being explicit about why, because every other detector in Phase 2 confirms at a bar's
`close_time` and a zero lag looks at first glance like the bug this project keeps
finding in legacy code.

The difference is *which price the definition reads*:

| Concept | Price read | Final when |
|---|---|---|
| FVG (R2-05) | `low`/`high` of candle 3 | candle 3 **closes** |
| Session high (R2-01) | running max over a window | the window **ends** |
| Liquidity sweep (R2-04) | the sweeping bar's extreme | that bar **closes** |
| **True Daily Open** | **`open` of the boundary bar** | **the bar opens** |

An open price is fixed at the first print of the bar and can never be revised by
anything that happens later within it. Waiting for the close would be the *opposite*
error to the ForexQuant one — not leaking the future, but discarding present
information and publishing a level an entire bar late.

### 4.1 The honest caveat

At tick resolution a bar's opening print arrives at the first tick **at or after** the
boundary, not at the boundary instant itself. Stamping confirmation at the boundary is
therefore the earliest *possible* moment rather than the empirically observed one, and
a consumer asking "what is observable at exactly 00:00:00.000 NY?" is answered
optimistically by up to one tick interval.

This is bounded and, at the resolution this engine operates at, unreachable: R2-07
assembles features at bar **close** boundaries, by which time the open is
unambiguously historical. It is recorded here rather than in a commit message because
it is the only place in Phase 2 where a confirmation timestamp is not provably
conservative. If sub-bar decisioning is ever introduced, this is the line to revisit.

---

## 5. Leakage guarantees

The detector reads exactly one row per date and never looks beyond it. Concretely:

1. **Before the boundary, the level does not exist.** `filter_observable(levels, t)`
   for any `t` strictly before 00:00 NY excludes that date's record.
2. **At the boundary it becomes observable**, at the boundary instant itself.
3. **After the boundary nothing can change it.** The record is a frozen dataclass and
   the detector is a pure function of the boundary row.
4. **Appending future bars cannot modify an existing record.** Prefix replay returns
   records byte-identical to the full batch for every date the prefix covers.
5. **Later prices are irrelevant.** Mutating any later `high`, `low`, `close` or
   `volume` — or any later structure, liquidity or FVG — leaves every record
   unchanged. This is proved by mutating the data, not by inspecting the code.
6. **A later date cannot mutate an earlier one.** Identity is
   `(symbol, timeframe, trading_date)` and records are never merged into a running
   level.

### 5.1 The shared observability gate

Observability goes through the contract's single gate —
`is_observable_at` / `filter_observable` / `assert_observable`. The string
`confirmation_timestamp <=` does not appear in `true_daily_open.py`, and a
source-level regression test fails the build if it is reintroduced, mirroring the
guard R2-04 and R2-05 carry.

---

## 6. Weekends, holidays and missing data

All four cases resolve through **one** rule — exact bar or nothing — rather than
through special cases:

| Situation | Boundary bar | Result |
|---|---|---|
| Saturday | market shut | no record |
| Sunday (before the ~17:00 NY reopen) | market shut | no record |
| Holiday with a full closure | no bar | no record |
| Dataset hole at the boundary | no bar | no record |
| Late start — first bar after 00:00 NY | no bar **at** the boundary | no record |
| Reopen exactly at 00:00 NY | bar exists | record, `open` of that bar |

No holiday calendar is consulted, and none is needed: a holiday is simply a date whose
boundary bar is absent, and the data already says so. Adding a calendar would introduce
a second source of truth that could disagree with the bars.

---

## 7. Identity and cardinality

```
level_id = tdo:<symbol>:<timeframe>:<YYYY-MM-DD>
```

- **One record per NY calendar date per (symbol, timeframe)**, at most.
- `trading_date` is the **New York calendar date**, derived by converting the boundary
  instant back to New York local time — never the UTC date. On 2024-03-08 the record's
  UTC timestamp is `05:00Z` on the same UTC date, but during EDT a 00:00 NY boundary
  is `04:00Z`, and for zones east of UTC the two dates could differ outright.
- Records are never collapsed into a running level and a later date never mutates an
  earlier one.
- Timestamps and the NY date are identity. **Positional dataframe indexes are not part
  of the record at all**, so they cannot accidentally become a cross-timeframe join
  key.

---

## 8. Batch, prefix and streaming equivalence

Required and tested, in the form R2-02…R2-05 use:

```
detect(all_bars)  ==  detect(bars[:n]) for every n, restricted to dates the prefix covers
                  ==  bar-by-bar replay
```

Equivalence here is structural rather than incidental: the detector's output for date
*d* is a function of a single row, so no ordering, warm-up or windowing effect can
exist. The tests assert it anyway, because "obviously correct" is how the legacy
off-by-one survived a code review.

---

## 9. Known limitations

1. **The True Daily Open is a property of a source timeframe.** On grids that do not
   contain the boundary instant — 4H under EST, 1D always — no record is produced. A
   consumer wanting the 1m-derived level while working on 4H needs cross-timeframe
   projection, which is **R2-07's** job and is deliberately not built here.
2. **Confirmation is stamped at the boundary instant, not at the first tick** (§4.1).
3. **No holiday calendar.** Absence is inferred from missing bars, which is correct for
   full closures and silent for half-days — a half-day with a normal 00:00 open
   produces a normal record, which is the intended behaviour.
4. **`trading_date` is a NY calendar date, not the R2-04 trading day.** The two labels
   disagree between 17:00 and 24:00 NY (§1). Downstream must pick deliberately.
5. **Validated over a four-day window.** The fixture covers one EST date, one EDT date
   and one weekend closure — enough to pin DST and absence, not enough to characterise
   holiday behaviour, which has no representative in the sample.
6. **The autumn DST transition has no real-data coverage** and is tested
   synthetically, for the same reason: the Phase 1.5 window is in March.

---

## 10. Real-data validation

EURUSD and XAUUSD, 2024-03-08 → 2024-03-11, 1m/5m/15m/1h stored or resampled, 4h
derived. Prices below are read from the fixture, never hardcoded in the
implementation.

| Symbol | NY date | Local | UTC | Clock | `open` |
|---|---|---|---|---|---|
| EURUSD | 2024-03-08 | 00:00 America/New_York | `2024-03-08T05:00:00Z` | EST | 1.09456 |
| EURUSD | 2024-03-11 | 00:00 America/New_York | `2024-03-11T04:00:00Z` | EDT | 1.09411 |
| XAUUSD | 2024-03-08 | 00:00 America/New_York | `2024-03-08T05:00:00Z` | EST | 2157.665 |
| XAUUSD | 2024-03-11 | 00:00 America/New_York | `2024-03-11T04:00:00Z` | EDT | 2179.365 |

The same two prices are produced on **every** timeframe whose grid contains the
boundary and whose bar survives the resampler — 1m, 5m and 15m for both instruments,
plus 1H/4H where available. Cross-timeframe agreement is asserted, not assumed.

Availability by timeframe, exactly as observed:

| | 1m | 5m | 15m | 1H | 4H | 1D |
|---|---|---|---|---|---|---|
| EURUSD | both | both | both | 03-08 only | none | none |
| XAUUSD | both | both | both | both | 03-11 only | none |

Every gap in that table has a mechanical explanation and none is a detector decision:
4H under EST cannot contain a 05:00 boundary (§3.2); EURUSD's 1H and 4H bars at
2024-03-11 04:00 UTC are dropped upstream by `require_complete` because one 1m bar is
missing from that hour; 1D is anchored to UTC midnight and can never contain 00:00 NY.
A test asserts the general form of this — *a missing level always means a missing
bar* — across every symbol and timeframe.

`2024-03-09` (Saturday) and `2024-03-10` (Sunday) produce **no record** for either
symbol on any timeframe — the closure is respected rather than papered over, and
neither the Friday close nor the Sunday-evening reopen is substituted.

The single UTC hour shift between the two dates — `05:00Z` → `04:00Z` for the same
local `00:00` — is the DST behaviour of §2, observed on real bars rather than
constructed.

**No trading claim is made from this sample.** It is an engineering and
timestamp-validation fixture.

---

## 11. Test coverage

**250 tests** across three files.

| File | Tests | Covers |
|---|---|---|
| `tests/test_true_daily_open.py` | 59 | Definition, open-price semantics, immutability, identity, zero-lag confirmation, EST/EDT, both DST transitions, the local-time invariant, weekend/holiday/missing bars, coarse grids, configuration, events, `latest_at`, vectorised-vs-reference |
| `tests/test_true_daily_open_leakage.py` | 31 | Observability window, future bars inert (incl. mutating the boundary bar's own H/L/C **and** a control that mutating its open *does* change the level), the naive alternatives — UTC-day open, nearest bar, previous close, the 17:00 boundary, a frozen UTC offset — batch == prefix == streaming, the shared-gate source guard, isolation, determinism |
| `tests/test_true_daily_open_real_data.py` | 160 | Real EURUSD + XAUUSD on 1m/5m/15m/1H/4H: prices matching real bars, four-distinct-price discrimination, cross-timeframe agreement, both real DST cases, weekend closure, no reopen substitution, coarse-grid availability, vectorised-vs-reference, leakage, prefix replay, and an R2-01…R2-05 regression check |

## 12. What this story deliberately does not do

Trading Day Open (17:00), PDH/PDL/PWH/PWL changes, Premium/Discount, dealing ranges,
OTE, entry models, order/breaker/mitigation blocks, IFVG, BPR, SMT, CSD, setup scoring,
and anything involving ML or Kronos. The engine gains exactly one primitive.
