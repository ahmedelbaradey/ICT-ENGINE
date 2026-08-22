# R2-11 — Multi-Timeframe Context — tasks

Story: [R2-11-MTF-STORY.md](../../docs/features/R2-11-MTF-STORY.md) ·
Concept map: [R2-11-CONCEPT-MAP.md](../../docs/features/R2-11-CONCEPT-MAP.md) ·
Master: [Phase-2-Market-Intelligence-STORY.md](../../docs/Phase-2-Market-Intelligence-STORY.md)

**Status: SPECIFIED — awaiting approval. No code written.**
New module `ict_kronos/ict/mtf.py`. `MTF_SCHEMA_VERSION = "r2-11.1"`.

Closes HANDOFF open item 2 — *"multi-timeframe assembly is still unbuilt and is now the
largest known gap"*. This is the **highest-leakage-risk story in the phase**.

## Prerequisites

| # | Prerequisite | Status |
|---|---|---|
| 1 | R2-09 complete and approved (HTF pool proximity is in scope) | ⛔ **BLOCKING** |
| 2 | R2-10 complete and approved (sequence, not dependency) | ⛔ **BLOCKING** |
| 3 | DST evidence on disk — March 2026, with **both** the US (03-08) and EU (03-29) transitions | ✅ **already satisfied** by `production-native-2026-02_08` |
| 4 | Explicit approval of this story | ⛔ **BLOCKING** |

R2-08.2 acquired six contiguous native months (2026-02 → 2026-08) for both symbols at
1H/4H/1D, so **no backfill is required and R2-11 is unblocked on data**. March 2026 holds
531 EURUSD and 508 XAUUSD hourly bars. `real-2024-03-08_12` could not have substituted: it
has **no 4H and no 1D partition**, so it cannot exercise MTF alignment at all.

## Tasks

| ID | Task | Notes | Status |
|---|---|---|---|
| R2-11-a | `MtfConfig` — enable, memoise, include-HTF-pools, distance reference | Frozen dataclass, `as_dict()`. **The hierarchy itself is NOT configurable** | ⬜ |
| R2-11-b | Hierarchy **computed** from `PRODUCTION_TIMEFRAMES`, not listed | A test asserts it equals `1H→{4H,1D}`, `4H→{1D}`, `1D→{}`, so it follows automatically if the universe changes | ⬜ |
| R2-11-c | Alignment: the last HTF bar with `close_time <= T`, via `latest_closed_bar`; **`align_htf_context()` reused UNCHANGED** for the raw aligned bar | Story §4.1. **A bar closing exactly at `T` IS usable** | ⬜ |
| R2-11-d | **An HTF context is an `ICTMarketState` built at `aligned_htf_close`** | Story §3. The design decision that keeps this story small and carries no ICT logic of its own | ⬜ |
| R2-11-e | HTF pool proximity from R2-09's `picture_at(aligned_htf_close)` | Distances re-measured against the **base** close by default | ⬜ |
| R2-11-f | `MtfContext` — frozen; `confirmation_timestamp == aligned_htf_close` | Satisfies `Confirmable`; `filter_observable` accepts it with no new code | ⬜ |
| R2-11-g | Staleness: `staleness_bars`, `alignment_lag_minutes` | The honest representation of every weekend and gap. **Never a threshold** | ⬜ |
| R2-11-h | Three-value unavailability: `NO_HIGHER_TIMEFRAME` · `NO_CLOSED_HTF_BAR` · `HTF_SERIES_MISSING` | "No such thing", "not yet", "data missing" are three facts | ⬜ |
| R2-11-i | Curated projection — exactly the fields of story §5.4, no more | `bars_since_break_htf_bars` **carries its unit in its name** | ⬜ |
| R2-11-j | Alignment / disagreement codes | `0` = disagree, `None` = could not ask. Never collapsed | ⬜ |
| R2-11-k | `MtfPicture` + `MtfContextBuilder` with injected views | Built once per frame, never per instant | ⬜ |
| R2-11-l | **Memoise HTF states by `(htf, aligned_htf_close)`** | Design property, not optimisation: 240 hourly instants share one daily state. ~3× → ~1.03× | ⬜ |
| R2-11-m | A test asserting memoised and non-memoised builders produce **identical** pictures | | ⬜ |
| R2-11-n | NaN → `None` at the `align_htf_context` boundary | The R2-07 audit's NaN-sentinel lesson: a `NaN` in a record breaks equality and reports a phantom streaming difference | ⬜ |
| R2-11-o | Refuse `base == htf` and `base > htf` | `MtfSpecError`. R2-11 never projects downward | ⬜ |
| R2-11-p | Serialisation + exact round trip | | ⬜ |
| R2-11-q | Config wiring: `app/config.py`, `Settings`, `.env.example` | | ⬜ |
| R2-11-r | Unit tests: normal, edge, malformed, boundary, empty | | ⬜ |
| R2-11-s | **Leakage L1 … L8** (master §6.3); **L7**: mutate **and append** HTF bars closing after `T`; **L3's wick-dependent partition declared** (`htf_high`/`htf_low` may move, ids and alignment may not) | | ⬜ |
| R2-11-t | **L8**: both broken implementations built — `merge_asof` on `timestamp`, and the forming-bar read — and L1/L2/L4 proven to **fail** against each | Report how many instants differ, for each | ⬜ |
| R2-11-u | **The eight mandatory MTF audit tests, on REAL data** | future HTF mutation · forming bar · incomplete bar · boundary crossing · **DST** · weekend · missing HTF candle · provider gap | ⬜ |
| R2-11-v | Three-link provenance chain: id resolves · observable at `aligned_htf_close` · `aligned_htf_close <= as_of` | Reuses `assert_provenance_resolves` / `assert_sources_observable_first` unchanged | ⬜ |
| R2-11-w | Identity: 5 collision cases | **Case 5 is real and recurs daily** — a 4H and a 1D bar both close at 00:00 UTC, so identity on `close_time` alone would collide every day | ⬜ |
| R2-11-x | Streaming: batch == prefix == bar-by-bar, with **exactly one inherited** asymmetry | HTF True Daily Open; prefix sees staler. **A test asserts it never inverts** | ⬜ |
| R2-11-y | Real-data: both symbols × 1H/4H/1D on `production-native-2026-02_08` (six months, DST included) | | ⬜ |
| R2-11-z | Guard tests | No `timestamp` join · no `resample`/`build_timeframe_stack` import · no `zoneinfo` · no detector re-implementation · no hand-rolled observability · no downward projection | ⬜ |
| R2-11-1 | **R2-07's timeframe-locality guards must still pass, untouched** | Story §10.4. `mtf.py` is a new module; `market_state.py` is not modified by this story | ⬜ |
| R2-11-2 | Performance: alignment per row, memoised vs not, batch per month | | ⬜ |
| R2-11-3 | Documentation: `docs/ict/mtf.md`; HANDOFF open item 2 closed in the same commit | | ⬜ |
| R2-11-4 | Regression: R2-01 … R2-10 provably untouched — **especially `align_htf_context`** | Consumed exactly as it is | ⬜ |
| R2-11-5 | Full suite + ruff + black; one local commit; **STOP** | No push | ⬜ |

