# The dataset row — schema, provenance, leakage contract and the quality audit

**Story:** R2-08 · **Modules:** [`dataset.py`](../../ict_kronos/features/dataset.py) ·
[`audit.py`](../../ict_kronos/features/audit.py)
· **Targets:** [targets.md](targets.md) · **Splits:** [splits.md](splits.md)

## 1. What a row is

One instant, three answers:

```
row.features            what was knowable at as_of      (R2-07, point-in-time)
row.targets             what happened next              (R2-08, future-dependent)
row.feature_provenance  where the features came from    (R2-07's ids, verbatim)
```

The two halves are built under **opposite** temporal rules, and the join is the only
place they meet. The builder hands the feature layer a frame and an instant, hands the
target engine the same frame and the same instant, and puts the answers side by side.
No value crosses. The builder itself contains no formula, no threshold and no lifecycle —
putting logic there would put it on both sides of the temporal boundary at once.

## 2. Row schema

| Field | Type | Notes |
|---|---|---|
| `symbol` | `str` | e.g. `EURUSD` |
| `timeframe` | `str` | e.g. `15m` |
| `as_of` | `datetime` | tz-aware UTC; always a **bar close** |
| `features` | `ICTFeatureVector` | 56 features, `FEATURE_NAMES` is the column order |
| `targets` | `tuple[TargetValue, ...]` | one per specification, in specification order |
| `feature_provenance` | `dict[str, tuple[str, ...]]` | `ICTMarketState.source_ids()` unchanged |
| `split` | `SplitLabel \| None` | `None` when no split was requested |
| `dataset_schema_version` | `str` | `r2-08.1` |
| `feature_schema_version` | `str` | `r2-07.1` |
| `target_schema_version` | `str` | `r2-08.1` |

Three versions rather than one, because the three layers move independently. A result can
always be traced to the definitions in force when it was built, not to whatever the code
says today.

**A row exists only at a bar close.** R2-07 refuses to invent a state between closes, and
this layer does not invent a row either — asking for an instant that is not a close
produces no row rather than an interpolated one.

## 3. Reproducibility

A dataset is rebuildable from `(symbol, timeframe, DatasetSpec, source bars)` and from
nothing else. `DatasetSpec` deliberately holds no data — only target specifications, the
split specification, and its own version. That is the difference between an experiment
that can be re-run and one that can only be believed.

## 4. The leakage contract

The property, stated so it can be tested rather than asserted:

```
truncate every bar after as_of      ->  features IDENTICAL,  targets may become unresolved
mutate every bar after as_of        ->  features IDENTICAL,  targets MUST move
mutate only future highs/lows       ->  features IDENTICAL,  excursion targets MUST move
mutate history before as_of         ->  features MUST change              <- the control
```

The last line is the one that makes the other three mean anything. Without it, every
inertness assertion would pass just as happily against a layer that computed nothing at
all.

The wick-only case is sharper than it looks: a mutation to future highs and lows must
move an `EXCURSION` target while leaving a close-to-close `FUTURE_RETURN` **unchanged**.
Both halves are asserted, because a layer that responded to everything would be as wrong
as one that responded to nothing.

## 5. Point-in-time equivalence

```
batch features(T)  ==  prefix-replay features(T)
```

for every instant, inherited from R2-07 along with its **one** documented exception: the
True Daily Open is the engine's only zero-lag event, so a prefix ending at `t` cannot
contain the bar that *opened* at `t`. See [market_state.md §10a](../ict/market_state.md).
R2-08 introduces no new exception.

Targets are **exempt by definition** — they are future-dependent, and a prefix legitimately
resolves fewer of them. The direction still matters and is tested: a prefix may resolve
*less*, never more.

## 6. Provenance

`feature_provenance` is `ICTMarketState.source_ids()` carried through **unchanged** — ids,
never geometry, never a price, never a nearest-match. Reused rather than recomputed, so
feature provenance has exactly one definition in the repository.

