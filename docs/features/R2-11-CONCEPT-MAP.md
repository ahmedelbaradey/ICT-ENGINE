# R2-11 — Multi-Timeframe Context — CONCEPT MAP

**Specification checkpoint. Written before `ict_kronos/ict/mtf.py` exists.**
Story: [R2-11-MTF-STORY.md](R2-11-MTF-STORY.md)

---

## 1. The shape of the problem

The join is one line:

```python
pd.merge_asof(base, htf, on="close_time", direction="backward")
```

**All of the risk is in which column you join on, and in what you join.** The resampler's
own module docstring calls this *"the single most dangerous place in Phase 1"* and names the
failure exactly:

> *"A 4H bar timestamped 08:00 is not knowable until 12:00. Joining it onto a 5M observation
> at 09:15 — which every naive `merge` or `reindex(method='ffill')` on the open timestamp
> will happily do — leaks four hours of future information… The model looks brilliant in
> backtest and is worthless."*

R2-11 is an **alignment problem wearing a join problem's clothes**, and the repository
already solved the join. What it has never solved is aligning *ICT state* rather than
*columns* — HANDOFF open item 2, *"the largest known gap"*.

---

## 2. Dependency graph

```
persisted 1H partition ──► ICTEngineView(1H) ──► local state          (R2-07, unchanged)
persisted 4H partition ──► ICTEngineView(4H) ──┐
persisted 1D partition ──► ICTEngineView(1D) ──┤
                                               ├──► MtfContextBuilder ──► MtfPicture
LiquidityModel(4H), LiquidityModel(1D) ────────┘         (R2-09)
```

Two things to read off it:

1. **No detector appears.** R2-11 consumes `ICTEngineView` and `LiquidityModel`, both of
   which already applied every observability decision.
2. **`resample` does not appear.** Every frame is read from a persisted, hashed,
   manifest-backed partition (master story §4.2). A guard test asserts the import is absent.

---

## 3. What an HTF context *is* — four candidates

The single most consequential design question in the story.

### M1 — An `ICTMarketState` built at an HTF bar close ≤ `T` *(SELECTED)*

| | |
|---|---|
| ✅ | **Every observability decision was already made by R2-07**, correctly, against `as_of = htf_close`, which is itself `<= T`. R2-11 adds no new causality reasoning at all |
| ✅ | **Conservative by construction.** It can only show *less* than a live observer at `T` would have, never more |
| ✅ | Full ICT semantics on the higher timeframe — structure, ranges, liquidity, composites — with **zero** duplicated logic |
| ✅ | `state_at()` returns `None` off a bar close, so asking at the wrong instant **fails loudly** rather than leaking |
| ✅ | Memoisable: all 240 hourly instants inside one daily bar share one HTF state |
| ⚠️ | Requires an `ICTEngineView` per HTF. Accepted — they are built once per frame, not per instant |

**Verdict: selected.** It is the only candidate where R2-11 carries no ICT logic of its own.

### M2 — Copied OHLC columns via `align_htf_context`

| | |
|---|---|
| ✅ | Already implemented, already tested, already the sanctioned join |
| ✅ | Cheapest possible |
| ❌ | **Throws away every ICT concept**, which is the entire reason the engine exists. "The 4H high" is not "the 4H structure is bullish and price is in 4H discount" |
| ❌ | A model would have to re-derive HTF structure from four columns — which is exactly the re-derivation this architecture forbids |

**Verdict: rejected as the primary mechanism, retained as a component.** `align_htf_context`
is still used for the raw aligned bar (`h4_open/high/low/close`), and it remains the only
sanctioned bar-level join.

### M3 — Precomputed HTF feature vectors joined on `close_time`

Build `ICTFeatureVector`s for the HTF, then `merge_asof` them onto base rows.

| | |
|---|---|
| ✅ | Reuses R2-13's projection; conceptually tidy |
| ❌ | **Circular.** R2-13 projects a state that contains the MTF context, so MTF cannot be built from R2-13 |
| ❌ | Adds 56 columns per HTF, most of whose HTF meaning is ambiguous (what is "4H `bars_since_cisd`" on a 1H row?) |
| ❌ | Loses the ids — the vector deliberately carries none — so multi-timeframe provenance becomes unprovable |

