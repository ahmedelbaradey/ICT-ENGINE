# R2-12 — Market State v2 — CONCEPT MAP

**Specification checkpoint. Written before any change to `market_state.py`.**
Story: [R2-12-MARKET-STATE-V2-STORY.md](R2-12-MARKET-STATE-V2-STORY.md)

---

## 1. The shape of the problem

R2-12 computes almost nothing. Every input is already gated, already provenanced, already
prefix-equivalent. The composition is three method calls.

**All of the risk is in what the composition is tempted to *add*:**

| Temptation | Failure |
|---|---|
| Extend `bias` with the new layers | Silently changes the meaning and range of a **shipped, dataset-recorded** feature. A market that was BULLISH becomes NEUTRAL with no market change |
| Recompute a value inside the state instead of querying the layer | Sees the full analysis rather than the observable slice. The one way this layer can leak |
| Merge pools into `LiquidityContext` | Two id registries in one record; `source_ids()` grouping breaks |
| Filter on data quality | Hides degraded observations from the model that should learn they are degraded |

R2-12 is a **restraint problem wearing a composition problem's clothes.**

---

## 2. Dependency graph

```
R2-09 LiquidityModel.picture_at(T) ──┐
R2-10 CotModel.snapshot_at(sym, T) ──┼──► ICTMarketState v2  (four NEW optional fields)
R2-11 MtfBuilder.picture_at(...)   ──┤
R2-08 coverage_report -> BarCoverage ┘
                                            ▲
                     R2-07 nine existing contexts, UNCHANGED
```

Every arrow is *"reads the point-in-time API of"*. None is *"re-derives"*.

---

## 3. Replace, parallel, or extend — three candidates

### X1 — Extend `ICTMarketState` with optional fields *(SELECTED)*

| | |
|---|---|
| ✅ | **The repository's own pattern.** `IctEvent` already carries optional fields *"where the concept supports them… and stay `None` elsewhere rather than being faked"* |
| ✅ | **The compatibility claim becomes one test:** for the same inputs, every v1 field of a v2 state equals the v1 state's field |
| ✅ | Every existing consumer keeps working with no change |
| ✅ | `_section()`, `as_dict()` and `source_ids()` each stay single-implementation |
| ✅ | With all switches off, a v2 state is byte-identical to v1 in every existing field — so R2-12 can land before R2-13 without changing a single number |
| ⚠️ | `as_dict()` gains four keys. Accepted: every existing key keeps its exact position and value, and the payload is versioned (`state_version`) |

**Verdict: selected.**

### X2 — A new `ICTMarketStateV2` composing a v1 instance

| | |
|---|---|
| ✅ | v1 is provably untouched — it is literally a member |
| ❌ | **Every consumer branches.** `DatasetBuilder`, `ICTFeatureVector.from_state`, every test helper |
| ❌ | Two `as_dict()`s, two `source_ids()`s, two round-trip suites — twice the surface that can drift |
| ❌ | Field access becomes `state.v1.structure.direction`, or a forwarding layer is written, which is X1 with extra steps |

**Verdict: rejected.**

### X3 — Replace `ICTMarketState`

| | |
|---|---|
| ❌ | Invalidates `tests/test_market_state.py` (788 lines) and `tests/test_market_state_real_data.py` (604 lines) as a matter of course — destroying the regression evidence at the moment it is most needed |
| ❌ | Nothing is gained. The nine existing contexts are correct and are not the thing changing |

**Verdict: rejected outright.**

---

## 4. Bias — four candidates, and why the boring one wins

`BiasContext` counts four sources; `bullish_score` / `bearish_score` are documented with
range **0–4** in [features.md](../ict/features.md) §10.

| # | Candidate | Verdict |
|---|---|---|
| **B1** | **Freeze `bias`; add a verdict-free `EvidenceContext`** | ✅ **selected** |
| B2 | Extend the count to seven sources | ❌ Changes a shipped feature's **range** and its **verdict** for an unchanged market. Two datasets would carry `bullish_evidence_count` with two meanings. The R2-06 lesson verbatim: *"two live definitions would mean every downstream result has to name which one it used, and the first person to forget makes the results unreproducible"* |
| B3 | Add a second, separately-named verdict (`bias_v2`) | ❌ Two live bias definitions, differing only in name. Whichever produces the nicer number would win by drift, not by evidence |
| B4 | Weight the sources | ❌ *"A weight is a hypothesis and this story does not test hypotheses"* (market_state.md §9). And the new sources are not commensurable with the old — "the 4H structure is bullish" and "price is in discount" are not one vote each in any sense the source material supports |

### 4.1 What B1 actually gives up, stated honestly

An aggregate that *uses* the new layers. That is a real loss, and it is the right one: the
aggregate any consumer wants is computable from `EvidenceContext`, which exposes every
labelled fact from every layer. What is not computable from a collapsed score is the
evidence that produced it.

`EvidenceContext` deliberately has **no verdict field of any kind** — not even one
documented as advisory. A verdict field becomes a second bias by use, whatever its
docstring says.

---