Target provenance lives on the value itself: `reference_price`, `reference_timestamp`,
`future_window_start`, `future_window_end`, and — for a resolved barrier race —
`resolving_bar_timestamp`. Enough to explain an answer without recomputing it.

## 7. The flat frame

`rows_to_frame(rows, target_name=...)` produces a fixed column order:

```
identity (7)  ->  FEATURE_NAMES (56, in order)  ->  target columns (11)
```

The order is defined once here so every consumer gets the same frame rather than each
assembling its own. As in R2-07, **the column order is the schema**.

## 8. The quality audit

`audit_rows` / `audit_dataset` **describe** a dataset. They do not choose features, drop
columns, impute anything, or rank anything by usefulness.

That line is the point. Feature selection driven by outcome is a modelling decision; it
belongs in Phase 4 where it can be ablated against a baseline, and doing it here — before
a single result exists — would bake an unmeasured hypothesis into the data itself. A
guard test asserts the module contains no `importance`, `correlation`, `select`, `drop`,
`fillna` or `impute`.

Reported per feature: missing count, NaN count, present count, unique count, min, max,
mean, and whether it is constant.

**A column that is entirely missing is not called constant.** "Always absent" and "always
the same number" are different findings with different causes.

Reported per target: total, resolved, unresolved, coverage, unresolved **by reason**, and
class counts for categorical targets. Also reported: row count, symbol and timeframe
coverage, all three schema versions, first and last `as_of`, duplicate `as_of` count,
whether the rows are chronological, and split counts including `EMBARGOED`.

Everything is sorted, so two runs produce byte-identical output.

## 9. Known limitations

| Limitation | Status |
|---|---|
| Four days of fixture data (2024-03-08 → 03-11) | **Dataset limitation.** Enough for weekend + DST coverage; not enough for a 16-bar horizon on 4H |
| One row costs a full point-in-time state | **Performance observation.** ~11 ms per row on 15m end to end, of which the targets are under 1%; a multi-year 1m dataset needs sampling or a pushed-down loop |
| Rows are held in memory | **Engineering choice.** No Parquet dataset writer yet; the storage layer exists and wiring it is a separate, explicit step |
| One symbol and timeframe per `Dataset` | **Engineering choice.** Cross-symbol pooling is a modelling decision, not a data one |
| No cost, spread or slippage model | **Out of scope.** A target is a market fact; execution is Phase 7 |

## 9a. Where the cost actually is

Measured on the real fixture, so nobody optimises the wrong half:

| Symbol | TF | Bars | Targets | Features | Whole dataset | Per row | Audit |
|---|---|---:|---:|---:|---:|---:|---:|
| EURUSD | 15m | 190 | 9.2 ms | 2171 ms | 2166 ms | 11.4 ms | 6.9 ms |
| EURUSD | 1H | 44 | 3.0 ms | 552 ms | 583 ms | 13.3 ms | 1.4 ms |
| EURUSD | 4H | 9 | 2.8 ms | 170 ms | 151 ms | 16.8 ms | 0.5 ms |
| XAUUSD | 15m | 185 | 7.5 ms | 1994 ms | 1984 ms | 10.7 ms | 5.3 ms |
| XAUUSD | 1H | 44 | 4.1 ms | 367 ms | 354 ms | 8.0 ms | 1.3 ms |
| XAUUSD | 4H | 9 | 2.0 ms | 145 ms | 149 ms | 16.5 ms | 0.5 ms |

**The target engine is under 1% of the cost.** Essentially all of it is R2-07 feature
construction, which is itself dominated by the two detector hotspots already recorded in
[HANDOFF.md](../dev/HANDOFF.md) (`UnicornDetector.analyse`, then IFVG). Nothing in R2-08
is optimised, and nothing in R2-08 is where the time goes.

## 10. Explicit non-goals

No model training · no Kronos · no XGBoost / LightGBM / sklearn / PyTorch · no
hyperparameter search · no backtesting · no execution · no feature selection · no
resampling or class balancing · no imputation.
