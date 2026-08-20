# ICTFeatureVector — the feature catalogue

**Story:** R2-07 · **Module:** [`ict_kronos/ict/feature_vector.py`](../../ict_kronos/ict/feature_vector.py)
· **State layer:** [market_state.md](market_state.md)

`feature_version` — **`r2-07.1`** · 56 features · column order is `FEATURE_NAMES`

## 1. Reading this document

Every feature below states its **unit**, its **range**, and what **missing** means for
it. Those three columns are the contract; the name is just a label.

Three rules hold for every row without exception:

- **The observation timestamp is the anchoring bar's `close_time`**, and every value is
  computable from information whose `confirmation_timestamp <= ` that instant. The
  vector is projected from an `ICTMarketState` that was already built point-in-time; it
  never touches a frame or a detector, so there is no future for it to reach.
- **`0` is a value, not "missing".** Zero is a real price distance and a real count.
  Missing is `None` in `as_dict()` and `math.nan` in `as_row()` — **never zero**.
- **Distances are in instrument points**, from `symbol.spec.point_value` (1e-5 EURUSD,
  1e-3 XAUUSD). Prices and points are never mixed. No ATR or volatility normalisation
  exists — no approved contract defines one, and adding it here would smuggle a
  modelling hypothesis into a representation layer.

**No label, no target.** Phase 3 owns labelling and is kept strictly separate.

## 2. Categorical encodings

Declared as module constants, never fitted from data — a label encoder fitted on
observed values silently renumbers between datasets.

| Table | Mapping |
|---|---|
| `DIRECTION_CODES` | `bearish -1`, `neutral 0`, `bullish 1` |
| `STRUCTURE_STATE_CODES` | `undefined 0`, `bullish 1`, `bearish -1` |
| `DELIVERY_STATE_CODES` | `undefined 0`, `bullish 1`, `bearish -1` |
| `ZONE_CODES` | `discount -1`, `equilibrium 0`, `premium 1` |
| `BIAS_CODES` | `unknown 0`, `bearish -1`, `neutral 0`, `bullish 1` |
| `BREAK_TYPE_CODES` | `bos 1`, `mss 2`, `choch 3` |

Sign is meaningful throughout: **negative is bearish-leaning, positive bullish-leaning**,
so a linear model reads the sign correctly without one-hot expansion.

`BIAS_CODES` maps **both** `unknown` and `neutral` to `0`. They are different facts and
`ICTMarketState.bias.bias` keeps them apart; the vector collapses them because a linear
model has no use for two distinct zeroes. **If the distinction matters, read the state** —
this is the one place the projection is deliberately lossier than the truth, and it is
recorded here rather than discovered later.

Missing categories are `None`, never a reserved integer: every integer in these tables
is a real category, and reusing one for "absent" would make the two indistinguishable.

## 3. Identity columns

Emitted by `as_dict()` before the features; **not** part of `FEATURE_NAMES` and not in
`as_row()`.

| Column | Meaning |
|---|---|
| `symbol` | instrument |
| `timeframe` | the bars the state was built from — R2-07 is timeframe-local |
| `as_of` | ISO-8601 UTC; the anchoring bar's `close_time` |
| `feature_version` | `r2-07.1` — ties a dataset to these definitions |

## 4. Price / dealing range (9)

| Feature | Unit | Range | Missing means |
|---|---|---|---|
| `close` | price | > 0 | never missing |
| `distance_from_true_daily_open_points` | points, signed | ℝ | no observable True Daily Open yet |
| `distance_from_equilibrium_points` | points, signed | ℝ | no dealing range yet |
| `percentage_position` | ratio | ℝ — **unclamped** | no dealing range yet |
| `is_premium` | flag | 0/1 | no dealing range — **not** 0 |
| `is_discount` | flag | 0/1 | no dealing range — **not** 0 |
| `is_equilibrium` | flag | 0/1 | no dealing range — **not** 0 |
| `dealing_range_width_points` | points | > 0 | no dealing range yet |
| `dealing_range_direction_code` | code | −1/0/1 | no dealing range yet |

