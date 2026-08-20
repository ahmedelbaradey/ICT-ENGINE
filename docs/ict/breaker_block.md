# Breaker Block

**Story:** R2-05.2 · **Module:** [`ict_kronos/ict/breakers.py`](../../ict_kronos/ict/breakers.py)

## 1. Definition

A **Breaker Block** is an Order Block that **failed** — price closed beyond it against
its direction — and whose zone consequently flips polarity.

```
bullish OB, closed through downward   ->  BEARISH breaker
bearish OB, closed through upward     ->  BULLISH breaker
```

A Breaker is **never** an independent candle pattern. It exists only as a transition of
a confirmed `OrderBlock`.

## 2. Inputs

`OrderBlockDetector` output, the bar series, and — when the structure condition is
enabled — `StructureDetector` output. No pattern detection happens in this module.

## 3. Exact deterministic rule

**Step 1 — failure.** The first bar whose **close** is beyond the source block's far
edge, against the block's direction:

```
bullish OB fails when   close < zone_bottom
bearish OB fails when   close > zone_top
```

`break_mode` defaults to **`CLOSE`**. `WICK` exists so the leakage suite can run the
naive implementation; it is not recommended, for R2-03's reason — wick breaks fire on
every stop-run.

**Step 2 — the structure condition.** `require_structure_break` defaults to **`True`**:
a confirmed R2-03 break in the *breaker's* direction must fall within
`structure_window_bars` of the failure. **Not every broken Order Block is a Breaker.**
Blocks that fail without one are recorded in `failed_without_structure`, not silently
dropped.

**Step 3 — the zone.** Inherited unchanged from the source Order Block, including
whichever geometry convention that block was built with.

**Retest is not confirmation.** A retest is a lifecycle event on an already-confirmed
Breaker, recorded in the update stream. Treating it as a precondition would defer the
event to an arbitrary later instant and make it unobservable in replay.

## 4. Event timestamp

The **failing bar's open** (`failure_timestamp`).

## 5. Confirmation timestamp

```
max( source_order_block.confirmation_timestamp,
     failing bar's close_time,
     structure break's confirmation_timestamp   (when required) )
```

Computed by `composite_confirmation`, never locally.

## 6. Provenance

```
source_order_block_id            -> resolves to an OrderBlock in the same analysis
source_order_block_confirmation  -> carried for the composite invariant
source_break_id                  -> the R2-03 break, when the gate is on
failure_timestamp                -> the bar whose close failed the block
```

The source Order Block is **never mutated**. The Breaker is a new event.

## 7. Observability

Shared gate only. A Breaker observable before its source Order Block is a
contradiction and is impossible to construct.

## 8. Leakage risks

| Risk | Mitigation |
|---|---|
| **Flipping polarity at the wick that pierces the block** | Failure is a `close`; `WICK` mode is implemented in tests and proven to fire earlier and more often |
| Promoting every broken block | Structure condition on by default |
| Inheriting an unconfirmed block | Confirmation takes the max including the source's |
| Retest treated as confirmation | Explicitly a lifecycle event |

## 9. Examples

```
... bullish Order Block confirmed, zone [1.0030, 1.0060] ...
open 1.0058 high 1.0059 low 1.0020 close 1.0055   -> wick below 1.0030, close above: NO failure
open 1.0055 high 1.0056 low 1.0018 close 1.0022   -> close 1.0022 < 1.0030: FAILS
```

→ bearish Breaker, zone `[1.0030, 1.0060]`, event at the failing bar's open,
confirmation at its close (plus the structure break when the gate is on).

## 10. Known ambiguity

Two readings exist. We implement the **failed-OB** reading — *"If price closed past the
OB extreme, the OB has failed — that is a Breaker Block, not a Mitigation"* — because
it composes directly with `OrderBlockDetector`. The **classic swing construction**
(the candle at a swept swing, confirmed when the opposing swing breaks) is documented
here and **not implemented**.

`structure_window_bars` is an engineering bound with no basis in the source.

## 11. Explicit non-goals

**Mitigation Block is deliberately not implemented.** It is the complement of a Breaker
under this reading — same level, block *held* instead of failing — and its absence is a
recorded decision, not an oversight. Also excluded: Rejection blocks, the Unicorn model
(later), signals, scoring, ML.
