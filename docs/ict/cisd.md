# CISD — Change In State of Delivery

**Story:** R2-05.2 · **Module:** [`ict_kronos/ict/cisd.py`](../../ict_kronos/ict/cisd.py)

## 1. Definition

Price is modelled as being **delivered** in one direction at a time. A run of
consecutive same-direction candles is one *delivery leg*. Delivery changes state when a
candle **body closes** through the **opening price of that leg**.

```
bullish CISD   a close ABOVE the opening price of the preceding bearish leg
bearish CISD   a close BELOW the opening price of the preceding bullish leg
```

> **Ignore the wicks.** Only opens and closes matter. A wick through the trigger level
> is not a CISD.

## 2. Inputs

Bars only. `LiquidityDetector` is consulted **only** if a prior-sweep requirement is
enabled. **`StructureDetector` is never consulted** — a source-level test asserts this
module does not import it.

## 3. Exact deterministic rule

**Step 1 — delivery legs.** Maximal contiguous runs of same-close-direction candles.
`close > open` is bullish, `close < open` is bearish, and a **doji** (`close == open`)
belongs to no leg and terminates the one in progress.

**Step 2 — trigger level**, by `anchor`:

| | Level |
|---|---|
| **`SERIES_OPEN`** *(default)* | the open of the leg's **first** candle |
| `EXTREME_OPEN` | the highest open in a bearish leg / lowest in a bullish one |

**Step 3 — the transition.** Within `max_bars_to_trigger` bars after the leg, the first
candle whose **close** clears the level:

```
bullish:  close > level + trigger_tolerance
bearish:  close < level - trigger_tolerance
```

Strict — a close exactly at the level is not a transition.

**Step 4 — the state machine.** Candidates are collected first and applied strictly in
**trigger order**, not leg order: a short later leg can trigger before a long earlier
one resolves, and walking the machine in leg order would make batch disagree with
replay. A transition into the state already in force is not a change of state.

## 4. Event timestamp

The **crossing bar's open**.

## 5. Confirmation timestamp

The **crossing bar's `close_time`**. Exactly one bar of lag.

## 6. Provenance

```
leg_start_timestamp / leg_end_timestamp / leg_length   -> the delivery leg acted against
trigger_level                                          -> the leg's opening price
trigger_close                                          -> the close that cleared it
previous_state -> resulting_state                      -> the transition itself
```

Records are immutable. `CisdAnalysis.state_at(t)` derives the timeline from observable
transitions only; a later CISD supersedes an earlier state without rewriting anything.

## 7. Observability

Shared gate only. Everything used is available at the signal candle's close: the leg is
built from candles at or before the trigger, and no future pivot, break or gap is
consulted. CISD is never classified retroactively.

## 8. CISD is not MSS

|  | reads | confirms |
|---|---|---|
| **CISD** | opens and closes | **earlier** — on a candle close |
| **BOS / MSS** | swing levels | **later** — on a structural break |

Both may fire on the same bar; neither implies the other. A real-data test asserts the
two produce different confirmation sets, because divergence is the evidence that they
are separate concepts rather than one concept under two names.

## 9. Leakage risks

| Risk | Mitigation |
|---|---|
| **Anchoring to an extreme open found with hindsight** | `SERIES_OPEN` default; `EXTREME_OPEN` is opt-in and documented as hindsight-prone |
| Triggering on a **wick** | Bodies only, tested with a wick-through fixture that must not fire |
| A later candle extending the leg and rewriting a published CISD | The leg is fixed at the trigger bar; extension changes nothing already emitted |
| State machine order | Transitions applied in trigger order, not leg order |

## 10. Examples

```
o 1.0100 h 1.0105 l 1.0080 c 1.0085    leg candle 1 — SERIES OPEN = 1.0100
o 1.0085 h 1.0090 l 1.0060 c 1.0065    leg candle 2
o 1.0065 h 1.0070 l 1.0040 c 1.0045    leg candle 3
o 1.0045 h 1.0120 l 1.0044 c 1.0115    close 1.0115 > 1.0100  -> BULLISH CISD
```

| Final-bar variant | Result |
|---|---|
| high 1.0120, **close 1.0095** | **no CISD** — a wick above the level is not a close through it |
| **close 1.0100** exactly | **no CISD** — equality is not a transition |
| **close 1.0115** | **bullish CISD**, trigger level 1.0100, confirmation at that bar's close |

## 11. Known ambiguity

- **Which open of the leg** is the trigger. The source says *"the opening price of the
  final consecutive series of those down-closing candles"*, which we read as the
  series' opening price — the **first** candle's open. `EXTREME_OPEN` is the
  alternative reading, available and not default.
- **A prior liquidity sweep is not required** — *"CISD alone is just a candle close"* —
  so no sweep requirement is imposed by default.
- `min_leg_length` defaults to **1**: a single down-close candle is a (short) bearish
  delivery leg. This produces frequent CISDs on fast timeframes; counts are reported
  rather than filtered to look tidy.

## 12. Explicit non-goals

No structural detection, no swing derivation, no signals, no entries, no scoring, no
higher-timeframe delivery context, no ML.
