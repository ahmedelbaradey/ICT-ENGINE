# R2-10 — Normalized COT Positioning Model — STORY

**Specification. Written before any COT code exists.**
Master story: [Phase-2-Market-Intelligence-STORY.md](../Phase-2-Market-Intelligence-STORY.md)
· Concept map: [R2-10-CONCEPT-MAP.md](R2-10-CONCEPT-MAP.md)
· Tasks: [R2-10-TASKS.md](../../tasks/Phase-2-ICT-Engine/R2-10-TASKS.md)

> **SPECIFICATION ONLY — implementation NOT started, NOT approved.**
> **No COT data has been fetched.**
>
> **Production timeframes: 1H / 4H / 1D only.** No dependency below 1H or above 1D, direct
> or indirect. COT aligns to production bar closes and to nothing else.
>
> **A verification gate (§12) must be completed and reported BEFORE any implementation.**
> If provider reality conflicts with this specification, **STOP and report** — do not invent
> data, do not weaken a rule, do not substitute Legacy.

---

## 1. The question this layer answers

> **Represent publicly reported futures positioning as point-in-time market context, in a
> common analytical representation, without introducing look-ahead and without pretending
> semantically different participant categories are the same thing.**

Two clauses, two distinct risks:

| Clause | Risk |
|---|---|
| *without look-ahead* | The datum most naturally keyed on — the report week — is **not** when the information existed. A Tuesday-dated report published on Friday, joined on its Tuesday date, hands a model three days of future on every observation. Nothing about the dataset looks wrong |
| *without pretending categories are the same* | TFF's *Leveraged Funds* and Disaggregated's *Managed Money* are different populations. Mapping them onto one column because both sound speculative destroys the meaning of both |

---

## 2. Placement — settled

```
ict_kronos/data/cot/
    contract.py       raw records, family-scoped enums, provenance
    provider.py       acquisition, factory, mock-by-default, pure parsers
    normalize.py      the normalisation layer  <- the abstraction
    model.py          point-in-time selection, NormalizedCOTContext
```

Directed by the brief. `ict/` was never a candidate: COT is not a deterministic function of
observed bars, it is an external weekly dataset about a **different instrument** than the
one being traded.

**Existing task and documentation structure is preserved** (brief §18): task files stay in
`tasks/Phase-2-ICT-Engine/`; no approved artefact is moved or renamed.

---

## 3. Report families — Legacy is EXCLUDED

### 3.1 The exclusion, stated once

> **Legacy COT is entirely out of scope for R2-10.**
> Not as a common spine. Not as a fallback. Not to fill a missing value. Not for
> normalisation. Not for cross-instrument comparability.

This supersedes **two** earlier drafts of this document — one that shipped Legacy only, and
one that used Legacy as the cross-instrument spine. Both are void.

### 3.2 The appropriate modern family per market

| Symbol | Mapped futures market | Expected family | Principal speculative category |
|---|---|---|---|
| `EURUSD` | Euro FX futures (CME) | **TFF** — Traders in Financial Futures | Leveraged Funds |
| `XAUUSD` | Gold futures (COMEX) | **Disaggregated** | Managed Money |

Every cell above is **UNVERIFIED until §12 gate rows 1–5 confirm it against real CFTC
data.** Availability is not assumed, and category semantics are **never inferred from
report names**.

**If an appropriate mapping cannot be established for a market: HARD STOP.** Do not
substitute Legacy. Do not approximate with a neighbouring contract.

### 3.3 Where commonality comes from

The earlier draft made Legacy the common spine because both symbols had it. That is exactly
the move this brief forbids, and the replacement is architectural rather than data-driven:

```
commonality  ==  the NORMALIZATION LAYER
             !=  a shared raw report family
```

`NormalizedCOTContext` is what EURUSD and XAUUSD have in common. The raw family is
**provenance**, carried alongside, never flattened away.

### 3.4 Semantic preservation — the rule that makes normalisation honest

