# R2-10 — COT Positioning Model — tasks

Story: [R2-10-COT-STORY.md](../../docs/features/R2-10-COT-STORY.md) ·
Concept map: [R2-10-CONCEPT-MAP.md](../../docs/features/R2-10-CONCEPT-MAP.md) ·
Master: [Phase-2-Market-Intelligence-STORY.md](../../docs/Phase-2-Market-Intelligence-STORY.md)

**Status: SPECIFIED — awaiting approval. No code written.**
New sub-package `ict_kronos/data/cot/`. `COT_SCHEMA_VERSION = "r2-10.1"`.
**Normalized COT. TFF (currencies) + Disaggregated (metals). Legacy EXCLUDED entirely.**

Nothing about COT exists in the repository today — a search for `COT`, `Commitments of
Traders`, `commitment` and `positioning` across every `.py` and `.md` returns zero matches.

## Prerequisites

| # | Prerequisite | Status |
|---|---|---|
| 1 | R2-09 complete and approved | ⛔ **BLOCKING** |
| 2 | Package placement `ict_kronos/data/cot/` | ✅ **settled by the revised brief** |
| 3 | Scope: **Normalized COT — TFF (currencies) + Disaggregated (metals). Legacy EXCLUDED** | ✅ **settled by the brief** |
| 4 | **§12 verification gate complete and reported** | ⛔ **BLOCKING — before any implementation code** |
| 5 | COT history on disk: ≥ `W + min_history` reports before **2026-02-01**, **per family** | ⛔ **BLOCKING for Phase B** |

## Phase A — verification gate and acquisition

**No implementation code is written until every A-task is complete and reported.**
Do not infer semantics from report names.

| ID | Task | Notes | Status |
|---|---|---|---|
| **R2-10-a1** | Verify **TFF** availability for the mapped currency contract | **If absent: HARD STOP. Do NOT substitute Legacy** | ⬜ |
| **R2-10-a2** | Verify **Disaggregated** availability for the mapped gold contract | **If absent: HARD STOP. Do NOT substitute Legacy** | ⬜ |
| **R2-10-a3** | Obtain approval for the **normalised role-mapping table** (story §3.4) | The one place semantic judgement enters. **If roles would have to be merged incompatibly: HARD STOP** | ⬜ |
| **R2-10-a4** | Verify the **exact EURUSD contract mapping** — market code, name, exchange | A wrong code is silently wrong data | ⬜ |
| **R2-10-a5** | Verify the **exact XAUUSD contract mapping** | Same | ⬜ |
| **R2-10-a6** | Verify **publication timing**: is a publication timestamp published per report, or must it be derived? | Decides §4.5's tier and whether `publication_is_derived` is ever `True` | ⬜ |
| **R2-10-a7** | Verify **historical coverage** per family per contract | Decides whether `W = 156` is reachable from 2026-02 | ⬜ |
| **R2-10-a8** | Verify **source reliability** — endpoint stability, rate limits, revision practice | Decides retry/backoff and the revision model | ⬜ |
| **R2-10-a9** | Verify **category names and semantics per family** | *"Do not infer semantics from report names"* | ⬜ |
| **R2-10-a10** | **Verification report**, recording every finding **and every difference from the specification** | A conflict is stated, never accommodated | ⬜ |
| R2-10-b | Backfill COT history, cached, hashed, manifested | Raw payloads write-once and immutable (CLAUDE.md rule 7) | ⬜ |
| R2-10-c | Cache provenance per artefact: `source`, `retrieval_timestamp`, `report_date`, `publication_timestamp`, `report_type`, `market/contract`, `checksum` | Brief's acquisition contract | ⬜ |
| R2-10-d | Deterministic fixture: ≥ 156 weeks, **all families**, both markets, **one revision**, **one delayed publication**, **one family-unavailable case** | Committed under `tests/fixtures/cot/`; CI uses this and never touches the network | ⬜ |

## Phase B — model

