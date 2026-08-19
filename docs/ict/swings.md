# SwingDetector (R2-02)

Story: [R2-02](../../user-stories/Phase-2-ICT-Engine/R2-02-swing-detection.md) · Code: [`ict_kronos/ict/swings.py`](../../ict_kronos/ict/swings.py)

---

## 1. Definition

A **swing high** is a bar whose high is not exceeded by the `left` bars before it or the `right` bars after it. A **swing low** is the mirror. Together they are the pivots market structure (R2-03) is built from.

**The confirmation lag is the entire point of this story.** A swing high at bar *i* is not a swing until `right` further bars have failed to exceed it. Charting software draws the pivot at bar *i*, and that is exactly the timestamp a naive implementation records — making every downstream feature `right` bars early. This is the single most common look-ahead bug in retail ICT research.

## 2. Algorithmic rule

For each index *i* with a complete window `[i - left, i + right]`:

```
left_extreme  = max(high[i-left : i])          # or min(low[...]) for a low
right_extreme = max(high[i+1 : i+right+1])
```

Then, by tie policy (§8):

| Policy | Swing high condition |
|---|---|
| `FIRST` *(default)* | `high[i] >  left_extreme` **and** `high[i] >= right_extreme` |
| `LAST` | `high[i] >= left_extreme` **and** `high[i] >  right_extreme` |
| `STRICT` | `high[i] >  left_extreme` **and** `high[i] >  right_extreme` |
| `ALL` | `high[i] >= left_extreme` **and** `high[i] >= right_extreme` |

Swing lows use the same structure with `<` / `<=` against minima.

A swing is emitted **only when bar `i + right` exists in the observed data**. That single rule is what makes batch detection equal streaming replay.

Implementation note: the detector uses rolling windows (`series.rolling(n).max().shift(...)`) rather than a Python loop, because a multi-year 1M series is millions of bars. `shift(-right)` is easy to get off by one, so [`reference_pivots()`](../../ict_kronos/ict/swings.py) provides a transparently-correct O(n·window) version and the tests assert the two agree — across all four tie policies, four window shapes, synthetic noise, **and real prices**.

## 3. Input

| | |
|---|---|
| `frame` | Canonical candle frame (`CANDLE_COLUMNS`), UTC |
| `symbol` | `Symbol` — supplies `point_value` for `strength` |
| `timeframe` | `Timeframe` — supplies bar duration, hence `close_time` |
| `config` | `SwingConfig(left, right, tie_policy)`, default `(2, 2, FIRST)` |

`left >= 1` and `right >= 1` are enforced. **`right = 0` is refused**: a pivot confirmed by its own bar has zero lag and is not a swing, just "this bar's high". Permitting it would quietly admit a zero-lag pivot into the feature set.

## 4. Output

**`SwingPoint`** — direction, both timestamps, `price_level`, `reference_level`, `strength`, positional `index`, `bars_to_confirm`.

**`IctEvent`** — `SWING_HIGH` / `SWING_LOW` with the full Phase 2 contract; `metadata` carries `left`, `right` and `tie_policy` so any stored dataset traces back to the parameters that produced it.

### Conventions

- **Direction:** swing high → `BULLISH`, swing low → `BEARISH`.
- **`reference_level`:** the most extreme neighbouring price in the window — the level the pivot had to beat.
- **`strength`:** prominence in instrument points, `(price_level − reference_level) / point_value` (sign-flipped for lows). Always ≥ 0. **Zero on a plateau** under `FIRST`/`ALL` — meaningful (the pivot stands zero points clear of its twin), not a defect.

## 5. Event timestamp

`event_timestamp` = **the pivot bar's open time**. Where the swing sits on the chart.

## 6. Confirmation timestamp

`confirmation_timestamp` = **the close time of bar `i + right`**.

Not the pivot bar's close, and not the confirming bar's open. Both would be early. The verdict depends on the high of bar `i + right`, and a bar's high is not final until it closes.

```
bars:              i-2   i-1    i    i+1   i+2
                              PIVOT              ← event_timestamp (open of i)
                                            └──► confirmation_timestamp (close of i+2)
```

With `left=right=2` on 5-minute bars, a pivot at 09:10 confirms at **09:25** — a 15-minute lag. Anything that treats it as known at 09:10 has leaked 15 minutes of future.

**Downstream cannot bypass this.** [`filter_observable(events, as_of)`](../../ict_kronos/ict/contract.py) is the single gate feature assembly goes through, and `SwingDetector.observable_at()` is proven equal to detecting over only the bars visible at `as_of`.

## 7. Immutability semantics

**A confirmed swing is never revised.** The rule evaluates the bounded window `[i - left, i + right]`, every bar of which has closed by the confirmation instant, so no later candle can change the verdict. Appending bars can only ever *add* swings.

Two consequences worth stating explicitly:

**A later higher high does not invalidate an earlier swing high.** Swings are *local* pivots, not running extremes. A new peak later is a new swing, not a retraction of the old one. Tested by `test_a_later_higher_high_does_not_invalidate_an_earlier_swing`.

**The window is positional over bars present, not over wall-clock time.** If a market gap sits inside the window, the confirming bar arrives later in wall-clock terms and the lag exceeds `(right + 1) × bar_duration`. This is **correct** — the pivot genuinely could not be known until those bars existed — but it means the lag is not a fixed duration.