> **Never claim `Dealer = Producer` or `Managed Money = Non-Commercial`** unless the source
> definitions explicitly justify it. They do not.

Normalisation maps a family's categories into **normalised roles** by an explicit, declared,
per-family mapping table — not by name similarity and not by position:

| Normalised role | TFF (currencies) | Disaggregated (metals) |
|---|---|---|
| `SPECULATIVE_LEVERAGED` | Leveraged Funds | Managed Money |
| `ASSET_MANAGER` | Asset Manager/Institutional | *(no direct analogue — `None`)* |
| `INTERMEDIARY` | Dealer/Intermediary | Swap Dealers |
| `COMMERCIAL_HEDGER` | *(no direct analogue — `None`)* | Producer/Merchant/Processor/User |
| `OTHER_REPORTABLE` | Other Reportables | Other Reportables |
| `NONREPORTABLE` | Nonreportable | Nonreportable |

Three properties make this defensible rather than a fudge:

1. **A role with no analogue in a family is `None`**, never filled from a different
   population. `ASSET_MANAGER` is `None` for gold; `COMMERCIAL_HEDGER` is `None` for EURUSD.
2. **Every mapping row is declared in a table with a justification string**, so a reviewer
   sees *why* Leveraged Funds and Managed Money share a role — both are the reportable
   speculative money of their respective report — and can reject it.
3. **The source category is preserved in provenance** on every normalised value, so the
   mapping is reversible and auditable.

**The mapping table above is a PROPOSAL requiring approval at gate row 9**, because it is
the one place where semantic judgement enters. It is not doctrine, and it is not derived
from the ICT material — it is an engineering mapping over CFTC definitions.

### 3.5 Futures-only versus futures-and-options

**Futures-only for v1**, carried as `report_variant` so the combined series can later be
added as a **second series, never a replacement**. It needs no assumption about
delta-equivalence when folding options into contract counts.

### 3.6 Instrument mapping is an approximation

Spot FX is not a futures contract. Every mapping row carries `market_code`, `market_name`,
`exchange`, `sign` (`+1`/`−1`, how net-long maps to a directional view on the **spot**
symbol), `is_approximate` — **always `True`** — and a `basis_note` naming the differences
(different instrument, settlement, participants, exchange-listed vs OTC, contract months vs
continuous spot). Both production mappings are expected to be `+1`; that is **asserted in a
test**, not assumed.

---

## 4. Publication timing — the part that decides whether this layer leaks

### 4.1 Four distinct timestamps, never collapsed

```
report_date              the Tuesday the positions were held        (a DATE)
publication_timestamp    the instant the report became public       (an INSTANT, UTC)
confirmation_timestamp   == publication_timestamp. The gate field   (an INSTANT, UTC)
market_as_of             the production bar close                   (an INSTANT, UTC)
```

`report_date` is a date without a time, deliberately: the CFTC states positions as of the
close of business Tuesday, and turning that into an instant would require inventing an
exchange close time. **It is never an alignment key**, so it never needs to be one.

### 4.2 The mandatory rule

> **A COT observation becomes usable only from its actual publication/availability
> timestamp: `publication_timestamp <= market_as_of`. Do NOT join by report week.**

Which is exactly the engine's existing gate, with no new observability code:

```python
usable    = filter_observable(reports, as_of=market_as_of)   # ONE gate
applicable = max(usable, key=lambda r: (r.publication_timestamp, r.report_id)) or None
```

`confirmation_timestamp` is an alias for `publication_timestamp`. Selection is per
`(market, report_family, report_variant)`.

### 4.3 The worked example — every row is a required test