| ID | Task | Notes | Status |
|---|---|---|---|
| R2-10-e | `CotPositioning` unified abstraction, family-specific fields preserved | Story §5.1. `net_position` is a **property**, never stored | ⬜ |
| R2-10-f | **`confirmation_timestamp == publication_timestamp`** | The entire leakage defence, in one line. Satisfies `Confirmable`; **the module writes no observability code of its own** | ⬜ |
| R2-10-g | **`publication_timestamp <= market_as_of` is mandatory**; the report period/date alone is never sufficient | Brief's COT-specific rule | ⬜ |
| R2-10-h | Publication instant derived from a **local** time via `zoneinfo`; `publication_is_derived` flag | 15:30 NY is 19:30 UTC in EDT, 20:30 in EST. A hard-coded UTC hour leaks one hour for half the year | ⬜ |
| R2-10-i | Per-family selection: independent per `(market, report_type, report_variant)` | One family being stale never affects the other | ⬜ |
| R2-10-j | **Never merge incompatible semantics.** A role absent in a family is `None`; `source_category` preserved on every role | Story §3.4 | ⬜ |
| R2-10-k | Availability table per `(symbol, report_type)`; **raises** for undeclared; `REPORT_TYPE_UNAVAILABLE` for a family a market lacks | Explicitly unavailable, **never** filled from another family | ⬜ |
| R2-10-l | Instrument mapping with `sign`, `is_approximate=True`, `basis_note`, `families` | A test asserts **neither** production mapping needs inversion | ⬜ |
| R2-10-m | Raw + dimensionless features per family | `None` when `OI == 0`, never `0` | ⬜ |
| R2-10-n | `change_vs_previous_report` against the previous **published** report; `previous_report_id` exposed | Not the previous *calendar* report. A delayed-publication test pins the difference | ⬜ |
| R2-10-o | `historical_rank` — rolling over **published-by-`T`** reports; `history_report_ids` exposed | Makes the no-future claim mechanically checkable | ⬜ |
| R2-10-p | `extreme_flag` — **configurable threshold, tri-state (`None`/`False`/`True`), continuous rank always beside it** | Required by the brief. Concern recorded and mitigated, not dropped — story §5.3 | ⬜ |
| R2-10-q | Warm-up: `None` + `reports_in_history`; `max == min` ⇒ `None`, **not `50`** | The R2-06 degenerate-range precedent | ⬜ |
| R2-10-r | Staleness: `report_age_days`, `period_age_days` | Keeps ~120 identical 1H rows honest | ⬜ |
| R2-10-s | Revisions **append-only**; nothing overwritten | Story §7. What makes "no streaming asymmetry" true | ⬜ |
| R2-10-t | Provider + factory, mock-by-default, lazy live import, loud degradation | CLAUDE.md rule 9, the `market_data` shape | ⬜ |
| R2-10-u | Pure parsers unit-tested against synthetic payloads, no network | The `dukascopy.py` design split | ⬜ |
| R2-10-v | Serialisation + exact round trip | | ⬜ |
| R2-10-w | Config wiring: `CotConfig.from_env`, `Settings`, `.env.example`, `[cot]` extra if needed | | ⬜ |

## Phase C — tests and close-out