`percentage_position` is **not clamped**, and values outside `[0, 1]` are the *common*
case: R2-06 anchors the range on the **broken** structural level, so right after a break
price sits beyond it. Measured on the four-day fixture: 42–81% of observations. See
[dealing_range.md](dealing_range.md) §14a. The three zone flags are `None` together —
either there is a range or there is not.

## 5. Structure (12)

| Feature | Unit | Range | Missing means |
|---|---|---|---|
| `structure_state_code` | code | −1/0/1 | never missing (`undefined` is 0) |
| `structure_direction_code` | code | −1/0/1 | never missing |
| `latest_break_type_code` | code | 1/2/3 | no break observable yet |
| `latest_break_direction_code` | code | −1/0/1 | no break observable yet |
| `has_bos` / `has_mss` / `has_choch` | flag | 0/1 | never missing |
| `bos_count` / `mss_count` / `choch_count` | count | ≥ 0 | never missing |
| `bars_since_structural_break` | bars | ≥ 0 | no break observable yet |
| `distance_from_structural_level_points` | points, signed | ℝ | no break observable yet |

`bars_since_*` counts **bars, not elapsed time**: across a weekend a time-based measure
would imply activity that did not occur.

`choch_count` is `0` under the default `ChochPolicy.SYNONYM`, which is correct and
intentional — CHoCH is MSS by default and no second algorithm exists.

## 6. Liquidity (8)

| Feature | Unit | Range | Missing means |
|---|---|---|---|
| `buy_side_liquidity_count` | count | ≥ 0 | never missing |
| `sell_side_liquidity_count` | count | ≥ 0 | never missing |
| `nearest_buy_side_points` | points, absolute | ≥ 0 | **no active buy-side level exists** |
| `nearest_sell_side_points` | points, absolute | ≥ 0 | **no active sell-side level exists** |
| `has_recent_sweep` | flag | 0/1 | never missing |
| `latest_sweep_side_code` | code | −1 buy-side / +1 sell-side | no sweep observable yet |
| `latest_sweep_is_rejection` | flag | 0/1 | no sweep observable yet |
| `bars_since_sweep` | bars | ≥ 0 | no sweep observable yet |

The `count == 0` / `nearest is None` pair is the clearest illustration of §1's second
rule: a count of zero says "no resting liquidity on that side", while a missing distance
says "there is nothing to measure to". A model given `0` for both would be told price is
*sitting on* a level that does not exist.

`latest_sweep_side_code` is signed by **consequence**, not by side: taking sell-side
liquidity is a bullish-leaning fact (`+1`), taking buy-side is bearish-leaning (`−1`).

## 7. Imbalance (7)

| Feature | Unit | Range | Missing means |
|---|---|---|---|
| `bullish_fvg_count` / `bearish_fvg_count` | count | ≥ 0 | never missing |
| `nearest_bullish_fvg_points` | points, absolute to midpoint | ≥ 0 | no active bullish FVG |
| `nearest_bearish_fvg_points` | points, absolute to midpoint | ≥ 0 | no active bearish FVG |
| `ifvg_count` | count | ≥ 0 | never missing |
| `latest_ifvg_direction_code` | code | −1/0/1 | no IFVG observable yet |
| `bpr_count` | count | ≥ 0 | never missing |

"Active" is each detector's own definition: an FVG that is partially filled is still
active; one fully mitigated is not. **Mitigation is never reinterpreted as inversion** —
`ifvg_count` counts genuine inversions only, which is the whole point of R2-05.2.

Distance is measured to the zone's **midpoint** (ICT's consequent encroachment), which
`FvgZone.midpoint` already defines, so no geometry is re-derived here.

## 8. Institutional & composites (11)

| Feature | Unit | Range | Missing means |
|---|---|---|---|
| `bullish_order_block_count` / `bearish_order_block_count` | count | ≥ 0 | never missing |
| `bullish_breaker_count` / `bearish_breaker_count` | count | ≥ 0 | never missing |
| `latest_breaker_direction_code` | code | −1/0/1 | no live Breaker |
| `rdrb_count` | count | ≥ 0 | never missing |
| `has_cisd` | flag | 0/1 | never missing |
| `delivery_state_code` | code | −1/0/1 | never missing (`undefined` is 0) |
| `bars_since_cisd` | bars | ≥ 0 | no CISD observable yet |
| `unicorn_count` | count | ≥ 0 | never missing |
| `latest_unicorn_direction_code` | code | −1/0/1 | no live Unicorn |

