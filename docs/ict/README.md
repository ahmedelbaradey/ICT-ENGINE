# ICT engine — detector documentation

> **Phase 2 composite work:** the eight concepts R2-05.2 … R2-05.9 are specified in
> [R2-05x-CONCEPT-MAP.md](R2-05x-CONCEPT-MAP.md) — dependency graph, shared schema,
> leakage criteria and the ambiguity register. Read it before starting any of them.

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
| FVGDetector | [R2-05](../../user-stories/Phase-2-ICT-Engine/R2-05-fair-value-gap.md) | [fvg.md](fvg.md) | ✅ Done |
| TrueDailyOpenDetector | [R2-05.1](../../user-stories/Phase-2-ICT-Engine/R2-05.1-true-daily-open.md) | [true_daily_open.md](true_daily_open.md) | ✅ Done |
| IfvgDetector | [R2-05.2](../../user-stories/Phase-2-ICT-Engine/R2-05.2-inversion-fair-value-gap.md) | — | 📋 Spec |
| OrderBlockDetector | [R2-05.3](../../user-stories/Phase-2-ICT-Engine/R2-05.3-order-block.md) | — | 📋 Spec |
| BreakerDetector | [R2-05.4](../../user-stories/Phase-2-ICT-Engine/R2-05.4-breaker-block.md) | — | 📋 Spec |
| BprDetector | [R2-05.5](../../user-stories/Phase-2-ICT-Engine/R2-05.5-balanced-price-range.md) | — | 📋 Spec |
| RdrbDetector | [R2-05.6](../../user-stories/Phase-2-ICT-Engine/R2-05.6-rdrb.md) | — | 📋 Spec ⚠ |
| CisdDetector | [R2-05.7](../../user-stories/Phase-2-ICT-Engine/R2-05.7-cisd.md) | — | 📋 Spec |
| CHoCH revision | [R2-05.8](../../user-stories/Phase-2-ICT-Engine/R2-05.8-choch-revision.md) | [structure.md](structure.md) | 📋 Spec |
| UnicornDetector | [R2-05.9](../../user-stories/Phase-2-ICT-Engine/R2-05.9-unicorn-model.md) | — | 📋 Spec |
| PremiumDiscountCalculator | R2-06 | — | ⛔ Deferred |
| ICTFeatureVector | R2-07 | — | ⛔ Deferred |

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
