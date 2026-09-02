---
name: uq-coverage-audit
description: Measure whether a predictive model's uncertainty intervals actually contain the truth, using leave-one-out empirical coverage. Returns a PASS / MARGINAL / OVERCONFIDENT verdict with the numeric gap between claimed and observed coverage. Use when an agent must decide if a prediction interval is trustworthy, when comparing models on calibration rather than accuracy, when a surrogate reports 95% intervals and someone wants to act on them, or when auditing overconfident forecasts before publication. Pure numpy, runs offline. Not for point-estimate accuracy scoring, and not a substitute for out-of-distribution checks.
license: MIT
compatibility: Local execution, Python 3.9+ with numpy only. No network, no credentials, no API key.
allowed-tools: Read Bash
metadata:
  version: "1.0"
  skill-author: SwarmLabs
  category: uncertainty-quantification
  method: "leave-one-out GP coverage with normalized noise floor"
  target-coverage: "0.95"
---

# Uncertainty Coverage Audit

## When to Use

A model reporting "95% confidence" is making a falsifiable claim: **if you
repeated this experiment 100 times, the truth should land inside the interval
about 95 times.** Almost nobody checks. This skill checks.

Triggers:

- "Can I trust this prediction interval?"
- "Is the model overconfident?"
- Comparing two models where accuracy is similar but calibration may differ
- Before quoting an interval to anyone who will make a decision on it
- After adding data — did calibration improve, or only accuracy?

Do **not** use this for point-estimate accuracy. Use MAE/RMSE for that. This
skill answers a different question: *when the model is uncertain, is it
uncertain by the right amount?*

## The Core Idea

| | Meaning |
|---|---|
| **Nominal coverage** | What the interval claims (usually 0.95) |
| **Empirical coverage** | Fraction of held-out points that actually fell inside |
| **Gap** | Empirical − nominal. Negative means overconfident. |

A model with a −0.40 gap claims 95% but delivers 55%. Its intervals are
decoration. That is a serious finding and should be reported as such.

## Workflow

### 1. Prepare a CSV

One row per observation, last column is the target, all preceding columns are
inputs. A header row is optional.

```csv
temperature,ph,substrate,titer
30,6.5,2.0,1.42
33,7.0,4.0,2.88
37,7.0,6.0,4.10
```

Minimum 6 rows — leave-one-out needs enough neighbours per fold to fit a
length scale. With fewer, the script refuses rather than emitting a meaningless
number.

### 2. Run the audit

```bash
python scripts/uq_audit.py data.csv
```

Useful flags:

```bash
python scripts/uq_audit.py data.csv --nominal 0.90 --noise-floor 0.03
python scripts/uq_audit.py data.csv --json      # machine-readable
```

### 3. Read the verdict

| Verdict | Gap | What to do |
|---|---|---|
| `PASS` | ≥ −0.05 | Quote the intervals as stated |
| `MARGINAL` | −0.05 to −0.15 | Usable, but widen bounds or collect more data before deciding |
| `OVERCONFIDENT` | < −0.15 | **Do not quote these bounds.** Collect data, re-check the noise floor, or change model form |

Exit code is `1` on `OVERCONFIDENT`, so it can gate a pipeline directly.

## Reporting Rules

1. **Always report the gap, not just the verdict.** "Coverage 0.71 vs nominal
   0.95" is a finding. "It failed" is not.
2. **Never suppress an OVERCONFIDENT result.** An agent that quietly drops
   calibration failures destroys the entire value of quantifying uncertainty.
3. **Distinguish the two failure modes:**
   - Coverage too **low** → intervals too narrow → overconfident.
   - Coverage too **high** (e.g. 1.00) → intervals too wide → the model is
     uselessly cautious. Mention this; it is the opposite failure but still a
     failure.
4. **Accuracy and calibration are independent.** A model can have excellent
   RMSE and terrible coverage. Always run both.

## Caveats

- **The 3% noise floor is intentional, not a bug.** It prevents the posterior
  variance from collapsing to zero between data points. Lowering it makes
  intervals prettier and coverage worse. Leave it at 0.03.
- Leave-one-out is optimistic on very small samples (n < 10). Treat PASS at
  n = 8 as weak evidence.
- This measures **interpolation** calibration. It says nothing about
  extrapolation — for that, run an out-of-distribution check separately.
- The default kernel is RBF with a median-heuristic length scale. Strongly
  non-stationary data may need a different surrogate; the coverage number is
  only as meaningful as the model it audits.

## References

- SwarmLabs publishes its own coverage numbers, including the 7 benchmarks
  currently in the red zone: <https://swarmlabs.tools/verify>
- Script: `scripts/uq_audit.py`