An Order Block counts as active when it is `ACTIVE` or `PARTIALLY_FILLED` — not
mitigated and not invalidated. Those are **different facts** in R2-05.3 and stay
different; the state exposes both and the vector counts the union of the live ones.

`unicorn_count` is routinely large relative to Breakers — several gaps overlap one
Breaker and none is collapsed (see [unicorn.md](unicorn.md) §12b). That is the
specified behaviour, not a defect, and the vector reports it as measured.

## 9. Session & temporal (6)

| Feature | Unit | Range | Missing means |
|---|---|---|---|
| `trading_day_age_minutes` | minutes | ≥ 0 | no observable True Daily Open yet |
| `session_elapsed_minutes` | minutes | ≥ 0 | no session active at this instant |
| `minute_of_session` | minutes | ≥ 0 | no session active at this instant |
| `active_session_count` | count | ≥ 0 | never missing |
| `day_of_week` | 0=Mon … 6=Sun | 0–6 | never missing |
| `hour_of_day` | UTC hour | 0–23 | never missing |

Sessions come from R2-01's existing definitions and DST handling; **no second timezone
implementation exists** in this layer, and a guard test asserts it. Overlapping windows
(London and New York share hours) are all reported in the state's `active_sessions`;
`active_session_count` is their number, and `session_elapsed_minutes` refers to the
first by name so the choice is deterministic.

`day_of_week` and `hour_of_day` are **UTC**, matching every timestamp in the engine.

## 10. Derived bias (3)

| Feature | Unit | Range | Missing means |
|---|---|---|---|
| `bias_code` | code | −1/0/1 | never missing |
| `bullish_evidence_count` | count | 0–4 | never missing |
| `bearish_evidence_count` | count | 0–4 | never missing |

The two counts are exposed **independently** of the verdict, so a consumer can ignore
`bias_code` entirely and use the evidence. The counting rule is in
[market_state.md](market_state.md) §9; it is counting, not scoring, and there are no
weights because a weight is a hypothesis.

## 11. Serialisation and determinism

```
FEATURE_NAMES          the schema — column order for as_dict() and as_row()
as_dict()              identity columns first, then features; missing -> None
as_row()               floats in FEATURE_NAMES order;      missing -> math.nan
from_dict(as_dict())   round-trips EXACTLY, including None
vectors_to_frame()     a DataFrame in the same order, every time
```

`FEATURE_NAMES` is declared explicitly rather than derived from the dataclass fields, so
reordering a field cannot silently renumber an existing dataset's columns.

Timestamps serialise as ISO-8601 UTC, enums as their `str` value, and `None` stays
`None` — there is no sentinel numeric encoding of missing anywhere in `as_dict()`.

## 12. Known ambiguity and limitations

| Element | Status |
|---|---|
| Which features to include | **Engineering.** No ICT source defines a feature vector. The set covers every approved detector once; it is not claimed to be sufficient or minimal. |
| `unknown` and `neutral` both coding to `0` | **Deliberate lossy projection** (§2). The state keeps them apart. |
| Points as the universal distance unit | **Engineering choice.** Comparable across instruments without a normalisation hypothesis. |
| No ATR / volatility normalisation | **Deliberate absence.** No approved contract defines one. |
| `session_elapsed_minutes` uses the first active window by name | **Engineering choice** for determinism under overlap; every active window is in the state. |
| Timeframe-local | **Directed by the R2-07 brief.** `align_htf_context()` remains the only sanctioned HTF join when that is authorised. See [market_state.md](market_state.md) §10. |
| **No feature is claimed to be predictive** | Whether this representation carries information is exactly what Phases 4 and 6 exist to answer. R2-07 makes the question askable; it does not answer it. |

## 13. Explicit non-goals

Labels or targets (Phase 3) · model training (Phase 4) · Kronos fusion (Phase 5/6) ·
setup-quality scoring · feature selection · normalisation or scaling · backtesting ·
execution · cross-timeframe projection.
