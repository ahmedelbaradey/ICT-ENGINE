# R2-12 — Market State v2 — tasks

Story: [R2-12-MARKET-STATE-V2-STORY.md](../../docs/features/R2-12-MARKET-STATE-V2-STORY.md) ·
Concept map: [R2-12-CONCEPT-MAP.md](../../docs/features/R2-12-CONCEPT-MAP.md) ·
Master: [Phase-2-Market-Intelligence-STORY.md](../../docs/Phase-2-Market-Intelligence-STORY.md)

**Status: SPECIFIED — awaiting approval. No code written.**
Modifies `ict_kronos/ict/market_state.py` **additively**. `STATE_VERSION`: `r2-07.1` →
`r2-12.1`.

R2-12 contains **no analysis**. It reads three point-in-time APIs and copies ids.

## Prerequisites

| # | Prerequisite | Status |
|---|---|---|
| 1 | R2-09 complete and approved | ⛔ **BLOCKING** |
| 2 | R2-10 complete and approved | ⛔ **BLOCKING** |
| 3 | R2-11 complete and approved | ⛔ **BLOCKING** |
| 4 | Explicit approval of this story | ⛔ **BLOCKING** |

## Tasks

| ID | Task | Notes | Status |
|---|---|---|---|
| R2-12-a | `liquidity_context` — a **separate** context, not fields on `LiquidityContext` | Different lifetimes, identities and provenance registries. `source_ids()` groups by originating detector | ⬜ |
| R2-12-b | `cot_context` — **one snapshot PER FAMILY**, never blended, `report_type` always exposed, **carrying `mapping`** | A state saying "net is +140 000" without saying "CME Euro FX futures, an approximation for spot" invites a false reading months later | ⬜ |
| R2-12-c | `htf_context` (`MtfStateContext`) — embeds R2-11's records **by value, unchanged** | One definition of an MTF context in the codebase | ⬜ |
| R2-12-d | `DataQualityContext` from R2-08's `BarCoverage` | **Describes, never filters.** A `DEGRADED_UNKNOWN` bar produces a complete, ordinary state | ⬜ |
| R2-12-e | `EvidenceContext` — named evidence per layer, **no verdict field of any kind** | Not even an advisory one; a verdict field becomes a second bias by use | ⬜ |
| R2-12-f | **`BiasContext` FROZEN — four sources, range 0–4, unchanged** | Story §5. Extending it changes a shipped feature's range and verdict for an unchanged market | ⬜ |
| R2-12-f2 | **Optional `extended_bias`** — distinct enum, distinct field, **OFF by default**, `None` ≠ `UNKNOWN`, `extended_bias_sources` named on the record | Permitted by the revised brief. Five constraints make it impossible to confuse with `bias` — story §5.1a | ⬜ |
| R2-12-f3 | A test asserting a state **with** `extended_bias` has a **byte-identical** `bias` to one without | The property that keeps the original trustworthy | ⬜ |
| R2-12-g | Four new **optional** `ICTMarketState` fields, all defaulting to `None` | The `IctEvent` optional-field pattern | ⬜ |
| R2-12-h | Three new `MarketStateConfig` switches, **all defaulting to `False`** | So `MarketStateBuilder()` with no arguments produces a state byte-identical to R2-07's in every existing field | ⬜ |
| R2-12-i | Three new injected collaborators on `ICTEngineView` / `MarketStateBuilder` | Injected, never constructed inside — the existing reproducibility pattern | ⬜ |
| R2-12-j | Four new `source_ids()` groups; `liquidity_pools.source_range_id` enumerated under `dealing_range` | The R2-07 audit defect reproduced deliberately, one layer up | ⬜ |
| R2-12-k | `as_dict()` gains four keys; **every existing key keeps its exact position and value** | | ⬜ |
| R2-12-l | NaN → `None` translation for every new numeric field | A `NaN` in a record breaks `from_dict(as_dict()) == v` and reports a phantom streaming difference | ⬜ |
| R2-12-m | `STATE_VERSION → r2-12.1` | The shape changed; a dataset records it | ⬜ |
| R2-12-n | Config wiring: `app/config.py`, `Settings`, `.env.example` | | ⬜ |
| R2-12-o | **`tests/test_market_state.py` and `tests/test_market_state_real_data.py` pass with ZERO edits** | 788 + 604 lines. **The compatibility proof.** Any required edit means the change was not additive | ⬜ |
| R2-12-p | `test_market_state_v1_compatibility.py` — every v1 field of a v2 state equals the v1 state's field, on real data, both symbols, all three timeframes | | ⬜ |
| R2-12-q | Unit tests for the four new contexts + `EvidenceContext` | Normal, edge, malformed, boundary, all-disabled | ⬜ |
| R2-12-r | **Leakage L1 … L8** (master §6.3); **L8**: the recompute-instead-of-query implementation built, and L1/L2/L4 proven to **fail** against it | The only leak available to a layer whose every input is gated — and the one a well-meaning optimisation introduces | ⬜ |
| R2-12-s | **Marker-substitution provenance completeness for all four new groups** | A value-based test is not a coverage test. The dealing-range id now reaches `source_ids()` by two paths and `_ids()` deduplicates, so a value-based test would pass with either field unread | ⬜ |
| R2-12-t | Identity: 5 collision cases | **Case 4** — one dealing-range id via two paths — is the R2-07 audit defect, deliberately reproduced | ⬜ |
| R2-12-u | Streaming: **no new asymmetry**; the two inherited ones pinned and proven not to invert | Local + HTF True Daily Open; prefix sees staler | ⬜ |
| R2-12-v | Real-data: both symbols × 1H/4H/1D, `production-native-2026-02_08` (six months, DST included), all layers enabled | | ⬜ |
| R2-12-w | **The observability guard must still pass** — no `confirmation_timestamp <=` in `market_state.py` | With its four-way mutation test of the guard itself. The stripper stays load-bearing: new code will *mention* observability in order to warn against it | ⬜ |
| R2-12-x | Performance per layer, enabled and disabled, so the marginal cost of each is attributable | Baseline: ≈ 2 ms/instant | ⬜ |
| R2-12-y | Documentation: `docs/ict/market_state.md` (§10 rewritten to point at R2-11); HANDOFF in the same commit | | ⬜ |
| R2-12-z | Regression: no detector, no `feature_vector.py`, nothing under `features/` changed | | ⬜ |
| R2-12-1 | Full suite + ruff + black; one local commit; **STOP** | No push | ⬜ |

