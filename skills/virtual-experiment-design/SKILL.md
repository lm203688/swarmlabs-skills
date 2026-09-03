---
name: virtual-experiment-design
description: Design virtual experiments with Gaussian-process surrogate models and built-in uncertainty quantification. Returns optimal next-run conditions with 95% credible intervals and out-of-distribution risk flags, rather than textual DOE guidance. Use when planning fermentation, cell culture, or chemistry experiments; optimizing bioprocess parameters (temperature, pH, substrate, inducer, feeding rate); choosing which experiment to run next under a limited budget; or requesting design of experiments (DOE), active learning, Bayesian optimization, or fitting published kinetic models (Monod, Andrews, Haldane, Contois, Pirt, Luedeking-Piret, Tessier, Baranyi). Backed by 63 literature-referenced scenarios. Not for wet-lab protocol execution or physical equipment control.
license: MIT
compatibility: Requires network access to https://swarmlabs.tools/api/v3. Python 3.9+ with requests, or any HTTP client. Read-only, no credentials needed for /scenarios, /predict, /fit, /guard.
allowed-tools: Read Write Bash
metadata:
  version: "1.0"
  skill-author: SwarmLabs
  category: experimental-design
  engine: "GP surrogate (RBF + ARD length scales + Cholesky)"
  uq: "posterior variance, noise floor normalized to 3%, target coverage 0.95"
---

# Virtual Experiment Design

## When to Use

Use this skill when a user needs to **decide what experiment to run next**, or to **predict an outcome with a confidence bound** before spending bench time.

Trigger situations:

- "Which conditions should I try next?" under a limited experiment budget
- Optimizing bioprocess parameters: temperature, pH, substrate concentration, inducer, feeding rate, dissolved oxygen
- Fitting experimental data against a published kinetic model
- Checking whether a proposed condition is inside or outside the model's reliable region
- Comparing candidate conditions and wanting a ranked list, not a narrative

Do **not** use for wet-lab protocol execution, pipetting steps, or equipment control — this skill computes, it does not operate hardware.

## What Makes This Different

Most experimental-design guidance tells an agent *what method to consider*. This skill **returns computed results**:

| Conventional skill output | This skill's output |
|---------------------------|---------------------|
| "Consider a response surface design" | 3 ranked conditions, each with predicted value and 95% interval |
| "You may want more replicates" | Current model uncertainty = 0.042; adding 2 points at X reduces it to 0.018 |
| "Be careful extrapolating" | Explicit `pass` / `controlled` / `reject` guard verdict with distance metric |
| "Fit a Monod model" | Fitted parameters with R², plus 3 alternative models compared |

The engine is a Gaussian-process surrogate with uncertainty quantification as a first-class output — **not** an afterthought.

## Workflow

### 1. Pick a scenario

```bash
curl -s "https://swarmlabs.tools/api/v3/scenarios" | jq '.scenarios[].key'
```

Returns 11 cross-domain benchmarks (`surrogate_forrester`, `surrogate_branin`, `pde_heat1d`, `bio_logistic`, `chem_langmuir`, `llm_scaling`, ...).

For microbiology scenarios, see `references/microbiology-scenarios.md` — 52 scenarios covering Monod, Andrews, Contois, Haldane, Pirt, Luedeking-Piret, Tessier, Baranyi, fed-batch, chemostat, Thiele, antibiotic kill, and thermal death kinetics.

Fetch metadata (bounds, formula, literature):

```bash
curl -s "https://swarmlabs.tools/api/v3/scenarios" \
  | jq '.scenarios[] | select(.key=="microbio_monod")'
```

### 2. Predict with uncertainty

```bash
curl -s -X POST "https://swarmlabs.tools/api/v3/predict" \
  -H "Content-Type: application/json" \
  -d '{
    "scenario": "microbio_monod",
    "train_x": [[0.1],[0.5],[1.0],[2.0],[5.0]],
    "train_y": [0.15, 0.42, 0.58, 0.71, 0.79],
    "query_x": [[0.3],[1.5],[3.0]]
  }'
```

Response gives `mean`, `std` (posterior uncertainty), and `dist` (distance to training data).

### 3. Check the guard before trusting a prediction

Always run the guard for conditions far from training data:

```bash
curl -s -X POST "https://swarmlabs.tools/api/v3/guard" \
  -H "Content-Type: application/json" \
  -d '{
    "scenario": "microbio_monod",
    "train_x": [[0.1],[0.5],[1.0],[2.0],[5.0]],
    "train_y": [0.15, 0.42, 0.58, 0.71, 0.79],
    "query_x": [[0.3],[1.5],[3.0]]
  }'
```

