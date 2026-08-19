# LEGACY_RESEARCH — prior trading/ML projects on this machine

**Date:** 2026-08-19
**Scope:** Inspect `ForexQuant`, `NNForTrading`, `TradingBot` (and the adjacent `TradingBotV2`) under `e:/Wrokspace/` for anything ICT-Kronos can reuse.
**Rule observed:** read-only. **No legacy directory was modified.** Nothing was copied.

---

## 0. Verdict up front

**No code is recommended for direct reuse.** The prior work is genuinely useful, but as *evidence and cautionary tales* rather than as source:

- **ForexQuant** has real prior ICT work (FVG + RDRB detection) in C#. Its FVG algorithm is textbook-correct and worth mirroring **conceptually**; its event timestamping is the exact look-ahead trap this project was built to prevent, and it has **no tests**.
- **NNForTrading** has a coherent ML pipeline shape — but on **crypto**, and its backtester models **no transaction costs at all**, which Master Plan §21 forbids outright.
- **TradingBot** puts an **LLM in the decision seat** (DeepSeek emits BUY/SELL), which is precisely what CLAUDE.md rule 3 and instruction §17 forbid. It is a worked example of the architecture this project rejects.
- **TradingBotV2** is two prompt files, no code.

The highest-value takeaways are three specific lessons, documented in §5.

---

## 1. ForexQuant — `e:/Wrokspace/ForexQuant`

.NET clean-architecture solution (`ForexQuant.sln`), git repo on `origin https://github.com/ahmedelbaradey/ForexQuant.git`, HEAD `800051c "Implement datafeed"`. Structure: `src/{Core,Infrastructure,Presentation,Tests,apis}`.

| Component | Location | Purpose | Reusable? | Reason | Dependencies | Potential conflicts |
|---|---|---|---|---|---|---|
| `FvgDetectionService` | `src/Infrastructure/.../Service/MarketData/FvgDetectionService.cs` | 3-candle Fair Value Gap detection + mitigation tracking | **Concept only** | The core rule is correct and matches the ICT definition. But it is C#, it timestamps events at candle **open**, and it has no tests | `ILoggerManager`, `Candlestick` entity | Language; timestamp semantics |
| `FairValueGap` entity | `src/Core/Domain/Entities/MarketData/FairValueGap.cs` | FVG record: `TopPrice`, `BottomPrice`, `Type`, `StartTime`, `IsMitigated`, `MitigatedAt` | **Field list only** | A good starting field list, but missing `confirmation_timestamp`, size, age, fill-% and validity — all required by our detector contract | EF Core annotations | Lacks the observability anchor |
| `RdrbDetectionService` | `src/Infrastructure/.../Service/MarketData/RdrbDetectionService.cs` | RDRB (a PD-array concept) detection | **No — deferred** | RDRB is explicitly out of MVP scope (Master Plan §8: prioritise liquidity, MSS/BOS, FVG, OB, P/D, sessions first) | as above | Scope |
| `Candlestick` entity | `src/Core/Domain/Entities/MarketData/Candlestick.cs` | OHLCV + `IsValid()`, `IsBullish`, `BodySize`, `TotalRange`, `IsStale()` | **No** | Our `MarketCandle` already covers this and adds UTC enforcement and `close_time`. `decimal(18,8)` vs our float64 is a deliberate divergence | EF Core | Duplicate concept |
| `FxcmMarketDataService`, `OandaMarketDataService`, `AlphaVantageService`, `MarketDataProviderFactory` | `src/Infrastructure/.../Service/MarketData/` | Broker/vendor data adapters behind a factory | **Pattern already independently adopted** | Our `build_market_data_provider` factory is the same shape. These are C# and target vendors we are not using | FXCM/OANDA SDKs, API keys | None — parallel evolution |
| `Sessions/*` (`SessionManagementService`, `UserSession`, `SessionSettings`) | `src/Core/.../Sessions/`, `src/Infrastructure/.../Sessions/` | **User login sessions** (30-min sliding expiry, Redis) | **No — name collision trap** | Despite the name these are **authentication sessions, not trading sessions**. There is *no* Asian/London/NY logic anywhere in ForexQuant | Redis, ASP.NET Identity | **Actively misleading** — see §5.3 |
| `apply-market-data-migration.sql`, `AddMarketDataModule` migration | repo root, `Infrastructure/Migrations/` | SQL schema for candles/FVGs | **No** | We store bars as immutable Parquet, not rows in a relational DB | EF Core, SQL Server/Postgres | Different storage model |
| `FVG-VISUALIZATION-IMPLEMENTATION.md` (24 KB) | repo root | Design notes for FVG rendering | **Reference — worth reading at Phase 10** | Useful when the dashboard needs to draw FVG zones | — | — |
| Tests | `src/Tests/`, `tests/` (16 files) | Assessments + Identity handler tests | **No** | **Not one test covers FVG, RDRB or any market-data logic.** The ICT code is entirely unverified | xUnit | — |

