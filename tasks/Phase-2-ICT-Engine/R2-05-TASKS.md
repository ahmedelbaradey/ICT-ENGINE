# R2-05 — Fair Value Gap — tasks

Story: [R2-05](../../user-stories/Phase-2-ICT-Engine/R2-05-fair-value-gap.md)

| ID | Task | Notes | Status |
|---|---|---|---|
| R2-05-1 | Read the legacy ForexQuant FVG detector as a NEGATIVE reference | Bug characterised in `docs/ict/fvg.md` §0 | ✅ |
| R2-05-2 | **Document semantics BEFORE code** — `docs/ict/fvg.md` | Written first | ✅ |
| R2-05-3 | `FvgZone` with `formation_timestamp` + `confirmation_timestamp` | Two required fields; legacy has one | ✅ |
| R2-05-4 | Confirmation = C3's `close_time`, derived, never assignable early | Off-by-one impossible by construction | ✅ |
| R2-05-5 | Bullish/bearish detection, wick vs body measure | Wick default | ✅ |
| R2-05-6 | `min_gap_points`, strict boundary equality | Configurable | ✅ |
| R2-05-7 | **Contiguity guard** — no phantom FVGs across weekends/data gaps | Default on | ✅ |
| R2-05-8 | Fill: touch=0%, partial, full; thresholds configurable | Partial stays usable | ✅ |
| R2-05-9 | Immutable zones + timestamped `FvgFillUpdate` stream | R2-04's level/sweep separation | ✅ |
| R2-05-10 | Invalidation == full mitigation, stated plainly | Exposed under both names | ✅ |
| R2-05-11 | Optional displacement filter, off by default | No R2-03 dependency | ✅ |
| R2-05-12 | Shared observability gate only — no hand-rolled comparison | Source-level test | ✅ |
| R2-05-13 | Config wiring — `FvgDetectionConfig`, env-overridable | CLAUDE.md rule 4 | ✅ |
| R2-05-14 | Naive `reference_zones()` + equivalence tests on real data | | ✅ |
| R2-05-15 | **The off-by-one test** — no FVG returnable at candle N | Mandatory | ✅ |
| R2-05-16 | **Adversarial leakage set** (6 required cases) | Mandatory | ✅ |
| R2-05-17 | **Batch vs prefix vs bar-by-bar replay** | | ✅ |
| R2-05-18 | **Real data** — EURUSD + XAUUSD on 1M/5M/15M/1H/4H | 1D/1W recorded as a dataset limit | ✅ |