```
Report period Tuesday 2026-07-14
Published     Friday  2026-07-17 15:30 America/New_York  =  19:30 UTC (EDT)

market_as_of                   Applicable report        Why
-----------------------------  -----------------------  ----------------------------
2026-07-14 22:00 UTC (Tue)     week of 2026-07-07       the 07-14 report is 3 days away
2026-07-15 12:00 UTC (Wed)     week of 2026-07-07       still unpublished
2026-07-16 12:00 UTC (Thu)     week of 2026-07-07       still unpublished
2026-07-17 19:00 UTC (Fri)     week of 2026-07-07       30 minutes before publication
2026-07-17 19:29:59.999 UTC    week of 2026-07-07       one instant before
2026-07-17 19:30:00 UTC (Fri)  week of 2026-07-14       exactly at publication -> usable
2026-07-17 20:00 UTC (Fri)     week of 2026-07-14       published
```

Publication exactly at `market_as_of` counts as usable — the engine's existing rule, applied
without exception.

### 4.4 The publication instant derives from a LOCAL time, and DST is real

15:30 America/New_York is **19:30 UTC under EDT and 20:30 UTC under EST**. A hard-coded UTC
hour puts every winter report one hour early — a one-hour leak in half the year, and in none
of the summer test data. Conversion uses `zoneinfo` from a configured local time, at the
point of use, and a guard test asserts the module defines no timezone constant of its own.

### 4.5 Delayed publications are data, not a rule

1. **If the source publishes an availability timestamp per report, use it verbatim.**
2. **Otherwise derive it** from `report_date` + the configured offset, and set
   `publication_is_derived = True`.

A holiday calendar is **not** built here — it is explicitly deferred (HANDOFF open item 7).
`publication_is_derived` is carried into provenance and projected as a data-quality feature,
so a dataset built entirely from derived instants is visibly weaker than one built from
published ones. **Gate row 6 determines which tier applies.** If publication timing cannot
be established at all: **HARD STOP**.

`publication_offset_minutes` is configuration, defaulting to **`0`** — a non-zero default
would silently make every dataset differ from the honest one.

---

## 5. The normalised representation

### 5.1 `NormalizedCOTContext` — the point-in-time answer

Per `(symbol, family)`. R2-12 consumes this; R2-13 projects it.

| Field | Notes |
|---|---|
| `symbol`, `as_of` | The production bar close |
| `available` | `bool` — **never missing**, the witness |
| `unavailable_reason` | `NO_HISTORY` · `NOT_YET_PUBLISHED` · `SYMBOL_UNMAPPED` · `FAMILY_UNAVAILABLE` · `DATASET_MISSING` |
| `report_family` | `TFF` / `DISAGGREGATED` — **provenance, always exposed** |
| `report_variant` | `FUTURES_ONLY` |
| `market` | code, name, exchange, `sign`, `is_approximate`, `basis_note` |
| `report_date`, `publication_timestamp` | §4.1 |
| `report_age_days`, `period_age_days` | Staleness. Keeps ~120 identical 1H rows legible |
| `publication_is_derived` | §4.5 |
| `open_interest` | |
| `roles` | `tuple[NormalizedRolePosition, ...]` — one per normalised role present |
| `historical_rank` | Percentile over **published-by-`T`** reports only |
| `change_vs_previous_report` | Against the previous **published** report |
| `positioning_change_rate` | Change normalised by open interest |
| `extreme_flag` | Tri-state, §5.4 |
| `reports_in_history` | The warm-up witness — **never missing** |
| `previous_report_id`, `history_report_ids` | Provenance for every derived value |
| `normalization_version` | §5.5 |
| `schema_version` | `COT_SCHEMA_VERSION = "r2-10.1"` |

`NormalizedRolePosition`: `role` (normalised), `source_category` (**the raw family
category — provenance**), `long_contracts`, `short_contracts`, `net` (*property*, never
stored), `net_pct_oi`, `long_share`, `concentration | None`.

**`source_category` on every role is what makes §3.4 checkable rather than asserted.**

### 5.2 Comparable analytical concepts

Supported, per role, where semantically valid: long exposure · short exposure · net
exposure · open interest · net / open interest · long-short concentration ·
historical percentile/rank · week-over-week change · positioning change rate ·
optional extreme state.