**Assessment.** ForexQuant is the only prior project with genuine ICT code. Its FVG rule (`bullish: candle3.Low > candle1.High`; `bearish: candle3.High < candle1.Low`) matches the standard definition and independently corroborates our Phase 2 Step 4 plan. Everything around that rule — timestamps, storage, testing — we should not copy.

---

## 2. NNForTrading — `e:/Wrokspace/NNForTrading`

Python ML project. **Not a git repository** (no `.git`). Torch-based, crypto-focused.

| Component | Location | Purpose | Reusable? | Reason | Dependencies | Potential conflicts |
|---|---|---|---|---|---|---|
| `SniperProtocolLabelGenerator` | `src/data/label_generator.py` (265 LOC) | Generates entry labels from indicator + funding-rate conditions | **No — but instructive** | Labels are **rule-based entry signals**, not outcome labels. Master Plan §17 requires an outcome target (*did TP hit before SL?*). Labelling "the strategy said enter here" teaches a model to imitate a rule, not to predict the market | pandas, config dict | Wrong target semantics; funding rate is crypto-only |
| `BacktestEngine` | `src/backtesting/backtest_engine.py` (348 LOC) | Event-ish backtest: `Trade`, `BacktestMetrics`, equity curve, entry/exit, metrics | **No — structurally disqualified** | `grep -niE "spread\|commission\|slippage\|fee"` returns **zero hits** across the engine, the risk manager and `config.yaml`. A cost-free backtest overstates every edge, and §21 requires spread + commission + slippage | pandas, numpy | Would need a rewrite, not an adaptation |
| `TechnicalIndicators` | `src/data/indicators.py` (310 LOC) | EMA, MACD, RSI, ATR, Bollinger, volume/momentum/trend, support/resistance | **Maybe — Phase 4 Model B only** | Model B (§22) needs *some* technical baseline. These are conventional implementations, but `ta>=0.11` gives the same thing tested. **ATR is a genuine need** (§24 `expected_move_atr`) | pandas, `ta` | Must be re-verified for look-ahead before any use |
| `preprocessor.py` | `src/data/preprocessor.py` (307 LOC) | Feature scaling / sequence prep for NN input | **No** | Built for torch sequence models; our Phase 4 is gradient-boosted trees on tabular features | numpy, sklearn | Different model family |
| `risk_manager.py` | `src/trading/risk_manager.py` (329 LOC) | Position sizing / risk limits | **Reference only** | Phase 7 will need position sizing; worth re-reading then | — | Crypto leverage assumptions |
| `data_fetcher.py` | `src/data/data_fetcher.py` | Binance OHLCV download | **No** | Binance/crypto. We use Dukascopy FX ticks | `python-binance` | Wrong market |
| Datasets | `data/raw/BTCUSDT_{3m,4h}.csv`, `data/raw/BTCUSDT_funding.csv`, `data/processed/labeled_data.csv` (26 MB total) | BTCUSDT crypto bars + funding | **No** | **Crypto, not FX/metals.** No EURUSD or XAUUSD data exists anywhere in these projects | — | Wrong instruments entirely |
| `debug_alignment.py` | repo root | Ad-hoc multi-timeframe alignment debugging | **Reference — read it** | The existence of a dedicated MTF-alignment debug script suggests they hit alignment pain. Worth a read before Phase 3 | pandas | — |

**Assessment.** The *shape* (fetch → indicators → labels → train → backtest) is a reasonable pipeline outline and matches our Phases 1–7 loosely. The substance does not transfer: wrong asset class, wrong target definition, and a backtester that cannot produce a trustworthy number.

---

## 3. TradingBot — `e:/Wrokspace/TradingBot`

Python, Dockerised, Binance + DeepSeek LLM. Not a git repository.

