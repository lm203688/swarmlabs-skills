---
name: kinetic-model-selection
description: Fit multiple published kinetic models to the same experimental data and compare them by R-squared, returning the best-supported form with parameters rather than assuming one. Covers Monod, Andrews, Haldane, Contois, Pirt, Luedeking-Piret, Tessier, Baranyi and growth/product/death kinetics. Use when deciding which rate law describes a dataset, when a fitted parameter will be reused downstream, or when checking whether reported kinetics are actually supported by the data. Not for fitting arbitrary user-defined equations.
license: MIT
compatibility: Requires network access to https://swarmlabs.tools/api/v3/fit. Read-only, no credentials.
allowed-tools: Read Bash
metadata:
  version: "1.0"
  skill-author: SwarmLabs
  category: model-fitting
  model-families: "Monod, Andrews, Haldane, Contois, Tessier, Baranyi, logistic, Pirt, Luedeking-Piret"
  goodness-of-fit: "R2 with 0.95 / 0.85 thresholds"
---

# Kinetic Model Selection

## When to Use

The most common quiet error in bioprocess analysis is **assuming a kinetic form
before looking at the data**. Monod is the default everyone reaches for; it is
wrong whenever substrate inhibition, maintenance metabolism, or a lag phase is
present.

Triggers:

- "What kinetics does my data follow?"
- Extracting µ_max or Ks for a downstream model
- A paper reports Monod parameters and someone wants to reuse them
- Growth curves that peak and decline, or show a lag

## Workflow

### 1. Fit several candidates at once

```bash
curl -s -X POST "https://swarmlabs.tools/api/v3/fit" \
  -H "Content-Type: application/json" \
  -d '{
    "data": [[0,0.10],[2,0.21],[4,0.48],[6,0.79],[8,0.90],[10,0.93]],
    "models": ["logistic", "monod"]
  }'
```

> **Payload contract (verified 2026-09-02).** The field is `data`, a list of
> `[time, OD600]` pairs — **not** separate `x` / `y` arrays. At least 4 points.
> `models` is optional; omit it to fit the two models the endpoint currently
> supports: `logistic` and `monod`. **The endpoint silently ignores any other
> name** (e.g. `baranyi`, `andrews`, `haldane`) — it does not error, it just
> omits them from `results`. The broader model library (Andrews, Haldane,
> Contois, Baranyi, Tessier, Pirt, …) is covered by the 52 microbiology
> scenarios' ground-truth evaluators and the local Python engine, not by `/fit`.
> Sending `{"x": ..., "y": ...}` returns
> `{"error": "Need >= 4 data points [[time, OD600]]"}`.

Verified response:

```json
{
  "n_data": 5,
  "results": [
    {"model": "logistic", "r2": 0.9958, "params": {"k": 0.65, "L": 0.990, "x0": 4}},
    {"model": "monod",    "r2": 0.8000, "params": {"mu_max": 0.35, "Ks": 0.651, "X0": 0.1, "S0": 3.20}}
  ],
  "best_model": "logistic",
  "note": "R2>0.95 excellent; R2>0.85 usable; R2<0.85 add more data"
}
```

### 2. Read R² against the thresholds

| R² | Interpretation |
|----|----------------|
| > 0.95 | Well supported — carry the parameters forward |
| 0.85 – 0.95 | Usable — report with the residual gap noted |
| < 0.85 | Do not conclude — more data, or the form is wrong |

### 3. Prefer the simpler model when R² ties

If Monod (2 params) and Baranyi (4 params) both reach 0.97, the extra parameters
bought nothing. Report the simpler one. A higher R² from a more flexible model
is expected, not evidence.

### 4. Sanity-check the parameters against literature

A fitted µ_max of 12 h⁻¹ for *E. coli* is not a discovery — it is a bad fit or
a units error. Cross-check against the strain database:

```bash
curl -s "https://swarmlabs.tools/api/v3/strain/ecoli_K12_MG1655"
```

## Common Pitfalls

| Symptom | Likely cause |
|---|---|
| Monod fits poorly at high substrate | Substrate inhibition — try Andrews or Haldane |
| Systematic lag not captured | Missing lag phase — try Baranyi |
| Growth continues after substrate exhaustion | Maintenance / endogenous metabolism — Pirt term |
| Product and growth decoupled | Non-growth-associated product — Luedeking-Piret |
| Excellent R², implausible parameters | Over-parameterized fit. Constrain or simplify |

## Caveats

- **R² compares fits on the same data only.** Never compare R² across datasets
  with different ranges.
- Fitted parameters are conditional on the fitting window. Truncating a curve
  before stationary phase changes µ_max materially.
- Substrate is often not measured directly. Fitting Ks against nominal feed
  concentration rather than residual substrate inflates it.
- This endpoint fits **time-series** data (`time, OD600`). Steady-state rate vs
  substrate curves need the surrogate workflow instead.

## References

- Live endpoint: `https://swarmlabs.tools/api/v3/fit`
- Scenario catalog with published formulas and citations:
  `https://swarmlabs.tools/api/v3/scenarios`