## Decisions that change what is being measured

1. **An HTF context is a full `ICTMarketState`, not copied columns.** Every observability
   decision was already made by R2-07 against `as_of = aligned_htf_close <= T`, so the
   alignment is **conservative by construction** — it can only show less than a live
   observer at `T` would have, never more.
2. **Join on `close_time`, never `timestamp`.** The repository's single most-guarded rule.
3. **Completed HTF bars only.** Forming bars are excluded in v1 and recorded as the leading
   v2 candidate — and if ever added, only as separately named fields.
4. **Stale contexts stay available, with their age published.** The last known 4H bar
   genuinely is the last known 4H bar. Marking it unavailable would discard true
   information; a staleness threshold is refused for the same reason a coverage threshold is.
5. **The hierarchy is computed, not listed.**
6. **HTF distances are measured from the base close** (configurable; `htf_close` is also
   projected, so nothing is lost).
7. **`0` = disagree, `None` = could not ask.** A warm-up row and a contested market must not
   look the same.
8. **COT is not routed through this layer.** Different clock, different alignment mechanism.

## Not implemented, and why

| Item | Reason |
|---|---|
| Forming-bar exposure | Concept map **F1/F2**; no precedent for running state entering a state object |
| Weighted HTF bias / "higher timeframe wins" | A weighting is a hypothesis |
| All 56 local features per HTF | 112 columns, most with ambiguous HTF units |
| HTF feature vectors joined onto base rows | **Circular** — R2-13 projects a state that contains the MTF context |
| Re-deriving HTF structure from the base frame | Duplicates ten detectors; needs `resample` |
| Downward projection (HTF → LTF) | Not a thing this layer does |
| A fabricated NY-anchored daily | production_universe.md §2 option B — an architectural decision, out of scope |
| `ICTMarketState` / `ICTFeatureVector` wiring | R2-12 and R2-13 |

## Deliverables (every story, no exceptions)

| # | Deliverable |
|---|---|
| 1 | Implementation |
| 2 | Tests — unit · boundary · leakage · provenance · identity/collision · real-data · serialisation · guard/contract · streaming/point-in-time |
| 3 | Documentation |
| 4 | Completion report |
| 5 | Performance measurements |
| 6 | **Leakage matrix** — one row per L-ID × component, **no blank cells**; every `n/a` carries a reason |
| 7 | **Provenance matrix** — one row per emitted id field: id kind, registry it resolves against, observable-by check |
| 8 | Real-data results |
| 9 | Limitations and ambiguities |
| 10 | Git status and commit information |

**If provider reality conflicts with this specification, STOP and report the conflict.
Do not invent data and do not weaken the rule.**

## Hard stop

```
R2-11 complete -> audit -> completion report -> COMMIT (local)
               -> STOP -> explicit approval required before R2-12
```

The completion report states: files, tests, static analysis, performance (memoised and not),
**both L4 divergence counts**, the DST evidence from March 2026, the single permitted
asymmetry and the proof it does not invert, ambiguities, assumptions, rejected alternatives
and limitations.

**If the DST months ever become unavailable, R2-11 stops and reports rather than shipping
with DST unvalidated.** Synthetic DST fixtures are a supplement, never a substitute —
R2-04 §13 records a real bug that only synthetic data exposed, and the lesson runs both
ways: both kinds of evidence are required.
