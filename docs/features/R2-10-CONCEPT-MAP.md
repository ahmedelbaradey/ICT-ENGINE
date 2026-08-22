# R2-10 — COT Positioning Model — CONCEPT MAP

**Specification checkpoint. Written before any COT code exists.**
Story: [R2-10-COT-STORY.md](R2-10-COT-STORY.md)

---

## 1. The shape of the problem

COT arithmetic is trivial:

```
net = long - short
index = 100 * (net - min) / (max - min)
```

**All of the risk is in two places**, and neither is the arithmetic:

| Risk | Failure |
|---|---|
| **Which report was known at `T`** | The natural key — the report date — is *not* when the information existed. Joining on it hands a model three days of future on every observation, permanently, with nothing about the dataset looking wrong |
| **Which history a statistic reads** | `min`, `max` and percentile are whole-window statistics. Computed once over the full download, they encode the series' future into every early row — and the values still look like perfectly ordinary percentiles |

This is a **timing problem wearing a data-ingestion problem's clothes**, exactly as R2-06
was a selection problem wearing an arithmetic problem's clothes.

---

## 2. Dependency graph

```
CFTC dataset ──► CotProvider ──► CotReport (immutable, append-only)
                                      │
                                      ▼
                          filter_observable(reports, T)   ← the ENGINE's one gate
                                      │
                                      ▼
                                 CotModel ──► CotSnapshot
```

**R2-10 depends on no repository module except `ict/contract.py`.** It reads no bars, knows
no ICT concept, and touches no detector. That isolation is the reason it can be built in
parallel with R2-09 (master story §3.2) and the reason its import guard is one line.

---

## 3. Report families — four candidates

| # | Candidate | Verdict |
|---|---|---|
| R1 | Legacy only | ❌ **VOID.** An early draft's selection. Legacy is now **entirely out of scope** |
| R2 | Legacy as a cross-instrument spine + modern families on top | ❌ **VOID.** A later draft's selection. Explicitly forbidden: Legacy may not be a spine, a fallback, or a filler |
| **R3** | **Modern family per market — TFF (currencies), Disaggregated (metals) — unified by a NORMALISATION LAYER** | ✅ **SELECTED** |
| R4 | Modern families exposed raw, side by side, no normalisation | ❌ Pushes the whole comparability problem to the consumer, and the first consumer to guess `Leveraged Funds ≈ Managed Money` does it undocumented |

### 3.1 Where commonality comes from — the decisive point

Both void candidates tried to find commonality **in the data**. R3 puts it **in the
architecture**:

```
commonality  ==  the NORMALIZATION LAYER      <- an explicit, versioned, justified mapping
             !=  a shared raw report family   <- what R1 and R2 reached for
```

`NormalizedCOTContext` is what EURUSD and XAUUSD have in common. The raw family is
**provenance**, carried alongside on every value, never flattened away.

R4 is worth naming because it is the honest-looking alternative: expose both families raw
and let the model sort it out. It fails because the mapping still happens — just
undocumented, in whoever writes the first cross-instrument query.

### 3.2 The semantic guard that makes R3 defensible

> **Never claim `Dealer = Producer` or `Managed Money = Non-Commercial`** unless the source
> definitions justify it.

Three properties, all checkable:

1. **A role with no analogue in a family is `None`**, never filled from a different
   population. `COMMERCIAL_HEDGER` is `None` for a currency market; `ASSET_MANAGER` is
   `None` for a metals market.
2. **Every mapping row carries a justification string**, so a reviewer sees *why*
   Leveraged Funds and Managed Money share a role and can reject it.
3. **`source_category` is preserved on every normalised role**, so the mapping is
   reversible and auditable.

**The mapping table is a PROPOSAL requiring approval at gate row 9.** It is engineering over
CFTC definitions, not ICT doctrine, and it is the one place semantic judgement enters.

## 4. Alignment — four candidates

| # | Candidate | Verdict |
|---|---|---|
| A1 | **`release_timestamp` as `confirmation_timestamp`; select `max(release) <= T`** | ✅ **selected.** Reuses the engine's one gate with zero new code. The rule is one line and one line is auditable |
| A2 | Report date + a fixed lag in days | ❌ A lag in *days* has no time of day, so every observation on the release day is either early or late. And a delayed release silently breaks it |
| A3 | Report date directly | ❌ **The leak.** This is broken implementation **B1** |
| A4 | Report date + a *conservative* lag (say 7 days) | ❌ Discards real information to avoid thinking about the actual release instant. "Safely wrong" is still wrong, and it makes the dataset quietly different from the honest one with nothing recording why |

A4 is the tempting compromise and it is worth naming: over-conservatism is not free, it is
an undocumented transformation of the data.

**The safety margin R2-10 does allow** (`release_offset_minutes`, §4.6 of the story)
defaults to `0` precisely so that the honest answer is the default and any deviation is
visible in the config rather than baked into the code.

---

## 5. Historical statistics — four candidates

| # | Candidate | Verdict |
|---|---|---|
| H1 | **Rolling window over the last `W` observable reports** | ✅ **selected.** Fixed, stated meaning; reproducible; window built from `filter_observable` so the leak is structurally impossible |
| H2 | Expanding window over all observable reports | ❌ Causal, but its value at a fixed instant **changes when the backfill lengthens**. Two runs of the same pipeline over different date ranges produce different features for the same day — reproducibility failure (CLAUDE.md rule 8) |
| H3 | Rolling window over all reports, filtered afterwards | ❌ **The leak.** This is broken implementation **B2** |
| H4 | No historical statistics at all | ❌ Discards the only cross-instrument-comparable positioning measure. The raw net is not comparable between a gold contract and a currency contract; a rank within its own history is |

