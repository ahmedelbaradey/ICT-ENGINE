# Prediction targets — definitions, conventions and what is deliberately unanswered

**Story:** R2-08 · **Module:** [`ict_kronos/features/targets.py`](../../ict_kronos/features/targets.py)
· **Dataset:** [dataset.md](dataset.md) · **Splits:** [splits.md](splits.md)

## 1. The temporal contract

The engine has exactly two halves, and they obey opposite rules:

```
FEATURES(T)  <-  information observable at T, and nothing else     ict_kronos/ict      (R2-07)
TARGET(T)    <-  may use information strictly after T              ict_kronos/features (R2-08)
```

That asymmetry is what makes supervised learning possible at all. It is also the single
most dangerous line in the repository, so it is drawn **between modules** rather than
inside one: `targets.py` imports nothing from the feature layer, the feature layer
imports nothing from here, and guard tests assert both directions. A target cannot leak
into a feature because a feature has no way to reach one.

The `DatasetBuilder` is the only place the two meet, and it only puts them side by side.

## 2. Conventions — every one is a choice

None of what follows is ICT doctrine. Each convention is stated here with the
alternative it was chosen over, because each one silently changes what a model is being
asked to predict.

| Element | Chosen | Alternative not taken | Why |
|---|---|---|---|
| Reference price | **Close of the bar whose close is `as_of`** | open of the next bar; mid; VWAP | It is the last price knowable at T, and it is the same number `ICTFeatureVector.close` reports — so feature and target agree on where "now" is |
| Future window | **Bars `i+1 … i+H` inclusive** | `i … i+H` | Including bar `i` would let a target read the bar the observation was made on |
| Return | **Simple close-to-close** | log return; open-to-close; high/low-based | One convention, stated once. `future_return` and `future_move_points` are the same move in two units and are never mixed |
| Threshold unit | **Instrument points** | return fraction; ATR multiples | Matches R2-07 §8, so nothing silently rescales between EURUSD and XAUUSD; an ATR unit would smuggle a volatility model into a label |
| Horizon unit | **Bars** | wall-clock minutes | Bars are what the detectors index. Across a weekend a time-based horizon would imply activity that did not occur |

Because horizons are counted in bars, a window that straddles a weekend is *wider* in
wall-clock time than `H` bar durations. That is visible rather than smoothed over:
every value carries `future_window_start` and `future_window_end`.

## 3. The four target types

### 3.1 `FUTURE_RETURN`

```
reference   = close[i]
final       = close[i + H]
future_return       = (final - reference) / reference
future_move_points  = (final - reference) / point_value
```

An intermediate spike does not affect it — that is what "close-to-close" means, and
there is a test asserting it so the convention cannot drift.

### 3.2 `DIRECTION`

```
move_points >= +threshold_points  ->  UP
move_points <= -threshold_points  ->  DOWN
otherwise                         ->  NEUTRAL
```

`threshold_points` is **configuration**. It is never fitted from the dataset, and the
same bars classify differently under two thresholds — there is a test that proves it.

**The boundary belongs to the direction.** `>=` is the declared rule, so a move of
exactly the threshold is `UP`.

**A zero threshold is degenerate, and it is degenerate on purpose.** With
`threshold_points = 0` the `UP` and `DOWN` conditions overlap at exactly zero and
`NEUTRAL` becomes unreachable. `UP` is checked first, so a flat market classifies `UP`.
This is recorded so nobody discovers it from a confusing class balance. A negative
threshold is refused outright.

**Points are rounded to 6 decimals before comparison.** A price is quantised to
`point_value`, so a move measured in points is a whole number of points up to binary
representation noise — and that noise is exactly what breaks the boundary rule:
`(1.0002 - 1.0) / 1e-5` evaluates to `19.999999999997797`, not `20`. Six decimals is a
million times finer than any supported instrument can express. It is a numerical-safety
constant, not a claim about how precisely price moves.

### 3.3 `EXCURSION` — MFE / MAE without the ambiguity

```
up_excursion_points   = (max(high[i+1 … i+H]) - reference) / point_value
down_excursion_points = (reference - min(low[i+1 … i+H])) / point_value
```

Reported as **two separate values, never one signed "MFE"**. Which excursion is
favourable and which is adverse depends on the side of a hypothetical trade, and folding
that into a single number would hide a long/short assumption inside the label.

**Both are signed, never clamped.** A window that never trades above the reference has a
*negative* upward excursion. Clamping it to zero would assert the market touched a price
it never touched.

A partial window is **unresolved**, not truncated: half a horizon reports a smaller
excursion, which is a wrong answer rather than a partial one.

### 3.4 `TP_BEFORE_SL` — and the one thing OHLC cannot tell us

Inputs are all explicit: `side`, `take_profit_points`, `stop_loss_points`, `horizon_bars`.
The side is **required** — whether a barrier is a profit or a loss depends on it, and
inferring it would hide the assumption inside the label.

Bars `i+1 … i+H` are scanned in order. The first bar that touches a barrier decides.

> **If one bar touches both barriers, the answer is `UNRESOLVED`.**

An OHLC bar records four prices and no sequence. "Open→high→low→close", "stops always
fill first", "use the close to break the tie" — each is a fabrication, and the label a
model then learns from would be that fabrication rather than the market. The
`SAME_BAR_AMBIGUITY` reason is recorded along with the timestamp of the offending bar, so
the ambiguity is countable rather than invisible.