**`concentration` is `None` where the source family does not report it** — not zero, and not
computed from a different quantity.

### 5.3 Availability is tri-state per role and per family

`FAMILY_UNAVAILABLE` (a market has no such family) and a role that is `None` within an
available family are **different facts** and are never collapsed. Neither is ever filled
from another family or another role.

### 5.4 `extreme_flag`

Optional, **tri-state `TRUE` / `FALSE` / `UNKNOWN`**, with the continuous
`historical_rank` **always available beside it**. The threshold is configuration, never a
hidden magic number. `UNKNOWN` during warm-up — an unknown extreme and a known
non-extreme are different facts. **The flag never replaces the continuous rank.**

### 5.5 Normalisation is versioned

`normalization_version` is carried on every context. Normalisation must be **deterministic,
documented, versioned, reproducible and point-in-time safe** — so a value can always be
tied to the mapping table that produced it, and a mapping change is visible rather than
silent.

---

## 6. History and point-in-time statistics

| Question | Answer |
|---|---|
| Minimum history | `min_history_reports`, configuration, default **52**. Below it, `historical_rank` and `extreme_flag` are **`None`/`UNKNOWN`** |
| Window | `W = 156` published reports (three years), configurable. **Community convention, not CFTC doctrine** |
| Rolling or expanding | **Rolling over the last `W` published reports.** An expanding window's value at a fixed instant changes when the backfill lengthens — a reproducibility failure |
| Warm-up | `None`, with `reports_in_history` published so it is **visible**. Never a default, never `0`, never `50` |
| Per family | Counted per `(market, family, variant)`. Warm-ups complete independently |
| No future data | **Structural**: the window is built from `filter_observable(reports, T)`. Never a full-history percentile |

**Blocking data prerequisite.** The production window starts **2026-02-01**, so with
`W = 156` the COT history must reach back to **2023-02 at the latest**; **2016 → 2026 is
recommended** — the dataset is weekly and tiny, and a decade removes the question
permanently.

---

## 7. Revisions

**A revision is a new immutable record with its own `publication_timestamp` and its own
`report_id`, carrying `revision_of`. Nothing is ever overwritten.**

An observation at `T` sees a revision only if it was published by `T` — correct, because
before the revision existed the original *was* the public fact. Re-running the pipeline
after a revision reproduces the same historical values, so the dataset stays reproducible
(CLAUDE.md rule 8), and the streaming asymmetry the append-only model avoids **cannot
occur**.

---

## 8. Acquisition and provenance

Cached under `ict_kronos/data/cot/`, raw payloads **write-once and immutable**
(CLAUDE.md rule 7). Every normalised value retains:

`report_family` · `CFTC contract/instrument identifier` · `report_date` ·
`publication/availability timestamp` · `normalization_version` ·
`source record identifier/hash where available` · `source_category` per role.

**Mock by default** (CLAUDE.md rule 9), exactly as `market_data` does it:

```
COT_BACKEND=fixture   (default)  deterministic local fixture; offline; CI uses this
COT_BACKEND=cftc      (opt-in)   live download; `requests` imported LAZILY; [cot] extra
```

Pure parsers (`parse_rows`, `derive_publication_timestamp`, `to_records`, `normalize`) are
pure functions over bytes/DataFrames, unit-tested against synthetic payloads with no
network — the `dukascopy.py` design split, which carries all the correctness risk.

---

## 9. Leakage contract

### 9.1 The four ways this layer can leak

| # | Leak | Defence |
|---|---|---|
| 1 | Join by report week | `confirmation_timestamp == publication_timestamp`. **The deliberately incorrect implementation** (§10) |
| 2 | Rank over the whole history | Windows from `filter_observable`; `history_report_ids` makes it checkable |
| 3 | Change computed against the previous *calendar* report | `previous_report_id` selected from the published set and exposed |
| 4 | Fill one family or role from another | Families and roles never share a code path; `FAMILY_UNAVAILABLE` and `None` roles are explicit |

