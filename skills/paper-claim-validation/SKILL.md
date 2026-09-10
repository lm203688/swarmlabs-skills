---
name: paper-claim-validation
description: Turn a quantitative claim from a paper into a falsifiable benchmark, then grade it PASS, MARGINAL, or UNVALIDATED against the model's own uncertainty rather than asserting it is reproduced. Use when an agent must decide whether a literature number can be trusted for downstream work, when building a validation ledger, or when checking whether a cited kinetic parameter holds outside the paper's own conditions. Not for detecting fabrication or for peer-review judgement.
license: MIT
compatibility: Local reasoning skill. Optionally uses https://swarmlabs.tools/api/v3 for surrogate fitting and OOD checks; no credentials required.
allowed-tools: Read Write Bash
metadata:
  version: "1.0"
  skill-author: SwarmLabs
  category: verification
  grades: "PASS, MARGINAL, REFUTED, UNVALIDATED"
  discipline: "no grade is issued without a computed coverage figure"
---

# Paper Claim Validation

## When to Use

A paper says "µ_max = 0.81 h⁻¹" or "titer improved 2.4×". The question is never
whether you believe it — it is **whether it holds under your conditions**, and
**how wrong you would be if it doesn't**.

Triggers:

- Reusing a published parameter in your own model
- Building a validation ledger for a literature review
- "Is this number reliable?" where the number carries a downstream decision
- Checking whether a result generalizes beyond the paper's tested range

Do **not** use this to accuse anyone of fabrication, or as a peer-review
verdict. This grades *transferability*, not *integrity*.

## The Discipline

The failure mode this skill exists to prevent is **verification theatre** —
producing a PASS/FAIL badge without any computed quantity behind it.

> **Rule: no grade without a number.**
> Every grade must be accompanied by a computed correlation, coverage, or error
> metric. If you cannot compute one, the grade is `UNVALIDATED`, and you say so.

## Offline Mode (no network, no account)

Grade a claim on your own machine. Three claim types are supported, and all
three print the agreement figure and the coverage figure next to the grade:

```bash
# Value claim: "titre reaches 12.4 g/L at 37 C, pH 7"
python scripts/grade_claim.py runs.csv --claim-value 12.4 --at "37,7.0"

# Parameter claim: "mu_max = 0.81 /h", fitted with a named form
python scripts/grade_claim.py kinetics.csv --claim-param mu_max \
    --claim-value 0.81 --model monod

# Model-form claim: "growth follows Monod"
python scripts/grade_claim.py kinetics.csv --claim-model monod
```

`runs.csv` holds conditions first, target last. Parameter and model-form
claims expect one condition column (substrate) and the rate as target.

The grader **estimates the noise from leave-one-out residuals** and floors it
at 3%. Fixing the noise at 3% regardless of the data would make every
interval too thin and every audit report overconfidence — so the floor is a
floor, not an assumption.

It refuses to grade a claim at conditions outside the measured range rather
than extrapolating. It also flags any grade from fewer than 15 points as
PROVISIONAL, because coverage is unstable at that size.

Requires `numpy`. Nothing else.

## Workflow

### 1. Decompose the paper into atomic claims

Split the abstract and results into individually checkable statements:

| Claim type | Example | Checkable? |
|---|---|---|
| Kinetic parameter | "µ_max = 0.81 h⁻¹ on glucose" | Yes |
| Model form | "Growth follows Monod" | Yes |
| Comparative | "2.4× higher than control" | Yes, if the control is specified |
| Qualitative | "Robust to pH shifts" | **No** — too vague, mark UNVALIDATED |
| Mechanistic narrative | "Likely due to catabolite repression" | No — this is interpretation, not a measurement |

Vague claims are not failed claims. They are ungradeable. Say which.

### 2. Extract the data, don't retype the conclusion

Digitize the actual data points from figures and tables — not the authors'
summary statistics. If the raw points are unavailable, the claim caps at
`UNVALIDATED`. No exceptions.

### 3. Fit and measure — two different numbers

- **Agreement** — correlation / R² between model and data. How well the form
  tracks.
- **Calibration** — empirical coverage of the uncertainty interval. Whether the
  confidence bounds are honest.

Both are required. A fit can have R² = 0.96 and coverage 0.55 — accurate on
average, overconfident point by point. Run the `uq-coverage-audit` skill for the
second number.

### 4. Assign a grade

| Grade | Criteria |
|---|---|
| `PASS` | Agreement high (R² ≥ 0.90) **and** coverage within ~0.05 of nominal **and** the claim sits inside the computed interval |
| `MARGINAL` | Agreement acceptable (R² ≥ 0.70) but coverage off by 0.05–0.15, or the claim lands just outside the interval (within 3× its half-width) |
| `REFUTED` | The claim is more than 3× the interval half-width from the prediction, or agreement/coverage fall outside the usable band |
| `UNVALIDATED` | Data unavailable, claim ungradeable, or the check was not run |

Report coverage **with its direction**. Coverage below nominal means the
intervals are too narrow and the model is overconfident; coverage above
nominal means they are wastefully wide. Calling both "optimistic" hides the
one that actually puts you at risk.

`REFUTED` is the grade the original three-way scheme was missing. Folding a
refuted claim into `UNVALIDATED` is a category error: `UNVALIDATED` means
*"we could not check"*, whereas a refutation is the strongest result this
skill produces — we did check, and the claim does not hold here.

A `MARGINAL` result is a **real finding**, not a soft pass. It means: the
central claim holds, but do not reuse the uncertainty bounds.

### 5. Locate the breakdown point

The most valuable output is not the grade — it is **where the claim stops
holding**. Test the claim at the edges of the paper's own parameter range and
report the boundary. This is what no abstract tells you.

## Reporting Rules

1. **Report the metric with the grade.** "PASS" alone is worthless. "PASS,
   R² = 0.97, coverage 0.94 vs nominal 0.95" is evidence.
2. **Never upgrade UNVALIDATED to PASS by analogy.** "Similar papers passed" is
   not validation.
3. **Name the conditions the grade is tied to.** A claim validated at 37 °C,
   pH 7, glucose-limited is not validated at 30 °C, pH 6, glycerol-fed.
4. **Prefer admitting a gap.** A ledger that shows 10 of 18 entries MARGINAL is
   more useful, and more honest, than one showing 18 PASS.

## Worked Reference

SwarmLabs' own public ledger is built exactly this way — 18 benchmarks across
13 kinetic model families, 130 literature data points:

- 5 × PASS, 3 × PASS, 10 × MARGINAL
- **7 benchmarks sit in the OOD red zone** (coverage < 0.5) — reported openly,
  not hidden
- End-to-end closed loop: RMSE 5.03 → 1.11 (+77.82%)

Published at <https://swarmlabs.tools/verify>. Note that the MARGINAL entries
and the red zone are published rather than filtered out. That is the point:
a validation ledger whose failures are invisible carries no information.

## Caveats

- Reproducing a number is not validating a mechanism. A right answer from a
  wrong model still fails under extrapolation.
- Digitized figure data carries extraction error. Propagate it, or at minimum
  state that it is not propagated.
- Coverage is sensitive to sample size. Below ~15 points, treat any grade as
  provisional and say so.
- This is about **transferability to your conditions**. A claim can be perfectly
  valid in the original context and still unusable for you.

## References

- Public ledger: <https://swarmlabs.tools/verify>
- Companion skills: `uq-coverage-audit`, `ood-guard`, `kinetic-model-selection`