| ID | Task | Notes | Status |
|---|---|---|---|
| R2-10-x | **Unit** tests: normal, edge, malformed, empty, per family | | ⬜ |
| R2-10-y | **Boundary** tests | Exactly at publication · one µs earlier · before every publication · dataset absent · warm-up ±1 · `max == min` · `OI == 0` · DST Jan vs Jul · family unavailable | ⬜ |
| R2-10-z | **The seven-row worked example** of story §4.3, with the boundary asserted at `19:29:59.999` **and** `19:30:00` | The clearest statement of the timing rule | ⬜ |
| R2-10-1 | **Leakage L1 … L8** (master §6.3). **L7 is the core proof** — mutate, delete and insert reports published after `T`, **in both families**; the snapshot must be byte-identical | `L2`/`L3` are `n/a` **with a reason and an import guard**, never a blank cell | ⬜ |
| R2-10-2 | **L8**: run L1/L4/L7 against two deliberately broken implementations — report-date alignment, and whole-history percentile — and assert each **fails** | | ⬜ |
| R2-10-3 | **Provenance** tests: every id resolves **in its own family's registry**; marker substitution, not value matching | | ⬜ |
| R2-10-4 | **Measure** the within-family cross-role relationship on real data | If it contradicts the "divergence" rejection, revisit **with evidence** | ⬜ |
| R2-10-5 | **Identity**: one report across 500 hourly states ⇒ **one** `report_id`; a revision ⇒ a **distinct** id | | ⬜ |
| R2-10-6 | **Real-data** tests over the production window (2026-02-01 → 2026-08-01), both symbols | | ⬜ |
| R2-10-7 | **Serialisation** tests: `from_dict(as_dict()) == value` exactly, including `None`, per family | | ⬜ |
| R2-10-8 | **Guard** tests | No market-data import · no ICT import · no hand-rolled observability · no timezone constant · no cross-family fill · no model-training import | ⬜ |
| R2-10-9 | **Performance**: lookup per row, batch per month, **per family** | | ⬜ |
| R2-10-10 | **Leakage matrix** and **provenance matrix** in the completion report, no blank cells | Master §8.1a | ⬜ |
| R2-10-11 | Documentation: `docs/features/cot.md`; HANDOFF in the same commit | | ⬜ |
| R2-10-12 | Regression: R2-01 … R2-09 provably untouched | | ⬜ |
| R2-10-13 | Full suite + ruff + black; clean git state; one local commit; **STOP** | No push | ⬜ |

## Decisions that change what is being measured

1. **Alignment is by `publication_timestamp`, never by report date.** `publication_timestamp
   <= market_as_of` is mandatory.
2. **Both families ship, independently, and are NEVER collapsed** into one positioning value.
3. **TFF is in scope** as the currency-side disaggregated-style report — flagged for
   confirmation at gate row `a3`.
4. **A family a market lacks is explicitly unavailable**, never substituted.
5. **The spot↔futures mapping is an approximation**, declared on every record.
6. **Rolling window, not expanding.** An expanding window's value at a fixed instant changes
   when the backfill lengthens — a reproducibility failure, not a leak.
7. **Warm-up is `None`, never a default.** `0` and `50` are both real index values.
8. **Revisions append; nothing is overwritten.** A re-run after a revision reproduces the
   original dataset.
9. **`extreme_flag` exists** (brief-directed), configurable and tri-state, with the
   continuous rank always beside it.
10. **`publication_offset_minutes` defaults to `0`.**

## Not implemented, and why

| Item | Reason |
|---|---|
| A blended cross-family positioning value | Decision 2 |
| Futures-and-options-combined | v1 is futures-only; `report_variant` carried so it can be added as a *second series* |
| Holiday calendar | Explicitly deferred (HANDOFF item 7); the two-tier publication rule works without one |
| Positioning acceleration | Needs three published reports, extremely noisy, no source supports it |
| Trader counts as a signal | Carried for provenance; not projected |
| Price-positioning correlation | Would break the layer boundary; a Phase 4 statistic |
| Any COT-derived bias | Evidence, not verdicts |
| Symbols beyond EURUSD / XAUUSD | Outside the production universe |

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
R2-10 complete -> audit -> completion report -> COMMIT (local)
               -> STOP -> explicit approval required before R2-11
```

The completion report must state **every difference between the §12 verification gate's
findings and this specification**, plus: files, tests, static analysis, performance,
**leakage matrix**, **provenance matrix**, real-data results, ambiguities, assumptions,
rejected alternatives, limitations, and git status/commit information.

**If provider reality conflicts with this specification, STOP and report the conflict.
Do not invent data and do not weaken the rule.**
