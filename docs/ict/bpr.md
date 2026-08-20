# Balanced Price Range (BPR)

**Story:** R2-05.2 · **Module:** [`ict_kronos/ict/bpr.py`](../../ict_kronos/ict/bpr.py)

## 1. Definition

A **Balanced Price Range** is the price band where a bullish FVG and a bearish FVG
**overlap** — a zone through which price has been delivered inefficiently in both
directions.

## 2. Inputs

`FvgDetector` output. Nothing else. This module contains no gap detection and no
midpoint construction.

## 3. Exact deterministic rule

For every pair of confirmed gaps of **opposite** polarity:

```
bullish FVG = [A, B]      bearish FVG = [C, D]

if  max(A, C) < min(B, D):
        BPR.low  = max(A, C)
        BPR.high = min(B, D)
else:
        no BPR
```

The zone is the **intersection**, never the union.

- **Strictly positive overlap required.** Two gaps touching at exactly one price share
  no range; a zero-width BPR is refused at construction, mirroring R2-05's rule that
  equality is not a gap. `min_overlap_points` raises the bar further.
- **Same-polarity pairs are never BPRs.**
- **Adjacency**: the two gaps must confirm within `max_bars_between` bars (default 100).
- **Cardinality**: one BPR per qualifying **pair**. Three mutually overlapping gaps
  yield every qualifying opposite-polarity pair, each with its own id. No merging, no
  "best" selection.

**Direction** follows `polarity`:

| | Meaning |
|---|---|
| **`LATER_FVG`** *(default, the project convention)* | the polarity of whichever gap confirmed second |
| `NEUTRAL` | no direction claimed |

## 4. Event timestamp

The **later** source gap's `formation_timestamp` — where the completed relationship
sits on the chart.

## 5. Confirmation timestamp

```
max(bullish_fvg.confirmation_timestamp, bearish_fvg.confirmation_timestamp)
```

A BPR is not knowable until **both** gaps are. Publishing at the earlier gap's
confirmation is the classic composite leak; `composite_confirmation` makes it
unexpressible locally.

## 6. Provenance

```
source_fvg_ids   -> (bullish_fvg_id, bearish_fvg_id), both resolving in the same analysis
bullish_fvg_id / bearish_fvg_id  -> named individually so polarity is unambiguous
bars_between     -> the confirmation distance between the two gaps
```

## 7. Observability

Shared gate only. Both sources are provably observable no later than the BPR —
asserted mechanically by `assert_sources_observable_first`.

## 8. Leakage risks

| Risk | Mitigation |
|---|---|
| **Emitting at the earlier gap's confirmation** | `composite_confirmation` takes the max; the naive version is tested and proven early |
| Pairing arbitrarily distant gaps | `max_bars_between` |
| Counting a touch as an overlap | Strictly positive overlap required |
| Collapsing several valid pairs into one | Explicitly forbidden; each pair has its own id |

## 9. Examples

```
bullish FVG [1.0050, 1.0100]      confirmed 09:15
bearish FVG [1.0070, 1.0105]      confirmed 09:30

intersection = [max(1.0050, 1.0070), min(1.0100, 1.0105)] = [1.0070, 1.0100]
```

→ BPR `[1.0070, 1.0100]`, overlap 300 points, direction **bearish** (the later gap),
confirmation **09:30** — not 09:15.

Two gaps at `[1.0050, 1.0070]` and `[1.0070, 1.0090]` touch at `1.0070`: **no BPR**.

## 10. Known ambiguity

- **Direction has no computable rule in the source**, which describes BPRs as
  directional by context. "Polarity of the later gap" is a deterministic **proxy** and
  is the weakest default in this story; `NEUTRAL` is arguably the more honest reading
  since the zone is by construction two-sided, and is available as configuration.
- **Adjacency is unspecified** in the source; `max_bars_between` is an engineering bound.

## 11. Explicit non-goals

No Unicorn (later), no interaction modelling with IFVG, no signals, no scoring, no
cross-timeframe BPR, no ML.