**Verdict: rejected.** The circularity alone is disqualifying; the provenance loss is worse.

### M4 — Re-derive HTF structure inside R2-11 from the base frame

| | |
|---|---|
| ✅ | One frame, no partition loading |
| ❌ | **Duplicates ten detectors** and creates a second definition of every ICT concept — the failure the R2-05.x import guards exist to prevent |
| ❌ | Would need `resample`, forbidden by master story §4.2 |
| ❌ | The re-derived structure would silently disagree with the real 4H analysis at the margins, and the disagreement would be indistinguishable from a real signal |

**Verdict: rejected outright.**

### Ranking

| Rank | Candidate | Causal | No duplication | Full semantics | Provenance | Outcome |
|---|---|---|---|---|---|---|
| **1** | **M1 HTF state at an HTF close** | ✅ | ✅ | ✅ | ✅ | **implemented** |
| 2 | M2 copied OHLC | ✅ | ✅ | ❌ | ⚠️ | used as a component |
| 3 | M3 HTF feature vectors | ✅ | ✅ | ⚠️ | ❌ | rejected (circular) |
| 4 | M4 re-derivation | ⚠️ | ❌ | ✅ | ❌ | rejected outright |

---

## 4. Forming bars — three candidates

| # | Candidate | Verdict |
|---|---|---|
| F1 | **Completed HTF bars only** | ✅ **selected, v1.** The brief's default; and the repository has never fed running state into a state object |
| F2 | Expose the forming bar's running high/low as separate, clearly named fields | ⚠️ **Deferred, the leading v2 candidate.** There *is* precedent for labelled running state (`RunningSessionState`, `PendingPeriod`) — but neither has ever entered `ICTMarketState`, and R2-04 is explicit about why: *"'the current day's high so far' is real information a live system has — but it is not a previous-day high, and conflating them is exactly the leak this module prevents"* |
| F3 | Fold forming-bar information into the existing fields | ❌ **Never.** It would make `h4_high` mean "closed high, or running high, depending" — a field whose meaning depends on when you asked |

If F2 is ever built, the requirement is absolute: **separately named fields that no existing
field's meaning depends on.**

---

## 5. Staleness — three candidates

At a Tuesday 09:00 1H bar, the aligned daily bar closed at 00:00 UTC — nine hours ago. Over a
weekend the aligned 4H bar can be two days old.

| # | Candidate | Verdict |
|---|---|---|
| S1 | **Available, with `staleness_bars` and `alignment_lag_minutes` published** | ✅ **selected.** The last known 4H bar genuinely *is* the last known 4H bar. Publishing its age is the honest representation |
| S2 | Mark stale contexts unavailable beyond a threshold | ❌ **Discards true information** and requires a staleness threshold nothing supports — the same objection as a coverage threshold, which the engine refuses outright (data_coverage.md) |
| S3 | Say nothing about staleness | ❌ Makes a weekend-stale 4H context indistinguishable from a fresh one. The dataset would contain the difference and hide it |

S1 is the same shape as R2-10's `cot_report_age_days` and R2-08's `coverage_ratio`:
**measure it, publish it, never threshold on it.**

---

## 6. Alignment and disagreement — three candidates

| # | Candidate | Verdict |
|---|---|---|
| G1 | **A code (`−1/0/+1`) plus a disagreement count, with every per-timeframe direction also exposed** | ✅ **selected.** The code is a convenience; the raw directions are the evidence |
| G2 | A weighted "HTF bias score" | ❌ A weighting is a hypothesis (market_state.md §9). "Higher timeframe wins" is a trading belief, not a measurement |
| G3 | Nothing — just the raw per-timeframe directions | ⚠️ Defensible and nearly selected. G1 wins only because "do all three agree" is a genuine, deterministic, zero-parameter fact that every consumer would otherwise recompute identically |

**The load-bearing detail of G1:** `0` means *"they disagree"* and `None` means *"we could
not ask"*. A warm-up row and a contested market must not look the same. This is the
`UNKNOWN ≠ NEUTRAL` rule applied to a new field.

---

## 7. Distance reference — two candidates