## Decisions that change what is being measured

1. **`bias` is frozen at four sources.** The conservative choice that looks like the timid
   one. Extending it to seven would change a shipped feature's **range** (0–4 → 0–7) and its
   **verdict** for an unchanged market, making two datasets incomparable under one column
   name — the R2-06 lesson verbatim.
2. **`EvidenceContext` has no verdict field.** Not even advisory.
3. **All four new fields default to `None`; all three switches default to `False`.** R2-12
   lands without changing a single existing number.
4. **Pools get their own context**, so no record's ids resolve against two registries.
5. **Data quality describes, never filters.** No state is withheld, no value nulled, no bar
   dropped.
6. **R2-11 records are embedded by value, not re-flattened.**
7. **R2-12 computes nothing.** Only arithmetic over values the three APIs returned, via the
   existing `_points()` and `_bars_since()` helpers.

## Not implemented, and why

| Item | Reason |
|---|---|
| An extended or second bias | Decision 1 |
| Any weighting of evidence | *"A weight is a hypothesis and this story does not test hypotheses"* |
| Any new interpretation at all | The three new layers contribute **evidence only** |
| Any re-derivation | Every input already applied the gate |
| A data-quality filter | Decision 5 |
| Changes to any of the nine existing contexts | The compatibility claim depends on them being untouched |
| `ICTFeatureVector` columns | R2-13 |
| A replacement `ICTMarketStateV2` class | Concept map **X2** — every consumer branches, twice the surface that can drift |

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
R2-12 complete -> audit -> completion report -> COMMIT (local)
               -> STOP -> explicit approval required before R2-13
```

**The strongest safety property, and the first thing the completion report must state:** if
`tests/test_market_state.py` and `tests/test_market_state_real_data.py` pass **with zero
edits**, the extension is genuinely additive. Any edit to either file is a signal that
something non-additive happened, and the story stops until that is explained.
