# Order Block

**Story:** R2-05.2 · **Module:** [`ict_kronos/ict/order_blocks.py`](../../ict_kronos/ict/order_blocks.py)

## 1. Definition — the definition of record for this engine

> An **Order Block** is the last candle, or contiguous group of candles, in the
> opposing direction whose range is subsequently **closed through** by the directional
> move.

```
bullish OB   last down-close candle/group,  then a later bar CLOSES ABOVE its high
bearish OB   last up-close   candle/group,  then a later bar CLOSES BELOW its low
```

Two consequences are load-bearing:

- **An Order Block does not require a Fair Value Gap.** OB formation and FVG formation
  are different events. `require_fvg` is an explicit opt-in qualifier, **off** by
  default; a coincident FVG is recorded as `related_fvg_id` — confluence, never a
  precondition.
- **An Order Block is not observable when its candidate closes.** It becomes an Order
  Block only when the directional close through its range happens, typically several
  bars later.

Deliberately **rejected** as insufficient: "the last opposite candle before any move",
"the last opposite candle before an FVG", "every large candle", "every engulfing
candle". None of them has a well-defined confirmation instant.

## 2. Inputs

Bars. Optionally `FvgDetector` (confluence annotation, or a qualifier when opted in)
and `StructureDetector`/`LiquidityDetector` provenance. No upstream concept is
re-derived.

## 3. Exact deterministic rule

**Step 1 — candidate runs.** A candle is *down-close* when `close < open` and
*up-close* when `close > open`. A **doji** (`close == open`) is neither: it belongs to
no run and terminates the one in progress. Runs are maximal and contiguous; with
`require_contiguous_bars` a time gap ends the run.

**Step 2 — the block.**

| `grouping` | Members |
|---|---|
| **`MULTI_CANDLE_GROUP`** *(default)* | the whole maximal run |
| `SINGLE_CANDLE` | only the run's final candle |

**Step 3 — the zone.**

| `geometry` | `zone_top` / `zone_bottom` |
|---|---|
| **`FULL_RANGE`** *(default)* | `max(high)` / `min(low)` across the members |
| `BODY` | `max(open, close)` / `min(open, close)` across the members |

`mean_threshold` (the 50% level) is exposed as a property so downstream never
re-derives it.

**Step 4 — confirmation.** Within `max_bars_to_confirm` bars after the run, find the
first bar whose **close** is beyond the block's relevant boundary:

```
bullish:  close > zone_top     + break_tolerance
bearish:  close < zone_bottom  - break_tolerance
```

Strict — a close exactly at the boundary is not a break, and a **wick** through it
confirms nothing. **A candidate never closed through is not an Order Block.** Not a
pending one, not a weak one: it does not exist.

**Optional qualifiers**, both off by default: `require_displacement` (the identical
ratio definition R2-03 and R2-05 use) and `require_fvg`.

## 4. Event timestamp

The **first candle of the group's open** — the candidate's own location on the chart.

## 5. Confirmation timestamp

The **`close_time` of the bar whose close broke through**. This is normally several bar
durations after the event timestamp, and that gap is the entire reason this detector is
non-trivial.

## 6. Provenance

```
source_candle_timestamps  -> every candle forming the block, in time order
break_bar_timestamp       -> the bar that confirmed it
break_close               -> the close that did it
related_fvg_id            -> confluence only; None is normal and valid
displacement_ratio        -> recorded when the displacement qualifier is on
```

Positional dataframe indexes are **not stored** — they cannot become join keys.

## 7. Observability

Shared gate only. Composite invariant: confirmation is strictly later than the event,
and later than any source event's confirmation.

## 8. Leakage risks

| Risk | Mitigation |
|---|---|
| **Stamping the OB at its own candle's close** | Confirmation is the breaking bar's `close_time`; the naive version is implemented in tests and proven to publish every block early |
| Wick-based confirmation | Break is a `close`, strictly beyond the boundary |
| Unbounded hindsight | `max_bars_to_confirm` (default 50) |
| Group boundaries shifting as data arrives | Runs are terminated by an opposing candle *within* the data; a run still open at the end of a prefix emits nothing |

## 9. Examples

```
doji                                     belongs to no run
open 1.0052 high 1.0060 low 1.0030 close 1.0035    <- candidate (down-close)
open 1.0035 high 1.0075 low 1.0034 close 1.0070    <- closes 1.0070 > 1.0060: CONFIRMS
```

→ bullish Order Block, zone `[1.0030, 1.0060]`, mean threshold `1.0045`, event at the
candidate's open, confirmation at the third bar's close.

Same sequence but the third bar closing at `1.0058` (having wicked to `1.0075`):
**no Order Block**.

## 10. Known ambiguity

- **Zone geometry** is contested — *"the body of that candle becomes your OB zone"*
  against the common full-range reading. `FULL_RANGE` is the default because the wider
  zone is touched sooner, so it errs toward declaring a block *spent* rather than still
  live.
- **The source is vague on qualifiers**, mentioning displacement, an engulf, a
  lower-timeframe MSS and an FVG without ranking them. This engine takes the
  close-through as the *definition* and every other condition as an explicit,
  documented, default-off qualifier.
- `max_bars_to_confirm` is an engineering bound with no basis in the source material.

## 11. Explicit non-goals

No Breaker logic (R2-05.2's `breakers.py`), no Mitigation/Rejection/Propulsion/Hidden
block variants, no ranking or quality score, no signals, no ML.