### 9.2 The matrix (master story §6.3 — authoritative L1–L8)

| # | R2-10 instantiation |
|---|---|
| **L1** | **No future bars** — n/a, R2-10 reads no bars. Restated as: no report with `publication_timestamp > as_of` may affect the context. **Asserted by an import guard**, never a blank cell |
| **L2** | **Future OHLC mutation** — n/a, same guard |
| **L3** | **Dependency declared**: *"depends on published external records only"* — no close, no wick |
| **L4** | **Point-in-time lifecycle.** The applicable report at `as_of` is the latest **published by** `as_of`; a revision published later does not retro-apply |
| **L5** | **Prefix equivalence.** A context built from reports truncated at `T` equals one built from the full set and queried at `T` |
| **L6** | **Identity stability.** One report across many bars ⇒ **one** `report_id`, unchanged when later reports arrive; a revision ⇒ a **distinct** id; two families ⇒ distinct id spaces |
| **L7** | **External inputs — the core proof.** Mutate, delete and insert reports published after `T`; move a publication timestamp forward and assert affected rows **stop seeing it** |
| **L8** | **Non-vacuous control.** Mutate an **available historical** report; dependent rows **must change**. Run against the §10 incorrect implementations, which must **fail** |

**Provenance integrity** is contracted in §8 and tested by marker substitution.

### 9.3 Required leakage tests (brief §10, verbatim)

1. mutate future COT releases → historical rows unchanged
2. remove the next COT release → prior rows unchanged
3. move publication timestamp forward → affected rows stop seeing it
4. mutate an available historical COT observation → dependent rows change
5. a non-vacuous control mutation

### 9.4 Boundary tests

Exactly at `publication_timestamp` · one microsecond earlier · before every publication ·
dataset absent · `min_history_reports − 1` and exactly `min_history_reports` ·
`max == min` in the window ⇒ rank `None`, **not `50`** · `OI == 0` ⇒ every `*_pct_oi`
`None`, not `0` · DST January vs July, both correct · a family unavailable for a market ⇒
`FAMILY_UNAVAILABLE`, never a substitution · a role with no analogue ⇒ `None`, never filled.

---

## 10. The deliberately incorrect implementation (§15, proves L8 non-vacuous)

> **Join COT by report week instead of by publication timestamp.**

Built into the leakage suite, run, and asserted to **fail** L1/L4/L7, with the number of
differing observations reported. A second one — a full-history percentile — is built the
same way for §9.1 leak 2.

---

## 11. Production timeframe restriction

COT aligns to **production bar closes only: 1H, 4H, 1D**. `assert_production_pair` is
called before any context is built, so a lower timeframe **raises** rather than producing
rows. COT introduces **no** dependency below 1H or above 1D — it reads no bars at all, and
an import guard asserts it.

**Known and accepted:** on 1H rows roughly 120 consecutive rows carry identical COT values.
That is a real property of a weekly datum, not a defect; `report_age_days` makes it legible.
Hiding it — by restricting COT to daily rows — would be a silent transformation.

---

## 12. The verification gate — BEFORE any implementation

**No implementation code is written until every row is answered against real CFTC data and
reported. Do not infer semantics from report names. Do not assume availability.**

| # | Must verify | HARD STOP if |
|---|---|---|
| 1 | **TFF** availability for the mapped currency contract | absent — do **not** substitute Legacy |
| 2 | **Disaggregated** availability for the mapped gold contract | absent — do **not** substitute Legacy |
| 3 | Exact **EURUSD** contract mapping (code, name, exchange) | ambiguous |
| 4 | Exact **XAUUSD** contract mapping | ambiguous |
| 5 | That neither mapping needs a sign inversion | inversion needed and undocumented |
| 6 | **Publication timing** — published per report, or derived? | cannot be established |
| 7 | **Historical coverage** per family per contract back to ≥ 2023-02 | insufficient for `W` |
| 8 | **Source reliability** — endpoint stability, rate limits, revision practice | unstable |
| 9 | **Category names and definitions per family**, and approval of the §3.4 role mapping | semantics would have to be merged incompatibly |