The real data shows this clearly. In EURUSD 5m over 2024-03-08 → 2024-03-12, 102 of 107 swings have exactly the nominal 20-minute lag. The exceptions are all genuine gaps, including:

| Pivot | Confirmed | Lag |
|---|---|---|
| Fri 2024-03-08 **21:55** | Sun 2024-03-10 **21:15** | **1 day 23:20** |

A pivot formed just before the Friday close is not knowable until the market reopens on Sunday. That is the confirmation rule doing exactly its job.

## 8. Edge cases

| Case | Behaviour |
|---|---|
| Fewer than `left + right + 1` bars | No swings. Too early, not an error |
| Pivot within the first `left` bars | Not detectable — no left window exists |
| Pivot within the last `right` bars | **Not yet confirmed.** Visible on a chart, correctly absent here |
| Plateau (equal consecutive extremes) | Per tie policy — see below |
| Entirely flat series | `STRICT` → none; `ALL` → every interior bar |
| Market gap inside the window | Window is positional; lag exceeds nominal (§7) |
| Unsorted input | Sorted first; result is identical |
| Empty frame | Empty result |
| `right = 0` or `left = 0` | `ValueError` at config construction |

## 9. Known ambiguities — documented, not silently resolved

### Tie / plateau policy

Flat tops and bottoms are common in FX and ubiquitous on quantised instruments (gold especially). Leaving this to comparison-operator accident would make the result depend on an unexamined `>` vs `>=`. Four readings, all valid:

| Policy | On a 2-bar plateau at indices 2–3 | Rationale |
|---|---|---|
| **`FIRST`** *(default)* | swing at **2** | One swing per plateau, at the earliest — the most honest timestamp, and the earliest confirmation |
| `LAST` | swing at **3** | Some traders treat the final touch as the pivot |
| `STRICT` | **none** | A plateau is not a clean pivot |
| `ALL` | swings at **2 and 3** | Every touch is structurally relevant |

`FIRST` is the default because it yields exactly one swing per plateau (no duplicates for R2-03 to disambiguate) at the earliest confirmable timestamp.

### Swing definition itself

We implement the **n-bar fractal**. Alternatives in circulation, deliberately not adopted:

- **ZigZag / percentage-threshold** — a pivot requires a minimum retracement. More selective, but its confirmation is unbounded (you may wait indefinitely), which conflicts with the streaming guarantee.
- **ATR-normalised swings** — like ZigZag with a volatility-scaled threshold. Reasonable, and a candidate for later, but it introduces an ATR lookback dependency.
- **Structural swings** — defined by BOS/MSS rather than by neighbours. Circular here, since R2-03 builds *on* swings.

The fractal definition was chosen because it is deterministic, has a **bounded** confirmation lag, and is trivially streamable — all three required by the batch-equals-replay guarantee.

### Configuration

```bash
export ICT_SWING_LEFT=3
export ICT_SWING_RIGHT=3
export ICT_SWING_TIE_POLICY=strict     # first | last | strict | all
```

Or in code: `SwingDetector(SwingConfig(left=3, right=3, tie_policy=TiePolicy.STRICT))`.

**Larger `right` means stronger pivots and a longer lag.** That trade-off is the parameter's whole meaning: `right=5` on 5m bars costs 30 minutes of latency before a pivot can be used. Phase 4 should treat it as a hyperparameter and report it, not tune it silently.

## 10. Test coverage

**139 tests** across three files, plus the shared-contract suite.

| File | Tests | Covers |
|---|---|---|
| `tests/test_swings.py` | 64 | Config validation, detection, reference/strength, all four tie policies, boundaries, vectorised-vs-naive equivalence, events, configurability |
| `tests/test_swings_leakage.py` | 24 | Confirmation lag, **immutability**, batch vs streaming replay (incl. bar-by-bar), downstream observability |
| `tests/test_swings_real_data.py` | 51 (+1 skip) | Real EURUSD + XAUUSD across 1m/5m/15m, plateau reality check, gap/weekend confirmation, interaction with R2-01 sessions |

Specifically required by the story: configurable `left`/`right` ✅ · equal/flat highs and lows ✅ · multiple candidate swings ✅ · insufficient history ✅ · boundary conditions ✅ · batch vs streaming replay ✅ · real EURUSD/XAUUSD ✅ · shared contract interaction ✅ · explicit leakage tests ✅.

Real-data tests **skip cleanly** when `data/` is absent.

## 11. Known limitations

1. **The window is positional, not temporal** (§7). Across a gap the lag exceeds nominal. Correct, but a consumer computing "expected latency" from `right × duration` will be wrong at gaps. A gap-aware variant that refuses to span a session break is a candidate for later.
2. **Every swing is a candidate, unranked.** With `left=right=2` on 5m data the detector emits many minor pivots. `strength` (prominence) is provided for filtering, but no significance ranking or higher-timeframe projection exists yet — R2-03 and R2-07 will need one.
3. **`detect()` is O(n) per call and stateless.** The bar-by-bar replay test is therefore O(n²) and deliberately kept to 120 bars. A genuine incremental detector (maintaining a rolling window) is a candidate if live inference needs it; batch research does not.
4. **No multi-timeframe swing projection.** A 1H swing is currently found by running the detector on 1H bars, not by projecting from 5M. R2-07 owns cross-timeframe assembly via `align_htf_context()`.
5. **`index` is positional in the frame passed in.** It is diagnostics only and must never be used as a join key across differently-sliced frames — the timestamps are the identity.