**H2 deserves care** because it is *not* a leak and is easy to defend. It is rejected on
reproducibility, not on causality, and the distinction is recorded so nobody re-opens it
believing it was rejected for leaking.

---

## 6. Revisions — three candidates

| # | Candidate | Verdict |
|---|---|---|
| V1 | **Append a new immutable record with its own release timestamp; never overwrite** | ✅ **selected.** Historical rows still resolve to the original, so a re-run after a revision reproduces the original dataset. Mirrors CLAUDE.md rule 7 and R2-04's frozen levels |
| V2 | Overwrite the original in place | ❌ Destroys reproducibility **and** creates the streaming asymmetry the brief anticipates: a query at `T` returns different answers before and after a revision arrives |
| V3 | Keep both but always prefer the revision | ❌ Retroactively rewrites what was publicly known on a past date. Before the revision existed, the original **was** the public fact |

V1 is the design that lets the story state, truthfully, that R2-10 introduces **no**
streaming asymmetry.

---

## 7. Instrument mapping

| Question | Answer |
|---|---|
| Is spot EURUSD the same instrument as CME Euro FX futures? | **No.** Different instrument, different settlement, different participants, exchange-listed versus OTC, contract months versus continuous spot |
| Is the mapping still worth making? | **Yes.** It is the only publicly reported positioning that exists for these instruments |
| How is that honesty preserved? | `is_approximate = True` on **every** mapping row, plus a `basis_note` naming the differences, carried on every `CotSnapshot` |
| Does either mapping need a sign inversion? | **No** — both futures are quoted in the same direction as the spot symbol. That is asserted in a test rather than assumed, and the `sign` field exists so a future USD-base pair can say otherwise |

A boolean that is always `True` looks redundant until the first exact mapping arrives, at
which point it is the field that stops the two being conflated.

---

## 8. Feature candidates — what was cut, and why

Roughly thirty candidates were considered; sixteen ship. The interesting rejections:

| Candidate | Why cut |
|---|---|
| **Positioning "extreme" flag** (`index > 80`) | A **threshold on a feature is a hypothesis**. `cot_index_*` already carries the full information and any model can find any threshold. Encoding one here bakes in an untested rule — the same objection R2-06 raised to a "quality" filter |
| **Positioning acceleration** (second difference) | Needs three observable reports, is extremely noisy on a weekly series, and no source supports it |
| **Cross-role "divergence" as a shipped feature** | Within a family the reported categories sum toward open interest by construction, so a "divergence" between two of them approaches an identity. **A test measures the relationship on real data** rather than assuming it; if the measurement contradicts the rejection, it is revisited **with evidence** |
| **Spreading positions** | Directionally neutral by construction, so they add no directional information. Cheap to add later if a *participation* measure is wanted |
| **Trader counts** | Available, but a count of traders is not a position, and no source connects it to price behaviour |
| **Price/positioning correlation** | Would require reading market bars inside R2-10, breaking the layer boundary; and it is a modelling statistic belonging to Phase 4 |
| **Any COT-derived bias or verdict** | Master story §9. R2-10 produces evidence |

---

## 9. Leakage criteria inherited

L1 … L8 from the master story §6.3. **Two** deliberately broken implementations, because this story has two independent
leaks. Both are built and **L8** asserts the leakage tests catch each:

| | The plausible leaky implementation |
|---|---|
| **B1** | Align by `report_date` — the Tuesday — so Wednesday and Thursday observations see a report released the following Friday |
| **B2** | Compute `min`/`max`/percentile over the **whole downloaded history**, then look up per row |

Both must be built and both must be proven to disagree with the causal implementation, with
the number of differing observations reported.

**L7 is R2-10's core proof** and has no analogue elsewhere in the phase: mutate every report
released after `T` — change every figure, delete some, insert spurious ones — and the
snapshot at `T` must be byte-identical. Paired (L8) with mutating a report released *before*
`T`, which must change it.

`history_report_ids` on every snapshot makes B2's absence **mechanically checkable**: a
test asserts every id in it has `release_timestamp <= as_of`. That is a direct proof, not a
behavioural inference.

---

## 10. Ambiguity register

Full register in [the story](R2-10-COT-STORY.md) §12 (B1 … B11). What is **flagged as
unverified** rather than decided:

| Item | Status |
|---|---|
| Report type names, category names, market codes | **UNVERIFIED** until `R2-10-a` confirms against the downloaded dataset |
| Whether release timestamps are published per report | **UNVERIFIED** — decides which tier of §4.5 applies |
| The exact release schedule and holiday-delay behaviour | **UNVERIFIED** — the two-tier rule works either way, with `release_is_derived` recording which was used |

This follows the repository's own precedent for unverified upstream claims (HANDOFF decision
4 on Kronos: *"claims are UNVERIFIED… Phase 5 starts with a verification task"*). The
specification states what is expected; the implementation proves it or records the
difference.

What is **labelled community convention** rather than doctrine:

| Item | Status |
|---|---|
| The COT index formula and its 156-week lookback | **Community convention.** The CFTC publishes positions, not an index. Labelled as such in the story and the module docstring, and the window is configuration |

---

## 11. What R2-10 does not build

Disaggregated or TFF categories · futures-and-options-combined series · a holiday calendar ·
a positioning "extreme" flag · acceleration · trader counts · price/positioning statistics ·
any COT-derived bias, signal or verdict · any other instrument beyond EURUSD and XAUUSD ·
`ICTMarketState` wiring (R2-12) · `ICTFeatureVector` columns (R2-13) · any ML, probability,
label or normalisation · any backtest rule.
