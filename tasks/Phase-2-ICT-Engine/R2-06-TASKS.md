# R2-06 — Premium / Discount — tasks

Story: [R2-06](../../user-stories/Phase-2-ICT-Engine/R2-06-premium-discount.md)

**Status: not started.** Tasks are enumerated at story start, per the execution order in
[IMPLEMENTATION_ROADMAP.md](../../docs/financial-ai/IMPLEMENTATION_ROADMAP.md). R2-06 does not
begin until the preceding story is complete and validated.

Every R2 story carries at minimum these obligatory tasks:

| ID | Task |
|---|---|
| R2-06-a | Implementation against the shared detector contract (`ict/contract.py`) |
| R2-06-b | Configuration wiring — no hardcoded trading constants |
| R2-06-c | Unit tests: normal, edge, malformed input, boundary, timeframe, timestamp |
| R2-06-d | **Batch vs streaming-replay equivalence test** |
| R2-06-e | **Leakage tests** — nothing observable before its `confirmation_timestamp` |
| R2-06-f | **Real-data acceptance** — EURUSD + XAUUSD 2024-03-08 → 2024-03-12 |
| R2-06-g | **Documentation** — definition, algorithm, I/O, timestamps, edge cases, ambiguities, coverage |