`nearest_buy_side_pool_points` inside a 4H context: measured from what?

| # | Candidate | Verdict |
|---|---|---|
| D1 | **The base bar's close at `T`** | ✅ **selected, default.** A decision at `T` acts on price at `T`. The 4H close is hours stale |
| D2 | The aligned HTF bar's close | ⚠️ Configurable, and `htf_close` is projected anyway, so nothing is lost |

Recorded because a reader will otherwise assume D2 and misread the column.

---

## 8. Leakage criteria inherited

L1 … L8 from the master story §6.3. **Two** deliberately broken implementations, because there are two independent, classic
MTF leaks. Both are built and **L8** asserts the leakage tests catch each:

| | The plausible leaky implementation |
|---|---|
| **B1** | `merge_asof` on `timestamp` instead of `close_time` — the failure the resampler docstring describes and `test_leakage.py::test_naive_join_on_open_timestamp_would_leak` already demonstrates at bar level. R2-11 extends it to state level |
| **B2** | Read the forming HTF bar's running high/low |

Both built, both proven to disagree, with the number of differing instants reported.

### 8.1 Provenance is a three-link chain

```
id resolves in the HTF analysis                        L6a
source.confirmation_timestamp <= aligned_htf_close     L6b
aligned_htf_close <= as_of                             L6c
```

Each link checkable, and the first two use `assert_provenance_resolves` /
`assert_sources_observable_first` from R2-05.2 unchanged. **Never copy an HTF value without
being able to prove it existed at the base `as_of`** — this chain is that proof.

### 8.2 The eight mandatory MTF audit tests

future HTF mutation · forming-bar mutation · incomplete HTF bar · boundary crossing · DST ·
weekend · missing HTF candle · provider gap. Each named individually in the story §8.3, each
run on **real** data.

---

## 9. Why a DST month is required — and why it is already on disk

A UTC-anchored 4H grid does not shift at a DST transition, so the alignment *should* be
unaffected. That is exactly the kind of "obviously fine" property that is quietly wrong when
someone converts to local time "to be safe" — and it would be wrong in only half the year,
in none of the summer test data.

March 2026 is the only month containing **both** the US transition (2026-03-08) and the EU
transition (2026-03-29). The `real-2024-03-08_12` fixture contains the 2024 US transition
but has **no 4H and no 1D partition**, so it cannot exercise MTF alignment at all.

**R2-08.2 already acquired it.** `production-native-2026-02_08` spans 2026-02 → 2026-08 and
holds 531 EURUSD / 508 XAUUSD hourly bars in March alone. The prerequisite is satisfied by
data on disk; no backfill is required.

**Both kinds of evidence are required, and the reason is on record.** R2-04 §13 documents a
real bug that *"real FX data hides completely"* and that only synthetic 24/7 data exposed;
conversely, only real data exposes a genuine provider gap. Synthetic DST fixtures are a
supplement, never a substitute.

---

## 10. Ambiguity register

Full register in [the story](R2-11-MTF-STORY.md) §11 (C1 … C9). The two decisions most
likely to be questioned:

| Decision | Why it is what it is |
|---|---|
| **Only a curated subset of HTF fields is projected** | Copying all 56 per HTF gives 112 columns, most of them counts whose HTF unit is ambiguous. `bars_since_break` is renamed `bars_since_break_htf_bars` precisely because on a 1H row the unit is load-bearing and invisible |
| **COT is not routed through the MTF layer** | The brief's sketch places it under 1D. COT aligns by *release timestamp*, not by *bar close* — a different clock. Routing it here would mean one alignment mechanism pretending to be two, and the first additional non-bar dataset would force it apart again. R2-12 composes them side by side, which is what a composition layer is for |

---

## 11. What R2-11 does not build

Forming-bar exposure · a weighted HTF bias · a "higher timeframe wins" rule · timeframes
outside `PRODUCTION_TIMEFRAMES` · downward projection (HTF → LTF) · a fabricated NY-anchored
daily · any resampling · COT alignment · `ICTMarketState` wiring (R2-12) ·
`ICTFeatureVector` columns (R2-13) · any change to `align_htf_context`, which is consumed
exactly as it is · any ML, probability, label or normalisation · any backtest rule.
