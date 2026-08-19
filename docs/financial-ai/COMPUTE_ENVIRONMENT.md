# COMPUTE_ENVIRONMENT

**Date:** 2026-08-19
**Purpose:** Record the hardware and software the research platform actually runs on, so Phase 5 (Kronos) is planned against measured facts rather than assumptions.

**Headline: no CUDA-capable GPU is present. This machine is CPU-only.**
Per the standing instruction, this does **not** block Phase 2 — ICT development is entirely CPU work. Kronos experimentation is deferred until a suitable execution environment exists.

---

## 1. Hardware

| Property | Value |
|---|---|
| OS | Windows 11 Pro, build 10.0.26200 |
| Architecture | AMD64 |
| CPU | Intel Core i7-8550U @ 1.80 GHz (base), 1.99 GHz max reported |
| Cores / threads | 4 physical / 8 logical |
| RAM | 31.9 GB |
| Free disk (E:) | 1.6 TB free of 1.9 TB |

The i7-8550U is a 2017 15 W ultra-low-voltage laptop CPU. It is entirely adequate for deterministic ICT feature engineering and gradient-boosted trees on tabular data, and it is **not** adequate for transformer inference at research scale.

## 2. GPU

| Adapter | Driver | Reported VRAM | CUDA-capable |
|---|---|---|---|
| AMD Radeon R7 M460 | 27.20.1034.6 | 4.29 GB (as reported by WMI) | **No** |
| Intel UHD Graphics 620 | 27.20.100.9664 | 1.07 GB (shared) | **No** |

- `nvidia-smi` — **not found**. No NVIDIA driver, therefore no NVIDIA GPU.
- `nvcc` — **not found**. No CUDA toolkit installed.
- Neither adapter supports CUDA. CUDA is NVIDIA-only, and Kronos (like essentially all PyTorch foundation models) targets CUDA in practice.

**A note on the AMD card.** The Radeon R7 M460 is discrete, so "there is a dedicated GPU" is technically true and practically irrelevant here:

- ROCm — AMD's CUDA equivalent — **does not support Windows** for PyTorch, and does not support this GPU generation regardless.
- DirectML is a theoretical Windows path for PyTorch on AMD, but it is not a supported Kronos backend, and validating a foundation model through an unsupported backend would make any result unattributable.

Treating this machine as GPU-capable would produce numbers nobody could trust. It is CPU-only.

## 3. Software

| Component | Status |
|---|---|
| Python (project venv) | 3.14.0 |
| Python (declared floor) | 3.12 — CI runs 3.12 and 3.13 |
| numpy | 2.5.2 |
| pandas | 3.0.5 |
| pyarrow | 25.0.1 |
| requests | 2.34.2 (installed for the Dukascopy backfill) |
| **torch (project venv)** | **not installed — deliberate.** The `[kronos]` extra is opt-in so CI and dev stay lightweight (CLAUDE.md rule 9) |
| torch (system Python, unrelated) | 2.9.0**+cpu** — a CPU-only build, present from an unrelated project |

The stray system-wide `torch 2.9.0+cpu` is worth naming explicitly: its `+cpu` suffix means it was built without CUDA. Even if a CUDA GPU appeared tomorrow, that wheel could not use it. It is not on this project's dependency path and should not be mistaken for GPU readiness.

## 4. What this means for Kronos (Phase 5)

Kronos is a transformer over K-line sequences. Rough expectations for a `Kronos-small`-class model on this CPU:

- **Single forecast:** likely seconds per call. Tolerable for a demo.
- **Research-scale batch forecasting:** the hybrid experiment (§16/§22) needs a forecast at *every* decision point across multiple years, symbols and walk-forward folds — plausibly hundreds of thousands of forecasts. At seconds each on 4 cores this is days-to-weeks of wall clock, which is not a viable research loop.

**Consequences, recorded now so Phase 5 is not planned on wishful thinking:**

1. **Phase 5 is deferred until an execution environment exists.** Options, in rough order of practicality: a rented CUDA instance for batch forecast generation; a smaller checkpoint (`Kronos-mini`) if it proves adequate; or accepting a materially reduced experiment scope on CPU and saying so in the results.
2. **Forecasts must be generated in batch and cached, never computed inline.** The `AiResponseCache` pattern noted in the Phase 0 analysis applies directly: key a stored forecast on `(symbol, timeframe, context_hash, sampling_params, model_version)`. This also makes forecasts reproducible, which §28 requires anyway.
3. **The forecast job lane must be restartable**, for the same reason the Dukascopy backfill is: long jobs on modest hardware get interrupted.
4. **Phases 2, 3, 4, 6 (fusion mechanics), 7 and 8 are unaffected.** Deterministic ICT detection, feature building, XGBoost/LightGBM, backtesting and walk-forward are all comfortable CPU workloads at this data scale. 32 GB RAM is generous for tabular research.

**Nothing about the research question is blocked.** Models A (OHLCV), B (OHLCV + technical) and C (OHLCV + ICT) — which together answer "does ICT add incremental information over simpler baselines?" — need no GPU at all. Only D and E, the Kronos arms, need one.

## 5. Heavy dependencies deliberately not installed

Per the standing instruction not to install heavy GPU dependencies during this phase, and CLAUDE.md rule 9:

- `torch`, `huggingface-hub` (`[kronos]`) — **not installed**
- `scikit-learn`, `xgboost`, `lightgbm`, `scipy` (`[ml]`) — **not installed** (Phase 4)
- `requests` (`[dukascopy]`) — **installed**, because the real-data proof genuinely required it

The default test gate installs only `[test]` and touches no network.

## 6. Reproducing these findings

```bash
nvidia-smi                       # expect: command not found
nvcc --version                   # expect: command not found

powershell -Command "Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM, DriverVersion"
powershell -Command "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB"
powershell -Command "Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors"

./.venv/Scripts/python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available())"
# expect: ModuleNotFoundError — the [kronos] extra is intentionally not installed
```

## 7. Verdict

| Question | Answer |
|---|---|
| NVIDIA GPU available? | **No** |
| CUDA available? | **No** |
| PyTorch available in the project env? | **No** — intentionally, `[kronos]` is opt-in |
| GPU memory available for inference? | **None usable** |
| Does this block Phase 2? | **No** — ICT development is CPU work |
| Does this block Phase 5 (Kronos)? | **Yes, at research scale.** Deferred pending a CUDA environment |
