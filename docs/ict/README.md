# ICT engine — detector documentation

One document per detector, each covering: definition, algorithmic rule, input, output,
event timestamp, confirmation timestamp, edge cases, known ambiguities, test coverage,
and known limitations.

**Where an ICT concept has several readings in the trading community, we state the one
we implement, make it configurable, and list the alternatives.** No interpretation is
adopted silently.

| Detector | Story | Doc | Status |
|---|---|---|---|
| SessionDetector | [R2-01](../../user-stories/Phase-2-ICT-Engine/R2-01-session-detector.md) | [sessions.md](sessions.md) | ✅ Done |
| SwingDetector | [R2-02](../../user-stories/Phase-2-ICT-Engine/R2-02-swing-detection.md) | [swings.md](swings.md) | ✅ Done |
| StructureDetector | [R2-03](../../user-stories/Phase-2-ICT-Engine/R2-03-market-structure.md) | [structure.md](structure.md) | ✅ Done |
| LiquidityDetector | [R2-04](../../user-stories/Phase-2-ICT-Engine/R2-04-liquidity.md) | [liquidity.md](liquidity.md) | ✅ Done |
| FVGDetector | R2-05 | — | ⬜ **Next** |
| PremiumDiscountCalculator | R2-06 | — | ⬜ Not started |
| ICTFeatureVector | R2-07 | — | ⬜ Not started |

## The shared contract

Every detector emits `IctEvent` ([`ict/contract.py`](../../ict_kronos/ict/contract.py)):

```
symbol  timeframe  event_type  direction
event_timestamp  confirmation_timestamp
price_level  reference_level  strength
```

plus, where applicable: `created_timestamp`, `invalidation_timestamp`,
`distance_from_price`, `age`, `status`.

**The rule that matters:**

> `confirmation_timestamp` is the earliest timestamp at which the event could have been
> known using ONLY information available at that time.

It is *not* where the event sits on a chart — that is `event_timestamp`. The constructor
refuses any event whose confirmation precedes its occurrence, so the leak cannot even be
expressed.

## Batch vs streaming

Every detector must satisfy `batch(history) == replay(bar-by-bar)`. The same engine
serves historical research, backtesting and live inference; if those three disagree,
the research is measuring something that could never have been traded.