This would only change if an **approved** lower-timeframe ordering mechanism existed.
None does, and R2-08 does not invent one.

A race decided early **is** resolved even when the horizon later runs off the end of the
data: bars that do not exist cannot change an outcome that already happened.

## 3.5 What the real fixture actually produced — and why it matters

Measured on EURUSD + XAUUSD, 2024-03-08 → 03-11, with `threshold = 20 points` and
`TP = SL = 50 points`:

| Symbol | TF | Rows | `ret_4` | `dir_4` | `tpsl_8` | Dominant unresolved reason |
|---|---|---:|---:|---:|---:|---|
| EURUSD | 15m | 190 | 97.9% | 97.9% | 73.7% | `no_touch_within_horizon` (40) |
| EURUSD | 1H | 44 | 90.9% | 90.9% | 84.1% | `same_bar_ambiguity` (5) |
| EURUSD | 4H | 9 | 55.6% | 55.6% | 44.4% | `same_bar_ambiguity` (4) |
| XAUUSD | 15m | 185 | 97.8% | 97.8% | **13.5%** | `same_bar_ambiguity` (**159**) |
| XAUUSD | 1H | 44 | 90.9% | 90.9% | **9.1%** | `same_bar_ambiguity` (**39**) |
| XAUUSD | 4H | 9 | 55.6% | 55.6% | 22.2% | `same_bar_ambiguity` (6) |

**A point is not a unit of volatility.** 50 points is 0.0005 on EURUSD and 0.05 USD on
gold — five hundredths of a dollar, far inside a typical XAUUSD bar. So nearly every
XAUUSD bar touches both barriers and the honest answer is `UNRESOLVED` 159 times out of
185. The same effect shows in the direction classes: a 20-point threshold leaves 79 of
190 EURUSD rows `NEUTRAL` and only **1** of 185 XAUUSD rows.

Nothing here is broken. The rule is doing exactly what it says, and it is telling us
something a silent tie-break would have hidden. Two consequences follow, and both are the
caller's to make explicitly:

* **Barrier distances and thresholds must be chosen per instrument**, relative to that
  instrument's typical bar range. A single number applied to both symbols is measuring two
  different questions.
* **Same-bar ambiguity is not a rare edge case.** It is the dominant unresolved reason
  whenever barriers are tight relative to bar range, and it grows with timeframe. The only
  principled cure is finer bars — an approved lower-timeframe ordering mechanism, which
  does not exist and which R2-08 does not invent.

Coverage falls to ~56% on 4H simply because nine bars cannot support a 4-bar horizon for
most rows. That is a **dataset limitation**, reported rather than engineered away.

## 4. Horizons

`DEFAULT_HORIZONS = (1, 2, 4, 8, 16)` is a convenient sweep and **nothing more** — powers
of two, not an ICT claim. No target may depend on an implicit horizon: `horizon_bars` is
required on every specification and is carried on every value.

## 5. Unresolved is a real answer

| Reason | Meaning |
|---|---|
| `INSUFFICIENT_HISTORY` | The horizon extends past the last available bar |
| `NO_TOUCH_WITHIN_HORIZON` | The full horizon was available and neither barrier was touched |
| `SAME_BAR_AMBIGUITY` | One bar touched both barriers; intrabar order is unknowable |
| `MALFORMED_FUTURE_BAR` | A bar inside the window carries a non-finite price |

An unresolved target is `resolved=False`, every value field is `None`, and the reason is
always present. It is **never** `0`, **never** `NEUTRAL`, **never** `False`. A model
trained on those substitutions is being taught that missing data means indecision.

The reasons are not interchangeable, and the audit counts them separately: "we ran out of
data at the end of the file" and "the market hit both barriers in one bar" are different
problems with different fixes.

Malformed bars are **reported, never repaired**. Nothing is interpolated, carried forward
or skipped over.

## 6. Missing-value policy

| Situation | Representation |
|---|---|
| A resolved target whose value happens to be zero | `0.0` — a real answer |
| A target that could not be resolved | `None` + `unresolved_reason` |
| The numeric projection of a missing value | `math.nan` (`as_row`) |

This is R2-07's rule extended, not a second policy: `0` and `UNKNOWN` are different, and
`NaN` lives only in the numeric row.

## 7. Known ambiguities

| Element | Classification |
|---|---|
| Close-to-close as *the* return convention | **Engineering decision** (§2). Open-to-close would model a different, execution-flavoured question |
| Threshold expressed in points rather than return fraction | **Engineering decision** (§2), chosen for consistency with every other distance in the engine |
| Powers-of-two horizons | **Engineering convenience** (§4). Not doctrine |
| Treating a same-bar double touch as unresolved | **Honest limitation of OHLC** (§3.4), not a modelling preference |
| Excursion reported as two signed values | **Engineering decision** (§3.3) to avoid encoding a trade side in a label |
| Rounding points to 6 decimals | **Numerical safety** (§3.2), not a precision claim |
| A point is not a unit of volatility | **Consequence of the points convention** (§3.5). Thresholds and barriers are instrument-*native* but not instrument-*calibrated*; choosing one number for both symbols measures two different questions |

## 8. Explicit non-goals

No model training · no Kronos · no XGBoost / LightGBM · no hyperparameter search · no
backtesting · no execution · no position sizing · no cost or slippage model · no feature
selection · no target chosen because it scored well.
