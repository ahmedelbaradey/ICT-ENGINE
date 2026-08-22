# R2-09 — Liquidity Model — tasks

Story: [R2-09-LIQUIDITY-STORY.md](../../docs/features/R2-09-LIQUIDITY-STORY.md) ·
Concept map: [R2-09-CONCEPT-MAP.md](../../docs/features/R2-09-CONCEPT-MAP.md) ·
Master: [Phase-2-Market-Intelligence-STORY.md](../../docs/Phase-2-Market-Intelligence-STORY.md)

**Status: SPECIFIED — awaiting approval. No code written.**
New module `ict_kronos/ict/liquidity_model.py`. `LIQUIDITY_MODEL_VERSION = "r2-09.1"`.

R2-09 closes [liquidity.md](../../docs/ict/liquidity.md) §14 limitations **2** (no pool
clustering) and **3** (no internal/external distinction). It **wraps** `LiquidityDetector`;
it does not modify it.

## Prerequisites

| # | Prerequisite | Status |
|---|---|---|
| 1 | R2-04 `LiquidityDetector` approved | ✅ |
| 2 | R2-06 `DealingRangeDetector` approved | ✅ |
| 3 | `production-native-2026-02_08` on disk — six native months, 1H/4H/1D, both symbols | ✅ |
| 4 | **Explicit approval of this story** | ⛔ **BLOCKING** |

## Pre-implementation verification (§15) — COMPLETE, 2026-08-21

Performed read-only, before any production code. No file under `ict_kronos/` was modified.

| # | §15 step | Result |
|---|---|---|
| 1 | Read R2-04 liquidity implementation | ✅ `ict_kronos/ict/liquidity.py`, 892 lines. Levels, sweeps, `PENDING/ACTIVE/APPROACHED/SWEPT`, side fixed at creation, `closed_beyond`, frozen records |
| 2 | Read R2-06 dealing range | ✅ `range_at(as_of)` supplies the structural range for internal/external. `position_of` unclamped; degenerate range returns NaN |
| 3 | Read R2-07 market state / features | ✅ `STATE_VERSION`/`FEATURE_VERSION` = `r2-07.1`; 56 features; timeframe-local; `source_ids()` is the provenance enumeration |
| 4 | Read R2-08 production universe + manifests | ✅ `production-native-2026-02_08`; native 1H + native 1D; 4H = four native 1H; `tick_dependency: false`, `minute_dependency: false` |
| 5 | Verify the production timeframe guard | ✅ `assert_production_pair` **raises** (`ProductionUniverseError`) for `M1/M5/M15` and for any non-production timeframe. It never converts. `>1D` is not a `Timeframe` member, so it is unrepresentable |
| 6 | Verify six-month calibration evidence | ✅ Six contiguous months **2026-02 … 2026-07**, both symbols × 1H/4H/1D. Measured below |
| 7 | Update the R2-09 task specification | ✅ This block |
| 8 | State any ambiguity | ✅ Three surfaced; **all three now resolved by the final brief** — see below |

### 6a. Measured calibration evidence (six months, read-only)

`pool_tolerance_points = median bar range / 10`, the declared uniform divisor.

| Pair | Bars | Median range (pts) | Median \|c−o\| (pts) | **Pool tolerance (pts)** |
|---|---|---|---|---|
| EURUSD 1H | 3 120 | 102.0 | 41.0 | **10.2** |
| EURUSD 4H | 754 | 210.5 | 93.0 | **21.0** |
| EURUSD 1D | 156 | 536.5 | 200.5 | **53.6** |
| XAUUSD 1H | 2 955 | 19 020.0 | 7 980.0 | **1 902.0** |
| XAUUSD 4H | 642 | 41 240.0 | 17 260.0 | **4 124.0** |
| XAUUSD 1D | 155 | 99 160.0 | 47 460.0 | **9 916.0** |

**These supersede the provisional table in the story**, which was derived from July 2026
alone. The drift is material — EURUSD 1H median range 85 → 102 pts, XAUUSD 1H 13 840 →
19 020 pts — which is precisely why §2 requires six-month calibration. The story's table
must be replaced with these values at implementation time.

**Note for R2-08:** `PRODUCTION_TARGET_PARAMETERS` still carries the July-only figures and
its own docstring flags that they *"should be re-derived from a longer history"*. Re-deriving
them is **out of R2-09's scope** and is not done here; it is recorded as a follow-up.

### 8a. The three architectural blockers — ALL RESOLVED