## 5. Pools: extend `LiquidityContext` or add a context? — two candidates

| # | Candidate | Verdict |
|---|---|---|
| C1 | **A separate `LiquidityPoolContext`** | ✅ **selected.** R2-04 levels and R2-09 pools have different lifetimes, different identities and different provenance registries. `source_ids()` groups by originating detector precisely so a record's ids all resolve against one registry |
| C2 | Add pool fields to `LiquidityContext` | ❌ One record whose ids resolve against two registries. And it modifies a context that is currently unchanged, weakening the "nine contexts untouched" compatibility claim to "eight and a half" |

---

## 6. Data quality: filter or describe? — two candidates

| # | Candidate | Verdict |
|---|---|---|
| Q1 | **Describe. Every bar produces a complete state; degradation is a field** | ✅ **selected.** R2-08's rule carried up one layer: *"a coverage ratio is a quality signal, never a validity rule"* |
| Q2 | Withhold or null a state for degraded bars | ❌ Hides degraded observations from the model that should learn they behave differently, and it reintroduces a threshold the engine has explicitly refused. A filtered dataset cannot even express the question |

---

## 7. Defaults: on or off? — two candidates

| # | Candidate | Verdict |
|---|---|---|
| N1 | **All three config switches `False`; all four new fields `None`** | ✅ **selected.** Makes the v1-compatibility test trivially true, lets R2-12 land without changing a single existing number, and forces every consumer to opt in explicitly |
| N2 | Default on | ❌ Every existing test would exercise the new paths on its first run, so a failure could not be attributed to the extension or to the composition. And a caller who did not ask for COT would silently get it |

---

## 8. Leakage criteria inherited

L1 … L8 from the master story §6.3. **The deliberately broken implementation (L8):**

> Reach into a layer's full analysis (`liquidity_model.analysis.pools`,
> `cot_model.all_reports`) instead of calling its point-in-time API.

Built and proven to disagree. It is the only leak available to a layer whose every input is
already gated, and it is the one a well-meaning optimisation introduces ("we already have
the analysis, why call a method").

### 8.1 The guard that must keep passing

`market_state.py` contains **no** `confirmation_timestamp <=` comparison — a source-level
guard with a four-way mutation test *of the guard itself* (R2-07 audit). R2-12 adds code to
this file and must not add the first such comparison.

The docstring-and-comment stripper stays load-bearing: the new code will *mention*
observability in order to warn against it, exactly as `dealing_range.py` names
`frame["high"].max()` in order to warn against it.

### 8.2 Provenance completeness: marker substitution is mandatory

The R2-07 audit found `source_ids()` omitting `premium_discount.source_break_id` **for a
whole story**, and no test noticed because that id usually *equals* `latest_break_id`.

> *"A provenance enumeration is only as good as its coverage test, and a value-based test is
> not one… The test that catches it stamps each field with a unique marker and asks whether
> the FIELD is read — not whether the value appears somewhere."*

Mandatory for all four new groups. The stress case is deliberately reproduced: a dealing
range id now reaches `source_ids()` by **two** paths — `premium_discount.range_id` and
`liquidity_pools.source_range_id` — and `_ids()` deduplicates, so a value-based test would
pass with either field unread.

---

## 9. Streaming

R2-12 introduces **no** asymmetry. It inherits exactly two, both of the same shape and both
already pinned:

| Source | Asymmetry |
|---|---|
| R2-05.1 True Daily Open (local) | Prefix sees staler (market_state.md §10a) |
| R2-11 HTF True Daily Open | Prefix sees staler (R2-11 §9.1) |

```
prefix sees LESS (staler)  <- safe, and what happens here
prefix sees MORE           <- a leak, and it never happens
```

The direction assertion is the one that matters and must fail loudly if it ever inverts.

**`NaN` never enters a state.** R2-06's degenerate-range `NaN` is already translated to
`None` at that boundary; the same translation is required for every new numeric field. A
`NaN` inside a record breaks `from_dict(as_dict()) == v` **and** reports a phantom streaming
difference — R2-07 audit defect 1, found by the audit and not by the fixture.

---

## 10. Ambiguity register

Full register in [the story](R2-12-MARKET-STATE-V2-STORY.md) §13 (D1 … D8). The single
decision most likely to be re-opened:

| Decision | Why it stands |
|---|---|
| **`bias` is frozen at four sources** | It is the *conservative* choice that looks like the timid one. Extending it costs nothing visible and silently makes two datasets incomparable under one column name. `EvidenceContext` gives every consumer strictly more than an extended count would, without that cost |

---

## 11. What R2-12 does not build

A second bias · a weighted score · any new interpretation · any re-derivation · a data-quality
filter · a change to any of the nine existing contexts · `ICTFeatureVector` columns (R2-13) ·
any ML, probability, label or normalisation · any backtest rule.

**The strongest safety property, restated:** if `tests/test_market_state.py` and
`tests/test_market_state_real_data.py` pass **with zero edits**, the extension is genuinely
additive. Any edit to either file is a signal that something non-additive happened, and the
story stops until that is explained.
