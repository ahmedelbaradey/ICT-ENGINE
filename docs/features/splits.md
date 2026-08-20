# Chronological splitting and the target-window embargo

**Story:** R2-08 · **Module:** [`ict_kronos/features/splits.py`](../../ict_kronos/features/splits.py)
· **Dataset:** [dataset.md](dataset.md) · **Targets:** [targets.md](targets.md)

## 1. No shuffling, ever

A financial time series is never shuffled (CLAUDE.md rule 6). There is no random split
here and no way to ask for one — a guard test asserts the module contains no
`train_test_split`, `shuffle`, `random` or `sample(`.

That much is obvious. The part that is not obvious is §3.

## 2. Boundaries

Two boundaries, both **exclusive upper bounds**:

```
as_of <  train_end                        ->  TRAIN
train_end      <= as_of < validation_end  ->  VALIDATION
validation_end <= as_of                   ->  TEST
```

Stated once, here, so no caller has to guess which side an exact boundary hit falls on.
TEST is open-ended and has no end.

Boundaries must be timezone-aware UTC and strictly ordered; anything else is refused at
construction rather than producing a silently empty split.

`SplitSpec.by_proportion` derives boundaries from proportions of the **ordered instants**,
not of the clock — so a weekend does not hand one split more rows than the proportion
asked for. TEST is the remainder rather than a third parameter, so the three can never
fail to sum to one.

## 3. The embargo — the leak that leaves no trace

> **A row's features end at `as_of`; its target ends `H` bars later.**

So a row a few bars before the train/validation boundary has a target that was resolved
by bars belonging to validation. Training on it leaks the validation period into the
model — with no exception, no NaN and no warning, and it inflates the very validation
score used to decide the experiment worked.

The fix: rows whose future window crosses their split's end are labelled `EMBARGOED` and
excluded from every split.

```
TRAIN ........................|EMBARGO|  VALIDATION ..............|EMBARGO|  TEST ......
                              ^                                   ^
                        train_end                          validation_end
```

Three properties make this trustworthy rather than decorative:

1. **It cannot be under-specified.** `DatasetSpec` refuses a split whose `embargo_bars`
   is shorter than its longest target horizon, and names the value to use.
   `DatasetSpec.with_split(...)` derives it. Raising is the only option that neither
   leaks nor silently overrides what the caller asked for.
2. **It is measured, not assumed.** When the actual target-window ends are supplied — as
   `DatasetBuilder` always does — a row is embargoed because its window *demonstrably*
   crosses the boundary, not because it sits near one. Without them the rule falls back
   to counting `embargo_bars` positions, which is the same rule expressed in bars.
3. **Nothing is dropped silently.** An embargoed row keeps its `natural_label`, so the
   plan can always answer "what was withheld, and from where".

**A row whose horizon runs off the end of the data is not embargoed.** Its target is
*unresolved*, not leaking — a different problem, and conflating them would withhold
rows for no reason.

**TEST rows are never embargoed** because nothing follows them.

**`embargo_bars = 0` disables the embargo entirely.** That is correct only for a dataset
with no forward-looking target at all, and `DatasetSpec` will refuse the combination in
every other case.

## 4. Auditability

`SplitPlan.boundaries()` reports the count, first and last instant of each split, plus
the serialised specification. `SplitPlan.as_dict()` serialises every individual
assignment. The partition is checkable by eye and by test: every instant assigned exactly
once, no instant in two splits, and `max(train) < min(validation) < min(test)`.

## 5. Known ambiguities

| Element | Classification |
|---|---|
| Exclusive-upper-bound boundaries | **Engineering decision** (§2). The alternative is equally valid and equally arbitrary; what matters is that it is stated |
| Default 0.6 / 0.2 / 0.2 proportions | **Engineering convenience.** Not a claim about how much data a model needs |
| Embargo on the *left* of a boundary only | **Deliberate** (§3). The contamination is forward-looking, so the protection is too. A symmetric purge would also be defensible and is not implemented, because nothing here has yet demonstrated it is needed |
| Walk-forward / expanding-window validation | **Not implemented.** A single train/validation/test partition is what R2-08 needs. Expanding-window validation is named in CLAUDE.md rule 6 and belongs to the phase that actually runs experiments |

## 6. Explicit non-goals

No random splitting · no stratification · no class balancing · no cross-validation
scheme · no walk-forward driver · no model training of any kind.