| # | Decision | Resolution |
|---|---|---|
| **AD-1** | Does `LiquidityModel` receive the bar frame? | ✅ **APPROVED — measurement only.** Input is `(LiquidityAnalysis, DealingRangeAnalysis, PointInTimeBarFrame)`. R2-04 stays authoritative for detection, level creation, lifecycle, sweep detection, `SWEPT`, identity and provenance. R2-09 may measure touch count, age, distance, structural context and lifecycle status from **already-detected** levels. The frame is an **observation source, not a second detector** — story §2a |
| **AD-2** | What does "displacement/reaction" mean? | ✅ **RESOLVED — do not invent.** No deterministic definition exists; R2-03's `displacement_ratio` measures structural-break displacement and **may not be reused**. `reaction = NOT_AVAILABLE`, an explicit schema sentinel. The five measurable components ship without it — story §5a.2. Also removed: `type_rank`, an invented level-type ordinal from an earlier draft |
| **AD-3** | Documentation and task paths | ✅ **RESOLVED — preserve the existing structure.** Task files stay in `tasks/Phase-2-ICT-Engine/`. **No approved artefact is moved or renamed.** Only the minimum new documentation files are created, and no directory is restructured for documentation's sake |

**R2-09 has no remaining architectural blockers. It is specified and awaiting implementation
approval.**

## Tasks

| ID | Task | Notes | Status |
|---|---|---|---|
| R2-09-a | `LiquidityModelConfig` — pool tolerance, min cardinality, touch tolerance, approach tolerance | Frozen dataclass, validated in `__post_init__`, `as_dict()`. **No literal in logic** | ⬜ |
| R2-09-b | Derive per-pair pool tolerances from the **six-month** measured median bar range — ONE-TIME, OFFLINE, output committed as a constant table | The `PRODUCTION_TARGET_PARAMETERS` shape; a uniform divisor; `rationale` on every row; **recomputed and asserted, never hand-copied** | ⬜ |
| R2-09-c | `LiquidityPool` — frozen, id from sorted member ids, `composite_confirmation()` | Story §8.1. `is_observable_at` delegates to the one contract predicate | ⬜ |
| R2-09-d | Single-linkage clustering **after** the observability gate | Story §4.2. Sort by `(price, level_id)`; `level_id` breaks ties so the id is run-stable | ⬜ |
| R2-09-e | Internal / external / **unknown** against the R2-06 range | Story §4.4. `UNKNOWN` is a third value, never collapsed into `INTERNAL` | ⬜ |
| R2-09-f | **`APPROACHED` = TOUCHED.** Enable R2-04's existing state with the calibrated tolerance and add `approach_count` | **No `TOUCHED` enum. No rename. No parallel concept.** Story §5 | ⬜ |
| R2-09-g | `CONFIRMED_TAKEN` derived from R2-04's existing `closed_beyond` | A new **name**, not a new rule. `SWEPT` is untouched | ⬜ |
| R2-09-h | Direction, asymmetry, nearest-side, relative position | Story §6. **No target is claimed** | ⬜ |
| R2-09-h2 | **Significance** — the **seven** approved components, each exposed separately | `pool_cardinality` (**size**) · `pool_density` · `span_points` · `approach_count` · `age_bars` · `distance_to_current_price_points` · `structural_context`. Size and density are **components, not attributes**. Story §5a.1 | ⬜ |
| R2-09-h2c | `pool_density = cardinality / (1 + span_points / pool_tolerance_points)` | Deterministic, dimensionless, no singularity at `span = 0`. `cardinality/span` **rejected** — singular at the tightest pool. Story §5a.1a | ⬜ |
| R2-09-h3 | `reaction_status = NOT_AVAILABLE` — an explicit schema sentinel, **no heuristic manufactured** | AD-2. Documentation states reaction is intentionally not scored until a deterministic definition is approved | ⬜ |
| R2-09-h3b | `significance_score` composite — **optional, OFF by default**, deterministic, documented, point-in-time safe, reproducible, independently tested | A guard test asserts it never filters, ranks-and-truncates, or selects | ⬜ |
| R2-09-h4 | **Nearest VALID candidate** — validity is observability + unswept + side + min cardinality | Story §6.0. Validity deliberately excludes a significance floor | ⬜ |
| R2-09-h5 | **Calibration guard**: `LiquidityModel` computes no median, quantile, `std`, rolling statistic or dataset-wide percentile at runtime | Story §4.5a. Tolerances come from a versioned constant table, calibrated offline | ⬜ |
| R2-09-h6 | **Bar-frame guard (AD-1)**: no level construction, no lifecycle write-back, no `SWEPT` redefinition, no competing identity, no bar with `close_time > as_of` | Story §2a.2 | ⬜ |
| R2-09-i | `LiquidityPicture` — the point-in-time answer R2-12 consumes | Story §8.2 | ⬜ |
| R2-09-j | Serialisation: `as_dict()` / `from_dict()`, exact round trip | `None` for missing, never `0`, never `NaN` | ⬜ |
| R2-09-k | Config wiring: `app/config.py`, `Settings`, `.env.example` | Env-only frozen dataclass, existing shape | ⬜ |
| R2-09-l | Unit + **boundary** tests | Normal, malformed, empty, single-level, degenerate, and the full §13a edge-case table — **all 21 cases named individually** | ⬜ |
| R2-09-m | Pool-count sensitivity to the tolerance divisor at 5 / 10 / 20 | **Description only.** No value is chosen because it produced a nicer pool count | ⬜ |
| R2-09-n | **Leakage suite L1 … L8** | Master §6.3. **L3's wick-dependent partition is declared, not discovered**; **L7 is `n/a` with a reason and an import guard**, never a blank cell | ⬜ |
| R2-09-o | **§15 deliberately incorrect implementations** built and proven to **fail**: (a) **count future touches after `as_of`**, (b) cluster-before-gate | Report **how many** instants differ, for each | ⬜ |
| R2-09-p | **L4 historical control** paired with L1/L2 in one test: mutate before `T` ⇒ **changes**; mutate after `T` ⇒ **does not** | Neither half can exist alone | ⬜ |
| R2-09-q | Confirming-bar wick mutation + control | The level must not become observable earlier | ⬜ |
| R2-09-r | Identity stress: 6 collision cases | Story §12. Case 2 (same price, different members ⇒ different ids) is the one that proves identity is not price-based | ⬜ |
| R2-09-s | Streaming: batch == prefix == bar-by-bar, **no asymmetry claimed** | 1H every cut, 4H true bar-by-bar | ⬜ |
| R2-09-t | Real-data: EURUSD + XAUUSD × **1H/4H/1D only** on `production-native-2026-02_08` (six contiguous months) | 8 named cases, story §13. **Timeframe-local — no MTF join** | ⬜ |
| R2-09-u | Guard tests | No hand-rolled observability · no swing/session/structure/resample/`build_h4_from_native_h1` import · no timezone · no model-training import · **production timeframe guard rejects 1M/5M/15M and anything <1H or >1D** | ⬜ |
| R2-09-u2 | **Timeframe-locality guard**: no resampler import, no `build_h4_from_native_h1`, no second partition read, no implicit 1H→4H→1D hierarchy | Story §0. R2-11 owns MTF | ⬜ |
| R2-09-v | Performance measured per stage, **not optimised** | R2-04 input · pool construction · approach measurement · significance · serialisation · total. Report ms/bar, level count, pool count, bar count, scaling. **Report the inherited `O(bars × active levels)` risk** — story §13b | ⬜ |
| R2-09-w | Documentation: `docs/ict/liquidity_model.md`; cross-reference `liquidity.md` §14 | | ⬜ |
| R2-09-x | HANDOFF updated **in the same commit** | Status, gotchas, open items | ⬜ |
| R2-09-y | Regression: R2-01 … R2-08 provably untouched | `git diff --stat` names none of them | ⬜ |
| R2-09-z | Full suite + `ruff check .` + `black --check .`; one local commit; **STOP** | No push | ⬜ |

