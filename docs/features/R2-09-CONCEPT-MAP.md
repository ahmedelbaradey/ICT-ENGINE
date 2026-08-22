# R2-09 — Liquidity Model — CONCEPT MAP

**Specification checkpoint. Written before `ict_kronos/ict/liquidity_model.py` exists.**
Story: [R2-09-LIQUIDITY-STORY.md](R2-09-LIQUIDITY-STORY.md)

This document records **why one design was chosen and which were not**. The story says what
to build; this says what was rejected and what that cost.

---

## 1. The shape of the problem

Pool arithmetic is trivial:

```
cluster levels whose prices are within a tolerance
pool price = the extreme of the cluster
```

**All of the risk is in three places**, and none of them is the arithmetic:

| Risk | Failure |
|---|---|
| *When* you cluster | Cluster before the observability gate and a pool's price and cardinality are set by levels that had not confirmed. Invisible in the output — a leaked pool looks like an ordinary pool |
| *What* the tolerance is | ICT gives no number. Inventing one is inventing doctrine; picking one per instrument without evidence is worse |
| *Whether a pool is an object or a view* | An object needs a lifecycle, and membership legitimately changes — which is mutation of a confirmed record |

R2-06 faced the identical shape (*"a selection problem wearing an arithmetic problem's
clothes"*). R2-09 is a **timing and identity problem wearing a clustering problem's
clothes**.

---

## 2. Dependency graph

```
SwingDetector (R2-02) ─┐
SessionDetector (R2-01)├─► LiquidityDetector (R2-04) ─► levels + sweeps ─┐
                       ┘                                                 │
                                                                         ├─► LiquidityModel
StructureDetector (R2-03) ─► DealingRangeDetector (R2-06) ─► ranges ─────┘        │
                                                                                  ▼
                                                                         LiquidityPicture
```

**Read the arrows as "consumes the confirmed output of", never "re-derives".** R2-09
detects nothing. It never imports `swings`, `sessions`, `structure` or `resampler` — a guard
test asserts it.

R2-05.x price arrays (FVG, Order Block, Breaker, Unicorn) are **not** inputs. They are
zones, not liquidity; treating an FVG as a liquidity pool would conflate imbalance with
resting orders, which the engine keeps separate throughout.

---

## 3. Pool representation — five candidates

Judged on: causal timestamp, identity stability, immutability compliance, and whether it can
answer "what is the pool *now*".

### P1 — Derived point-in-time view *(SELECTED)*

Cluster `analysis.active_at(as_of)` on demand. The pool exists only as an answer to a query.

| | |
|---|---|
| ✅ | **Causal by construction.** Every member came through the one gate before clustering began. The leak of §1 is structurally impossible |
| ✅ | **No lifecycle to get wrong.** Members have lifecycles; the pool is a snapshot of them |
| ✅ | **Immutability holds trivially** — nothing is stored, so nothing is mutated |
| ✅ | Identity is a pure function of membership, so the same membership always yields the same id |
| ⚠️ | Recomputed per instant. Cost is O(active levels) over an already-sorted list — accepted, measured |
| ⚠️ | "Pool history" is not directly queryable. Accepted: the *members'* history is, and that is the fact anyone actually needs |

**Verdict: selected.** It is the only candidate where the leakage failure cannot be written.

### P2 — Confirmed pool events with a lifecycle

Emit a `LiquidityPool` event when a cluster first forms; track `ACTIVE → PARTIALLY_SWEPT →
SWEPT`.

| | |
|---|---|
| ✅ | Symmetric with every other R2-05.x composite; a pool becomes a first-class event with provenance |
| ✅ | Pool history is directly queryable |
| ❌ | **Membership changes after confirmation.** A new equal-highs level confirming inside an existing pool either mutates a confirmed record (forbidden) or spawns a near-duplicate pool. R2-04 already hit this shape with equal-level runs and chose overlapping immutable pairs — acceptable for a *pair*, unacceptable for a cluster that can grow indefinitely |
| ❌ | Combinatorial identity growth: an N-member cluster built incrementally emits up to N pools describing the same place |
| ❌ | `PARTIALLY_SWEPT` is a genuinely new lifecycle state, and R2-04 says explicitly there is no state between resting and taken |

**Verdict: rejected.** Its one real advantage (queryable history) does not pay for a new
lifecycle plus an identity explosion.

### P3 — Pools as merged super-levels replacing their members

Replace three levels with one pool level.

| | |
|---|---|
| ✅ | Smallest downstream surface; one object per price region |
| ❌ | **Destroys the distinction R2-04 exists to preserve.** "Was the PDH swept?" and "was the session high swept?" become one unanswerable question. liquidity.md §7 rejects exactly this for sweeps: *"those are different signals"* |
| ❌ | Provenance collapses — a merged level's `liquidity_type` is undefined |
| ❌ | Would require modifying `LiquidityDetector`, which is approved and out of bounds |

**Verdict: rejected**, and it is the candidate a reader is most likely to assume was chosen.

### P4 — Density function instead of discrete pools

A continuous "liquidity density at price p" (kernel over level prices).

| | |
|---|---|
| ✅ | No tolerance, no cardinality, no clustering decision at all — the §1 ambiguity vanishes |
| ✅ | Naturally differentiable; pleasant for a neural model |
| ❌ | **Trades one arbitrary parameter for another** — the kernel bandwidth is exactly as unspecified by ICT as the tolerance is, and it is less interpretable |
| ❌ | No discrete "the pool has 3 members" fact, so pool cardinality — a thing ICT does talk about — becomes unrepresentable |
| ❌ | Not what "a pool of liquidity" means in the source material |

**Verdict: rejected.** Recorded as a Phase-4 *feature-engineering* idea over R2-09's output,
not as a representation-layer replacement.

### P5 — No pools; expose only per-level distances

Skip clustering entirely.

| | |
|---|---|
| ✅ | Zero new ambiguity; nothing invented |
| ✅ | Already almost true — R2-07 exposes `nearest_buy_side_points` |
| ❌ | **Leaves liquidity.md §14 limitation 2 unaddressed**, which is one of the three gaps this phase exists to close |
| ❌ | Cannot express "three levels stacked within a tick", which is the actual ICT claim about where stops rest |

**Verdict: rejected as the primary answer, retained as the fallback.** Every per-level
distance R2-07 already exposes survives untouched, so if pools prove useless in Phase 4,
nothing is lost by ignoring the pool columns.

### Ranking

| Rank | Candidate | Causal | Immutable | Stable id | Complexity | Outcome |
|---|---|---|---|---|---|---|
| **1** | **P1 derived view** | ✅ | ✅ | ✅ | low | **implemented** |
| 2 | P5 no pools | ✅ | ✅ | ✅ | none | fallback, documented |
| 3 | P2 confirmed events | ✅ | ❌ | ❌ | high | documented |
| 4 | P4 density | ✅ | ✅ | n/a | medium | deferred to Phase 4 |
| 5 | P3 merged super-levels | ✅ | ❌ | ❌ | medium | rejected outright |

**Exactly one is implemented.** No policy enum switches between pool definitions — the R2-06
precedent: *"two live definitions would mean every downstream result has to name which one
it used."*

---

## 4. Tolerance — five candidates

| # | Candidate | For | Against | Outcome |
|---|---|---|---|---|
| T1 | **Fraction of measured median bar range, per (symbol, timeframe)** | Evidence-backed; the repository's own precedent (`PRODUCTION_TARGET_PARAMETERS`); scales correctly across two instruments 160× apart in point terms; a single uniform divisor so nothing is chosen case by case | The divisor is still a judgement; derived from one month (the same honest limitation R2-08 already carries) | ✅ **selected** |
| T2 | A flat point constant | Trivial | 50 points is 0.0005 on EURUSD and 5 cents on gold — one number asking two different questions. R2-08 measured the damage: a flat barrier swallowed **86 %** of XAUUSD labels | rejected |
| T3 | Percentage of price | Scale-free | Ties tolerance to price *level*, not to volatility. Gold at 4000 gets four times the tolerance of gold at 1000 with no change in behaviour | rejected |
| T4 | ATR multiple | The conventional volatility unit | **Explicitly refused by the engine.** features.md §1: *"No ATR or volatility normalisation exists — no approved contract defines one, and adding it here would smuggle a modelling hypothesis into a representation layer."* Reversing that in a feature story would be a silent architectural change | rejected on principle |
| T5 | Reuse `equal_tolerance_points` (1.0 point) | No new number at all | Answers a **different question** (are two *swing pivots* the same high?) and would make the pool tolerance change whenever R2-04's did. 1.0 point on gold is 0.001 USD — every level its own pool, so the feature would be vacuous | rejected |

T5 is the trap worth naming: reusing an existing constant *looks* like restraint and is
actually a silent coupling of two unrelated decisions.

---

## 5. Internal / external — three candidates

| # | Candidate | Verdict |
|---|---|---|
| I1 | **R2-06 dealing range** | ✅ **selected.** The only causal, structurally meaningful range the engine has. R2-04 §14 limitation 3 already names it as the missing ingredient |
| I2 | Rolling max/min over N bars | ❌ Causal but *"has no structural meaning and drifts every bar"* (R2-06 concept map, candidate A). It would also introduce a lookback constant with no basis |
| I3 | Session or daily range | ❌ Mixes timeframes silently — R2-06 rejected the same move for premium/discount (candidate D). A daily range read on 1H bars is a cross-timeframe projection wearing a local label |

**The cost of I1, stated plainly:** before the first confirmed structural break there is no
range, so `zone = UNKNOWN`. On a fresh month's first bars that is a real and visible warm-up.
`UNKNOWN` is a third value precisely so the warm-up is legible rather than reported as
`INTERNAL`.

---

## 6. Interaction states — the two rejected redesigns

| Candidate | Verdict |
|---|---|
| Redefine `SWEPT` to require a close beyond | ❌ Changes the meaning of a shipped, tested, dataset-recorded feature. R2-04 removed a `require_rejection` flag for a closely related reason: it created an incoherent state where a level was consumed but emitted no event. `CONFIRMED_TAKEN` is derived from `closed_beyond`, which R2-04 already publishes — a new *name*, not a new *rule* |
| **Add a `TOUCHED` lifecycle state** | ❌ **VOID.** An earlier draft proposed it as a fifth, non-consuming state. **`APPROACHED` already IS "touched"** — R2-04's existing state, shipped and tested. Two names for one event is exactly the duplication the engine forbids. R2-09 **enables** `APPROACHED` (R2-04 ships it disabled) and **counts** it as `approach_count`; it adds no enum value and renames nothing |
| Add `PARTIALLY_SWEPT` for pools | ❌ R2-04 is explicit that there is no state between resting and taken. A pool losing one member is fully described by the members' own states plus the pool's changing cardinality, which the derived-view design gives for free |

---

## 7. Leakage criteria inherited

The six proofs of [R2-05x-CONCEPT-MAP.md](../ict/R2-05x-CONCEPT-MAP.md) §4 apply unchanged
— but **the master story's §6.3 numbering supersedes it**, and §6.3a maps the two so no
proof is silently dropped. Prefix equivalence and naive divergence survive as named testing
obligations rather than as leakage IDs.

**The deliberately broken implementation for this story (L8):**

> Cluster every level the analysis produces, then filter the resulting pools by `as_of`.

Written out in the leakage suite and proven to disagree with the causal implementation. It
is the single most likely way a pool layer leaks, and it is **invisible in the output** — a
leaked pool has a plausible price, a plausible cardinality and a valid confirmation
timestamp. Only comparison against the causal implementation exposes it.

The test must report **how many instants differ**, so the divergence is quantified rather
than merely asserted.

---

## 8. Ambiguity register

Full register in [the story](R2-09-LIQUIDITY-STORY.md) §14 (A1 … A10). Summary of what is
**refused** rather than decided:

| Refused | Because |
|---|---|
| A numeric pool tolerance presented as ICT doctrine | The source material gives none. Ours is labelled **engineering**, derived from measurement, and carries its rationale on every parameter row |
| "Previous significant high" | No deterministic definition exists in the source material, and swing significance is an open upstream question (HANDOFF item 6). Building one here would invent doctrine *and* duplicate an R2-02/R2-03 responsibility |
| "The nearest pool is the target" | ICT says price *seeks* liquidity, not that it seeks the *nearest*. This is exactly the claim Phase 4 exists to test |
| A liquidity "strength" or "quality" score | No source defines one. The raw material a ranking would need — distance, cardinality, member types, age, internal/external — is all exposed instead |

---

## 9. What R2-09 does not build

Stated so absence is a decision:

Previous-month levels · trendline/diagonal liquidity · round-number levels · volume-profile
or order-flow liquidity · liquidity "strength" scores · pool ranking · a target predictor ·
forming-period running extremes as levels · any change to `LiquidityDetector` ·
`ICTMarketState` wiring (that is R2-12) · `ICTFeatureVector` columns (that is R2-13) · any
ML, probability, label or normalisation · any signal, entry, stop, target or backtest rule.
