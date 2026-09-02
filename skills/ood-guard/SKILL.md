---
name: ood-guard
description: Classify whether a query condition falls inside the region a model was actually trained on, returning pass / controlled / reject per point rather than a vague extrapolation warning. Use before trusting any prediction or surrogate output on conditions outside the training range, when estimating how far a proposed experiment is from existing data, or when deciding whether a model can answer at all or should refuse. Backed by a live distance-aware guard endpoint over 63 literature-referenced scenarios. Not for wet-lab safety review and not a replacement for a physical risk assessment.
license: MIT
compatibility: Requires network access to https://swarmlabs.tools/api/v3/guard. Read-only, no credentials. Also works offline via scripts/guard_local.py using numpy.
allowed-tools: Read Bash
metadata:
  version: "1.0"
  skill-author: SwarmLabs
  category: model-reliability
  verdicts: "pass, controlled, reject"
  engine: "distance-to-training-data gate with GP posterior inflation"
---

# Out-of-Distribution Guard

## When to Use

Every predictive model is a locally valid approximation. The question is never
"is this model good?" — it is **"is this model valid *here*?"**

Triggers:

- Someone proposes a condition outside the tested range ("can we predict at
  8 g/L when we only measured up to 2?")
- Before reporting any surrogate prediction as a result
- Ranking candidate conditions where some are unexplored territory
- Reviewing a colleague's or a paper's extrapolation

Do **not** use this for laboratory safety clearance. It judges *model
reliability*, not *physical hazard*.

## The Three Verdicts

| Verdict | Meaning | Obligation |
|---|---|---|
| `pass` | Inside the reliable region | Report the prediction normally |
| `controlled` | Near the boundary | Usable, but state the wider interval and prefer adding a data point |
| `reject` | Out of distribution | **Do not present as a result.** Say the model cannot answer here |

The hard rule:

> **A `reject` verdict is a refusal, not a low-confidence answer.**
> Never convert it into "the model predicts roughly X, but be careful."
> That conversion is how bad numbers get into papers.

## Workflow

### 1. Query the guard

```bash
curl -s -X POST "https://swarmlabs.tools/api/v3/guard" \
  -H "Content-Type: application/json" \
  -d '{
    "scenario": "microbio_monod",
    "train_x": [[0.1],[0.5],[1.0],[2.0],[5.0]],
    "train_y": [0.15, 0.42, 0.58, 0.71, 0.79],
    "query_x": [[0.3],[1.5],[30.0]]
  }'
```

Verified live response (2026-09-02):

```json
{
  "scenario": "microbio_monod",
  "n": 3,
  "counts": {"pass": 2, "controlled": 0, "reject": 1},
  "trusted_ratio": 0.6666666666666666,
  "mean": [0.2867, 0.6892, 0.53],
  "std":  [0.00720, 0.00720, 0.2276],
  "status": ["pass", "pass", "reject"]
}
```

Note the shape: `status` is **per query point, positionally aligned** with
`query_x`. The far point (30.0) is rejected, and its posterior `std` inflates
from 0.0072 to 0.228 — a 31× widening. That inflation is the guard doing its
job, not a bug.

### 2. Interpret `trusted_ratio`

The fraction of queried points that came back `pass`. Useful as a gate:

- `1.0` — proceed
- `0.5`–`1.0` — proceed on the `pass` subset only, name the excluded ones
- `< 0.5` — the proposed design is mostly unexplored; recommend a bridging
  experiment before trusting anything

### 3. Recommend the bridging point

When points are rejected, the useful answer is **what data would fix it**. Pick
a condition roughly midway between the nearest training point and the rejected
query. Adding one point there usually converts `reject` to `controlled`.

### 4. Offline fallback

No network, or data not covered by a named scenario:

```bash
python scripts/guard_local.py data.csv --query "37,7.0,6.0"
```

Implements the same logic — nearest-neighbour distance against the training
hull, expressed in standardized units — with numpy only.

## Reporting Rules

1. **Name the verdict explicitly.** "The guard rejected this condition" is
   information. "The model is uncertain" is not.
2. **Report the distance, not just the label.** Saying *how far* outside tells
   the user whether one bridging run is enough.
3. **Never average across verdicts.** Mixing `pass` and `reject` points into a
   single mean hides the boundary. Report the subsets separately.
4. **Reject is a legitimate answer.** An agent that refuses to extrapolate is
   more useful than one that extrapolates quietly.

## Caveats

- The guard measures distance in **input space**. A query can be close in
  input space yet behave differently if the system has a regime change (e.g.
  oxygen limitation onset). Domain knowledge still rules.
- It reflects coverage of *your* training data, not absolute truth. Dense but
  biased data yields confident wrong answers.
- `controlled` is the verdict most often misread. It does not mean "probably
  fine" — it means "usable with an explicitly wider interval and a note".

## References

- Live guard endpoint: `https://swarmlabs.tools/api/v3/guard`
- Published coverage and OOD red-zone figures:
  <https://swarmlabs.tools/verify>
- Offline implementation: `scripts/guard_local.py`
