# Inversion Fair Value Gap (IFVG)

**Story:** R2-05.2 · **Module:** [`ict_kronos/ict/ifvg.py`](../../ict_kronos/ict/ifvg.py)

## 1. Definition

An **Inversion Fair Value Gap** is a confirmed FVG that price has broken through
against its original delivery, flipping its polarity.

> **An IFVG is not FVG mitigation.** A gap can be filled — even 100% filled — without
> inverting. Mitigation is measured from bar **extremes**; inversion requires a bar
> **close** beyond the zone. They are different questions about the same zone and both
> stay answerable.

## 2. Inputs

`FvgDetector` output plus the bar series. **No gap detection happens in this module** —
`FvgDetector` is the only thing in this codebase that decides whether three candles
contain an imbalance.

## 3. Exact deterministic rule

For each confirmed `FvgZone`, scan bars whose `timestamp >= zone.confirmation_timestamp`
(so C3 cannot invert the gap it defines) up to `max_bars_to_invert`:

| Trigger | Bullish source gap inverts when | Bearish source gap inverts when |
|---|---|---|
| **`CLOSE_THROUGH_FAR_EDGE`** *(default)* | `close < zone.bottom` | `close > zone.top` |
| `CLOSE_INSIDE_ZONE` | `close < zone.top` | `close > zone.bottom` |
| `WICK_THROUGH` | `low < zone.bottom` | `high > zone.top` |

The first qualifying bar inverts the gap. **One inversion per gap, terminal** — an
IFVG never flips back into an FVG.

- **Polarity flips**: bullish gap → bearish IFVG, and the mirror.
- **Geometry is inherited unchanged** from the source gap.
- **Lifecycle**: `ACTIVE` → `PARTIALLY_FILLED` → `MITIGATED`, measured against the
  *inverted* polarity from the inverting bar onward.

`WICK_THROUGH` exists only so the leakage suite can run the naive implementation beside
the causal one. It is not a recommended setting.

## 4. Event timestamp

The **inverting bar's open** — where the flip sits on the chart.

## 5. Confirmation timestamp

The **inverting bar's `close_time`**. The trigger reads a close, so the flip is not
knowable until that bar closes. Lag is exactly one bar duration.

## 6. Provenance

```
source_fvg_id            -> resolves to an FvgZone in the same analysis
source_fvg_confirmation  -> carried so the composite invariant is checkable
                            from the record alone
```

Geometry is inherited but identity always points back; the zone is never re-derived.

## 7. Observability

Through the shared gate only — `is_observable_at` / `filter_observable` /
`assert_observable`. `IfvgZone.is_observable_at` delegates to the contract predicate.
A source-level test fails the build if a raw comparison is reintroduced.

Composite invariant, enforced by `composite_confirmation`:

```
ifvg.confirmation_timestamp >= source_fvg.confirmation_timestamp    (strictly later)
```

## 8. Leakage risks

| Risk | Mitigation |
|---|---|
| Marking inversion at a **wick** rather than a close | Default trigger reads `close`; the wick reading is a separate, tested, non-default mode |
| Treating full mitigation as inversion | Explicitly separated; `mitigated_without_inversion` records the difference |
| Publishing before C3 closed | Scan starts at `zone.confirmation_timestamp` |
| Unbounded hindsight pairing | `max_bars_to_invert` (default 500) |

**L4 naive divergence** is proven twice: the wick trigger reports inversions the causal
one does not, and when both fire it fires *earlier*.

## 9. Examples

A bullish gap `[1.0050, 1.0100]`:

| Following bar | Outcome |
|---|---|
| low 1.0045, **close 1.0120** | gap fully filled by the wick → `MITIGATED`, **no IFVG** |
| low 1.0070, **close 1.0075** | closed inside the zone → **no IFVG** under the default |
| low 1.0040, **close 1.0045** | closed beyond the far edge → **bearish IFVG** `[1.0050, 1.0100]` |

## 10. Known ambiguity

The primary source says only *"price closes beyond a Fair Value Gap"*. "Beyond" admits
the far-edge and the near-edge readings; we implement the **far-edge** reading by
default because it is the strictest and keeps inversion clearly distinct from
mitigation, and expose the looser one as configuration. This is an **engineering
choice**, not a quotation.

## 11. Explicit non-goals

Not implemented here: BPR, Breaker, Unicorn, any signal or entry, any scoring, any
cross-timeframe projection, any ML.