Output: a verification report recording every finding **and every difference from this
specification**, stated as a conflict rather than accommodated.

---

## 13. Ambiguity register

| # | Ambiguity | Chosen | Why | Kind |
|---|---|---|---|---|
| **B1** | Futures positioning as a proxy for spot | **Approximation, declared on every record** | The only publicly reported positioning that exists | **Engineering assumption** |
| **B2** | Which families | **TFF (currencies) + Disaggregated (metals). Legacy EXCLUDED** | **Directed by the brief.** Commonality comes from normalisation, not from a shared raw family | **Engineering — directed** |
| **B3** | How to make two families comparable | **A declared role-mapping table with per-row justification; `None` where no analogue exists** | Name similarity is not equivalence. `source_category` is preserved so the mapping is reversible | **Engineering — approval required, gate row 9** |
| **B4** | Futures-only vs combined | **Futures-only**, `report_variant` carried | No delta-equivalence assumption | **Engineering** |
| **B5** | Publication timestamps published? | **UNVERIFIED — gate row 6**; two-tier rule with `publication_is_derived` | Repository precedent for unverified upstream claims | **Verification gate** |
| **B6** | Holiday-delayed publications | **Published if available, else derive and flag** | A holiday calendar is explicitly deferred | **Engineering** |
| **B7** | Rank window | **156, rolling, configurable** | Expanding breaks reproducibility; 156 is community convention, labelled as such | **Community convention** |
| **B8** | Warm-up representation | **`None`/`UNKNOWN` + `reports_in_history`** | `0` and `50` are real values | **Engineering** |
| **B9** | Weekly datum on 1H rows | **Yes, with `report_age_days`** | A real property of the data; hiding it is worse | **Engineering — limitation** |
| **B10** | `extreme_flag` | **Tri-state, configurable, beside the continuous rank** | Directed by the brief; the concern (a threshold is a hypothesis) is mitigated, not dropped | **Engineering — directed** |
| **B11** | A COT-derived bias | **No** | Evidence, not verdicts | **Engineering** |

---

## 14. Files

**New:** `ict_kronos/data/cot/{__init__,contract,provider,normalize,model}.py` ·
`tests/fixtures/cot/*` (≥ 156 weeks, **both families**, both markets, one revision, one
delayed publication, one family-unavailable case, one role-absent case) ·
`docs/features/cot.md` · `tests/test_cot_{provider,normalize,model,leakage,real_data}.py`

**Modified:** `ict_kronos/data/__init__.py` · `app/config.py` · `.env.example` ·
`pyproject.toml` (`[cot]` extra) · `docs/features/README.md` · `docs/dev/HANDOFF.md` ·
`tasks/README.md`

**MUST NOT change:** everything under `ict_kronos/ict/` and `ict_kronos/features/` ·
`data/{resampler,normalizer,dukascopy,dukascopy_candles,backfill,coverage,realdata,production_ingest}.py`
· every existing test · **the existing task/documentation structure** (brief §18).

---

## 15. Definition of done, and the hard stop

1. **§12 verification gate complete and reported**, with every difference recorded
2. COT history on disk back to ≥ 2023-02, **per family**
3. Every task in [R2-10-TASKS.md](../../tasks/Phase-2-ICT-Engine/R2-10-TASKS.md) ✅
4. `pytest -q` green; `ruff` and `black` clean; **no silent skip**
5. The deliberately incorrect implementations built and proven to **fail**
6. **Leakage matrix** and **provenance matrix**, no blank cells
7. Real-data results on the production universe; performance per family
8. `docs/features/cot.md` written; HANDOFF updated in the same commit
9. Clean git state; one local commit. **No push.**

```
=> R2-10 complete -> audit -> completion report -> STOP
=> explicit approval required before R2-11
```