## Decisions that change what is being measured

Flagged rather than buried. Each silently changes what a pool *is*.

1. **Pools are a derived point-in-time view, not a confirmed event.** No lifecycle, no
   `SWEPT` state, no invalidation of its own — its members have all of those.
2. **The pool tolerance is not the equal-highs tolerance.** They answer different questions
   and are independently configured. A test asserts changing one does not change the other.
3. **Tolerance is per (symbol, timeframe), from measured bar range.** A point is not a unit
   of volatility.
4. **`SWEPT` keeps R2-04's meaning exactly.** `CONFIRMED_TAKEN` is a derived name over
   `closed_beyond`, not a redefinition.
5. **Single linkage, so a chain A–B–C is one pool** even when `dist(A,C) > tolerance`.
   Visible via `span_points`.
6. **`UNKNOWN` zone is a third value.** Before the first confirmed break there is no range,
   and "inside a range that does not exist" is not a fact.
7. **No target, no ranking, no strength score.** Distance and cardinality are exposed; the
   claim is not.

## Not implemented, and why

| Item | Reason |
|---|---|
| Previous-month levels | No source treats a calendar month as an ICT reference the way it treats day and week |
| "Previous significant highs" | No deterministic definition exists; swing significance is an open upstream question (HANDOFF item 6) |
| Trendline / round-number / volume-profile liquidity | Not deterministic, not ICT, or the data does not exist (Dukascopy volume is a tick count) |
| Pool ranking or strength | No source defines one. The raw material for one is exposed instead |
| Confirmed pool events with a lifecycle | Concept map candidate **P2** — membership changes after confirmation, which is mutation |
| Merged super-levels | Concept map **P3** — destroys the PDH-vs-session-high distinction R2-04 exists to preserve |
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
R2-09 complete -> audit -> completion report -> COMMIT (local)
               -> STOP -> explicit approval required before R2-10
```

The completion report states: files changed, test counts, static-analysis result, measured
performance, pool statistics per symbol × timeframe (count, median cardinality, unpooled
fraction, internal/external/unknown split, no-range fraction), the L4 divergence count, every
ambiguity encountered, every assumption, every rejected alternative, and every limitation.