Verdicts:

| Verdict | Meaning | Action |
|---------|---------|--------|
| `pass` | Inside reliable region | Trust the prediction |
| `controlled` | Near the boundary | Usable, but flag the wider interval; prefer adding a training point here |
| `reject` | Out of distribution | Do not trust; run a real experiment to extend the model |

**Never present a `reject` prediction as fact to the user.** Say the model cannot answer and explain what data would fix it.

### 4. Fit a kinetic model (optional)

```bash
curl -s -X POST "https://swarmlabs.tools/api/v3/fit" \
  -H "Content-Type: application/json" \
  -d '{
    "data": [[0,0.10],[2,0.21],[4,0.48],[6,0.79],[8,0.90],[10,0.93]],
    "models": ["logistic", "monod"]
  }'
```

> **Payload contract (verified against live API 2026-09-02).** The `/fit` endpoint
> currently fits only `logistic` and `monod` — any other model name in `models`
> is silently ignored (it does not error, it just omits it from `results`). The
> wider model library (Andrews, Haldane, Contois, Baranyi, …) lives in the 52
> microbiology scenarios' ground-truth evaluators, not in `/fit`. The field is `data`,
> a list of `[time, OD600]` pairs — **not** separate `x` / `y` arrays. Minimum 4
> points. `models` is optional; omit it to fit both supported models. Passing
> `{"x": [...], "y": [...]}` returns
> `{"error": "Need >= 4 data points [[time, OD600]]"}`.

Response: `results[]` with per-model `r2` and fitted `params`, plus `best_model`
and a calibration `note`:

| R² | Interpretation |
|----|----------------|
| > 0.95 | Excellent — the kinetic form is well supported |
| 0.85 – 0.95 | Usable — report it, but flag the residual gap |
| < 0.85 | Do not conclude — collect more points, or the form is wrong |

Use this to show the user **which kinetic form actually fits their data** rather
than handing them the one they assumed.

### 5. Recommend next runs

Rank candidate conditions by predicted value and uncertainty. Two valid strategies — state which one you are using:

- **Exploit**: highest predicted mean. Use when the goal is to hit a target.
- **Explore**: highest posterior `std`. Use when the goal is to improve the model.

A balanced next-batch suggestion: take the top 2 by mean and top 1 by std.

## Examples

### Example 1 — "What substrate concentration should I test next?"

User has 5 data points for E. coli growth rate vs glucose, wants the next run.

1. `GET /scenarios` → confirm `microbio_monod`, bounds `[0.001, 10.0]`
2. `POST /predict` over a grid of 50 candidate concentrations
3. `POST /guard` on the top 10 candidates
4. Report: top 3 `pass` conditions with mean ± 1.96·std, and note any `controlled` candidates worth a real run

### Example 2 — "Does my data follow Monod or Andrews?"

Andrews adds substrate inhibition — growth drops at high substrate. If the user's data peaks then declines, Monod will fit poorly.

1. `POST /fit` with `["monod"]` to get a Monod R² baseline (the endpoint only fits `logistic`+`monod`).
2. For the Andrews/Haldane comparison, do **not** use `/fit` (it ignores those names). Instead compare the user's data against the microbiology scenarios' ground-truth evaluators: `POST /predict` on `micro_mle_ecoli_monod` and `micro_haldane_putida` (or `/sample` them with the user's substrate range), then judge which ground truth reproduces the user's curve.
3. Report the winner with R² and, if Andrews/Haldane wins, name the estimated inhibition constant from the scenario's published formula.

### Example 3 — "Can I trust a prediction at 8 g/L when I only tested up to 2?"

1. `POST /guard` with the query point
2. Expect `reject` or `controlled` (distance too large)
3. Tell the user: no — and specify which intermediate concentration would most improve the model

## Caveats

- Predictions are only as good as the training data. Fewer than ~5 points per dimension gives wide intervals — say so.
- The 3% noise floor is deliberate. It prevents overconfident intervals; do not present it as a flaw to be tuned away.
- Published parameters in scenario metadata are literature values, not fits to the user's strain. Treat them as priors, not ground truth.
- This is in-silico. A virtual result suggests where to look; it does not replace a confirming bench experiment.

## References

- Scenario catalog with formulas and literature: `references/microbiology-scenarios.md`
- Ready-to-run workflow: `scripts/design_next_experiment.py`
- Live API: `https://swarmlabs.tools/api/v3/`