| Component | Location | Purpose | Reusable? | Reason | Dependencies | Potential conflicts |
|---|---|---|---|---|---|---|
| `DeepSeekClient` | `src/deepseek_client.py` | **Asks an LLM for BUY/SELL/HOLD decisions**, parses JSON, falls back to HOLD on empty response | **No — the rejected architecture** | The LLM *is* the decision source. CLAUDE.md rule 3 and instruction §17 forbid this: an LLM must never decide "I think this is an FVG", nor emit a trade signal | DeepSeek API key | Direct violation of our core constraint |
| `prompt copy.md`, `Systemprompt.md` | repo root | Trading system prompts, incl. market-structure language | **No** | Encodes strategy as prose for an LLM to interpret — non-deterministic and untestable, the opposite of our deterministic ICT engine | — | — |
| `backtest_runner.py`, `backtest_analyzer.py`, `backtest_comparator.py` | `src/` | Backtest orchestration + comparison | **Reference only** | The **comparator** idea (compare runs side by side) is genuinely relevant to §22 ablations. Implementation is coupled to the LLM decision loop | pandas | Coupled to LLM path |
| `indicators.py` | `src/indicators.py` | Technical indicators | **No** | Duplicate of NNForTrading's, no advantage | — | — |
| `test_candle_timestamp.py` | repo root | Verifies the candle timestamp shown in AI decision logs | **Reference — the lesson, not the code** | They needed a dedicated test to pin down *which candle* a decision belonged to. That is the confirmation-timestamp problem arriving through the back door | — | — |
| `scripts/finetune_deepseek.py`, `prepare_finetuning_data.py` | `scripts/` | LLM fine-tuning for trading decisions | **No** | Doubles down on the rejected architecture | — | — |
| `docker-compose.yml`, `Makefile` | repo root | Containerisation | **No** | Ours already exists and is simpler | Docker | — |

**Assessment.** Useful as a documented example of what this project deliberately does **not** do. Its existence is why instruction §17 is worth stating explicitly.

## 3b. TradingBotV2 — `e:/Wrokspace/TradingBotV2`

Contains only `Systemprompt.md` (13 KB) and `UserPrompt.md` (2 KB). **No code, no data, no tests.** Nothing to reuse.

---

## 4. What actually transfers

| Item | From | How we use it |
|---|---|---|
| FVG 3-candle rule | ForexQuant | Independent corroboration of the Phase 2 Step 4 definition. We implement it fresh in Python, with a confirmation timestamp and tests |
| ATR | NNForTrading | Genuinely needed for §24 `expected_move_atr` and R-multiple sizing. Implement fresh, test for look-ahead |
| Technical-indicator set (EMA/RSI/MACD/Bollinger) | NNForTrading | Defines a sensible **Model B** baseline for the §22 ablation. Prefer the maintained `ta` library over copied code |
| "Backtest comparator" idea | TradingBot | The ablation harness (§22) needs exactly this: run N configurations, compare on identical out-of-sample periods |
| FVG visualisation notes | ForexQuant | Phase 10 dashboard reference |

**Nothing else.** No datasets (all crypto), no engines, no models.

---

## 5. The three lessons worth more than any of the code

### 5.1 The confirmation-timestamp bug, caught in the wild

`FvgDetectionService.cs` line 68 carries this comment:

```csharp
StartTime = candle3.Timestamp,  // 🔧 FIXED: Changed from candle2 to candle3 to exclude formation candles
```

Someone hit the look-ahead problem in production and patched it. **The patch is still wrong** — and instructively so.

A 3-candle FVG is only *knowable* once candle 3 has **closed**, because the rule tests `candle3.Low`, and a candle's low is not final until close. Stamping the event at `candle3.Timestamp` (its **open**) still claims the pattern was visible one full bar early.

Our contract requires both fields, distinctly:

```
event_timestamp        = candle3.timestamp     # where the pattern sits on the chart
confirmation_timestamp = candle3.close_time    # when it was first knowable
```

This single real-world example justifies the entire `close_time` design in [resampler.py](../../ict_kronos/data/resampler.py) and the detector contract for Phase 2. It is now recorded as the motivating case.

### 5.2 A cost-free backtest is not a backtest

NNForTrading's engine computes win rate, profit factor and equity curves with **zero** spread, commission or slippage. On 5M FX data, spread alone is a material fraction of a small edge — a cost-free backtest can turn a losing strategy into a winning chart. Master Plan §21 already requires costs; this is the concrete reason the requirement exists, and why Phase 7 must not reuse this engine.

### 5.3 "Session" is an overloaded word

ForexQuant's `SessionManagementService`, `UserSession` and `SessionSettings` are **login-session** code. A future contributor searching for "session" would find 30-minute sliding expiry and Redis caching, and conclude trading-session logic already exists. It does not — **no Asian/London/NY logic exists in any legacy project.**

Phase 2's `SessionDetector` is genuinely greenfield. Our naming should stay unambiguous (`TradingSession`, `KillZone`) so this collision cannot recur.

---

## 6. Constraints observed

- All four directories were opened **read-only**. `git status` was run in `ForexQuant` only to identify its remote; nothing was staged, committed or modified.
- `NNForTrading`, `TradingBot` and `TradingBotV2` are **not git repositories**, so any accidental change would have been unrecoverable — another reason nothing was touched.
- No code was copied into ICT-Kronos. Where a concept transfers, it will be **re-implemented with tests**, per CLAUDE.md rule 12.
- `NNForTrading/.env` and `TradingBot/.env` exist and are likely to contain live API keys. They were **not read**, and must never be copied into this repository.
